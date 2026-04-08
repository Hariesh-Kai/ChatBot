from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from backend.memory.pg_memory import get_connection
from backend.state.job_state import get_job_state
from backend.storage.minio_client import (
    delete_pdf as minio_delete_pdf,
    upload_pdf as minio_upload_pdf,
)

_TABLE = "minio_upload_outbox"
_UPLOAD_ROOT = (Path(__file__).resolve().parent / "uploads").resolve()

_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()
_WAKE_EVENT = threading.Event()
_WORKER_THREAD: Optional[threading.Thread] = None


def init_outbox_table() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    id BIGSERIAL PRIMARY KEY,
                    job_id TEXT,
                    session_id TEXT,
                    company_document_id TEXT NOT NULL,
                    revision_number TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_error TEXT,
                    minio_path TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (company_document_id, revision_number, source_file)
                );
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{_TABLE}_status_retry
                ON {_TABLE} (status, next_retry_at);
                """
            )


def _retry_delay_sec(attempt_number: int) -> int:
    base = max(1, int(os.getenv("MINIO_OUTBOX_RETRY_BASE_SEC", "5")))
    cap = max(base, int(os.getenv("MINIO_OUTBOX_RETRY_CAP_SEC", "300")))
    n = max(1, int(attempt_number))
    delay = base * (2 ** (n - 1))
    return min(cap, delay)


def enqueue_minio_upload(
    *,
    job_id: str,
    session_id: Optional[str],
    company_document_id: str,
    revision_number: str,
    source_file: str,
    local_path: str,
) -> int:
    init_outbox_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_TABLE} (
                    job_id,
                    session_id,
                    company_document_id,
                    revision_number,
                    source_file,
                    local_path,
                    status,
                    attempt_count,
                    next_retry_at,
                    last_error,
                    minio_path,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, NOW(), NULL, NULL, NOW())
                ON CONFLICT (company_document_id, revision_number, source_file)
                DO UPDATE SET
                    job_id = EXCLUDED.job_id,
                    session_id = EXCLUDED.session_id,
                    local_path = EXCLUDED.local_path,
                    status = 'pending',
                    next_retry_at = NOW(),
                    last_error = NULL,
                    updated_at = NOW()
                RETURNING id;
                """,
                (
                    (job_id or "").strip() or None,
                    (session_id or "").strip() or None,
                    str(company_document_id),
                    str(revision_number),
                    str(source_file),
                    str(local_path),
                ),
            )
            row = cur.fetchone()
            outbox_id = int(row[0])

    _WAKE_EVENT.set()
    return outbox_id


def _claim_due(limit: int) -> List[Dict[str, Any]]:
    init_outbox_table()
    rows: List[Dict[str, Any]] = []
    batch = max(1, int(limit))
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                WITH due AS (
                    SELECT id
                    FROM {_TABLE}
                    WHERE status IN ('pending', 'failed')
                      AND next_retry_at <= NOW()
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE {_TABLE} t
                SET status = 'uploading',
                    updated_at = NOW()
                FROM due
                WHERE t.id = due.id
                RETURNING t.*;
                """,
                (batch,),
            )
            rows = list(cur.fetchall() or [])
    return rows


def _mark_uploaded(outbox_id: int, minio_path: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET status = 'uploaded',
                    minio_path = %s,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (minio_path, int(outbox_id)),
            )


def _mark_failed(outbox_id: int, error: str, next_attempt_number: int) -> None:
    delay = _retry_delay_sec(next_attempt_number)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET status = 'failed',
                    attempt_count = attempt_count + 1,
                    last_error = %s,
                    next_retry_at = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE id = %s;
                """,
                ((error or "")[:2000], int(delay), int(outbox_id)),
            )


def _delete_local_copy(local_path: str) -> None:
    path = Path(local_path).resolve()
    if not path.exists() or not path.is_file():
        return

    try:
        path.relative_to(_UPLOAD_ROOT)
    except Exception:
        # Safety: do not delete anything outside the upload directory.
        return

    path.unlink(missing_ok=True)

    # Best effort cleanup for empty revision/document folders.
    for parent in (path.parent, path.parent.parent):
        try:
            parent.relative_to(_UPLOAD_ROOT)
        except Exception:
            continue
        try:
            parent.rmdir()
        except Exception:
            pass


def _job_allows_cleanup(job_id: Optional[str]) -> bool:
    if not job_id:
        return True
    job = get_job_state(str(job_id))
    if not job:
        return False
    return job.status in ("READY", "ERROR")


def process_due_uploads_once(limit: int = 2) -> int:
    claimed = _claim_due(limit=limit)
    if not claimed:
        return 0

    processed = 0
    for row in claimed:
        outbox_id = int(row["id"])
        try:
            local_path = str(row.get("local_path") or "").strip()
            if not local_path:
                raise RuntimeError("local_path missing")
            if not Path(local_path).exists():
                raise FileNotFoundError(f"Local file not found: {local_path}")

            revision_text = str(row.get("revision_number") or "").strip()
            revision_int = int(revision_text) if revision_text.isdigit() else 1

            minio_path = minio_upload_pdf(
                local_path=local_path,
                document_id=str(row["company_document_id"]),
                revision=revision_int,
                filename=str(row["source_file"]),
                overwrite=True,
            )
            _mark_uploaded(outbox_id, minio_path)
            if _job_allows_cleanup(row.get("job_id")):
                _delete_local_copy(local_path)
            processed += 1
        except Exception as e:
            attempts = int(row.get("attempt_count") or 0) + 1
            _mark_failed(outbox_id, str(e), attempts)
            print(
                f"[MINIO-OUTBOX] Upload failed id={outbox_id} "
                f"attempt={attempts}: {e}"
            )
    return processed


def _worker_loop() -> None:
    poll_sec = max(1.0, float(os.getenv("MINIO_OUTBOX_POLL_SEC", "5")))
    batch_size = max(1, int(os.getenv("MINIO_OUTBOX_BATCH_SIZE", "2")))

    while not _STOP_EVENT.is_set():
        try:
            process_due_uploads_once(limit=batch_size)
        except Exception as e:
            print(f"[MINIO-OUTBOX] Worker loop error: {e}")

        _WAKE_EVENT.wait(timeout=poll_sec)
        _WAKE_EVENT.clear()


def start_outbox_worker() -> None:
    global _WORKER_THREAD
    init_outbox_table()

    with _LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return

        _STOP_EVENT.clear()
        _WAKE_EVENT.clear()
        _WORKER_THREAD = threading.Thread(
            target=_worker_loop,
            daemon=True,
            name="minio-outbox-worker",
        )
        _WORKER_THREAD.start()
        print("[MINIO-OUTBOX] Worker started.")


def stop_outbox_worker() -> None:
    global _WORKER_THREAD
    with _LOCK:
        thread = _WORKER_THREAD
        _WORKER_THREAD = None

    if thread and thread.is_alive():
        _STOP_EVENT.set()
        _WAKE_EVENT.set()
        thread.join(timeout=3)
        print("[MINIO-OUTBOX] Worker stopped.")


def get_outbox_summary() -> Dict[str, Any]:
    init_outbox_table()
    status_counts: Dict[str, int] = {}
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT status, COUNT(*)::int AS count
                FROM {_TABLE}
                GROUP BY status;
                """
            )
            for row in cur.fetchall() or []:
                status_counts[str(row["status"])] = int(row["count"])

            cur.execute(
                f"""
                SELECT id, status, attempt_count, last_error, updated_at
                FROM {_TABLE}
                WHERE status IN ('pending', 'failed', 'uploading')
                ORDER BY updated_at DESC
                LIMIT 10;
                """
            )
            recent = list(cur.fetchall() or [])

    return {
        "counts": status_counts,
        "recent": recent,
    }


def cleanup_uploaded_local_copy(
    *,
    company_document_id: str,
    revision_number: str,
    source_file: str,
    local_path: str,
) -> bool:
    """
    Best effort cleanup after commit is done.
    Local PDF is deleted only if MinIO upload already succeeded.
    """
    init_outbox_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT 1
                FROM {_TABLE}
                WHERE company_document_id = %s
                  AND revision_number = %s
                  AND source_file = %s
                  AND status = 'uploaded'
                LIMIT 1;
                """,
                (company_document_id, str(revision_number), source_file),
            )
            row = cur.fetchone()

    if not row:
        return False

    _delete_local_copy(local_path)
    return True


def cancel_outbox_uploads(
    *,
    job_id: Optional[str] = None,
    company_document_id: Optional[str] = None,
    revision_number: Optional[str] = None,
    source_file: Optional[str] = None,
    delete_remote: bool = True,
) -> Dict[str, int]:
    init_outbox_table()

    filters: List[str] = []
    params: List[Any] = []

    clean_job_id = (job_id or "").strip()
    clean_company_document_id = (company_document_id or "").strip()
    clean_revision_number = (revision_number or "").strip()
    clean_source_file = (source_file or "").strip()

    if clean_job_id:
        filters.append("job_id = %s")
        params.append(clean_job_id)
    if clean_company_document_id:
        filters.append("company_document_id = %s")
        params.append(clean_company_document_id)
    if clean_revision_number:
        filters.append("revision_number = %s")
        params.append(clean_revision_number)
    if clean_source_file:
        filters.append("source_file = %s")
        params.append(clean_source_file)

    if not filters:
        return {"deleted": 0, "remote_deleted": 0, "local_deleted": 0}

    where_sql = " AND ".join(filters)
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, job_id, company_document_id, revision_number, source_file, local_path, status
                FROM {_TABLE}
                WHERE {where_sql};
                """,
                tuple(params),
            )
            rows = list(cur.fetchall() or [])

            cur.execute(
                f"DELETE FROM {_TABLE} WHERE {where_sql};",
                tuple(params),
            )
            deleted = int(cur.rowcount or 0)

    remote_deleted = 0
    local_deleted = 0
    for row in rows:
        local_path = str(row.get("local_path") or "").strip()
        if local_path and Path(local_path).exists():
            _delete_local_copy(local_path)
            local_deleted += 1

        if not delete_remote:
            continue

        revision_text = str(row.get("revision_number") or "").strip()
        revision_int = int(revision_text) if revision_text.isdigit() else 1
        try:
            if minio_delete_pdf(
                document_id=str(row.get("company_document_id") or ""),
                revision=revision_int,
                filename=str(row.get("source_file") or ""),
            ):
                remote_deleted += 1
        except Exception as e:
            print(f"[MINIO-OUTBOX] Remote delete failed for cancelled upload id={row.get('id')}: {e}")

    return {
        "deleted": deleted,
        "remote_deleted": remote_deleted,
        "local_deleted": local_deleted,
    }

