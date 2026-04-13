from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.memory.pg_memory import save_active_document
from backend.queue.celery_app import celery_app, is_celery_enabled
from backend.rag.pipeline import run_pipeline
from backend.rag.upload_cancellation import (
    UploadCancellationError,
    cleanup_cancelled_upload,
    is_upload_cancel_requested,
    raise_if_upload_cancel_requested,
)
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)
from backend.state.dev_settings import get_dev_settings
from backend.state.job_persistence import get_job_run, list_job_runs
from backend.state.job_state import (
    get_job_state,
    mark_job_error,
    mark_job_ready,
    set_job_progress,
)
from backend.storage.minio_outbox import (
    cleanup_uploaded_local_copy,
    process_due_uploads_once,
)

_LOCK = threading.Lock()
_ACTIVE_JOBS: Dict[str, threading.Thread] = {}
_MAX_CONCURRENT_COMMITS = max(1, int(os.getenv("RAG_COMMIT_CONCURRENCY", "1")))
_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT_COMMITS)
_CELERY_TASK_NAME = os.getenv("CELERY_COMMIT_TASK_NAME", "chatui.rag.commit")
_HEARTBEAT_INTERVAL_SEC = max(5, int(os.getenv("RAG_COMMIT_HEARTBEAT_SEC", "15")))
_RECOVERY_STALE_SEC = max(
    _HEARTBEAT_INTERVAL_SEC * 3,
    int(os.getenv("RAG_COMMIT_RECOVERY_STALE_SEC", "90")),
)
_RECOVERY_LIMIT = max(1, int(os.getenv("RAG_COMMIT_RECOVERY_LIMIT", "16")))


def _required_keys() -> Tuple[str, ...]:
    return (
        "pdf_path",
        "company_document_id",
        "revision_number",
        "source_file",
        "db_connection",
    )


def _start_progress_heartbeat(
    job_id: str,
    *,
    value: int,
    label: str,
) -> Tuple[Dict[str, Any], threading.Event, threading.Thread]:
    state: Dict[str, Any] = {
        "value": max(0, min(100, int(value))),
        "label": str(label),
    }
    stop_event = threading.Event()

    def _beat() -> None:
        while not stop_event.wait(_HEARTBEAT_INTERVAL_SEC):
            set_job_progress(
                job_id,
                value=int(state.get("value") or 0),
                label=str(state.get("label") or "Processing in background."),
            )

    thread = threading.Thread(
        target=_beat,
        daemon=True,
        name=f"rag-heartbeat-{job_id[:8]}",
    )
    thread.start()
    return state, stop_event, thread


def _update_progress_state(
    state: Dict[str, Any],
    *,
    value: Optional[int] = None,
    label: Optional[str] = None,
) -> None:
    if value is not None:
        try:
            state["value"] = max(0, min(100, int(value)))
        except Exception:
            state["value"] = 0
    if label is not None:
        state["label"] = str(label)


def _resolve_commit_payload(
    *,
    job_id: str,
    session_id: Optional[str],
    final_metadata: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Dict[str, Any]]:
    clean_session_id = (session_id or "").strip() or None
    metadata = dict(final_metadata or {})

    if not metadata or not clean_session_id:
        job = get_job_state(job_id)
        if job:
            if not clean_session_id:
                clean_session_id = (job.session_id or "").strip() or None
            if not metadata:
                metadata = dict(job.metadata or {})

    if not metadata or not clean_session_id:
        persisted = get_job_run(job_id)
        if persisted:
            if not clean_session_id:
                clean_session_id = (persisted.get("session_id") or "").strip() or None
            if not metadata:
                maybe_meta = persisted.get("metadata")
                if isinstance(maybe_meta, dict):
                    metadata = dict(maybe_meta)

    missing = [k for k in _required_keys() if not metadata.get(k)]
    if missing:
        raise RuntimeError(f"Missing required metadata fields: {missing}")

    return clean_session_id, metadata


def _enqueue_celery_commit(
    *,
    job_id: str,
    session_id: Optional[str],
    final_metadata: Dict[str, Any],
) -> bool:
    if celery_app is None:
        return False

    celery_app.send_task(
        _CELERY_TASK_NAME,
        kwargs={
            "job_id": job_id,
            "session_id": session_id,
            "final_metadata": final_metadata,
        },
    )
    set_job_progress(
        job_id,
        value=max(int(final_metadata.get("progress", 40) or 40), 40),
        label="Queued on RabbitMQ worker.",
    )
    print(f"[RAG-COMMIT] Celery task queued job_id={job_id}")
    return True


def start_commit_job(
    job_id: str,
    *,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Start asynchronous commit for a job_id.
    Returns False when the same job is already running.
    """
    job_id = (job_id or "").strip()
    if not job_id:
        return False

    resolved_session_id, resolved_metadata = _resolve_commit_payload(
        job_id=job_id,
        session_id=session_id,
        final_metadata=metadata,
    )

    if is_celery_enabled():
        try:
            return _enqueue_celery_commit(
                job_id=job_id,
                session_id=resolved_session_id,
                final_metadata=resolved_metadata,
            )
        except Exception as e:
            print(f"[RAG-COMMIT] Celery queue failed, falling back to local thread: {e}")

    with _LOCK:
        existing = _ACTIVE_JOBS.get(job_id)
        if existing and existing.is_alive():
            return False

        t = threading.Thread(
            target=_run_commit,
            args=(job_id, resolved_session_id, resolved_metadata),
            daemon=True,
            name=f"rag-commit-{job_id[:8]}",
        )
        _ACTIVE_JOBS[job_id] = t
        t.start()
        return True


def run_commit_payload(
    *,
    job_id: str,
    session_id: Optional[str],
    final_metadata: Dict[str, Any],
) -> None:
    progress_state, heartbeat_stop, heartbeat_thread = _start_progress_heartbeat(
        job_id,
        value=42,
        label="Background processing started.",
    )

    try:
        raise_if_upload_cancel_requested(job_id=job_id, session_id=session_id)
        set_job_progress(
            job_id,
            value=42,
            label="Background processing started.",
        )

        job_dir = (
            Path(__file__).resolve().parents[1]
            / "tmp"
            / "jobs"
            / job_id
        )
        for evt in run_pipeline(
            pdf_path=final_metadata["pdf_path"],
            job_dir=str(job_dir),
            company_document_id=final_metadata["company_document_id"],
            db_connection=final_metadata["db_connection"],
            extra_metadata={**final_metadata, "session_id": session_id, "job_id": job_id},
            mode="commit",
        ):
            if isinstance(evt, dict) and evt.get("type") == "PROGRESS":
                _update_progress_state(
                    progress_state,
                    value=evt.get("value"),
                    label=evt.get("label"),
                )
                set_job_progress(
                    job_id,
                    value=evt.get("value"),
                    label=evt.get("label"),
                )

        raise_if_upload_cancel_requested(job_id=job_id, session_id=session_id)

        rev_text = str(final_metadata["revision_number"])
        try:
            settings = get_dev_settings()
        except Exception:
            settings = {}
        resolved_collection_name = normalize_collection_name(
            final_metadata.get("rag_collection_name")
            or settings.get("rag_collection_name")
            or DEFAULT_RAG_COLLECTION_NAME
        )

        if session_id:
            save_active_document(
                session_id=session_id,
                company_document_id=final_metadata["company_document_id"],
                revision_number=rev_text,
                collection_name=resolved_collection_name,
                filename=final_metadata["source_file"],
            )

        mark_job_ready(job_id)

        try:
            process_due_uploads_once(limit=1)
        except Exception as e:
            print(f"[RAG-COMMIT] Outbox tick failed job_id={job_id}: {e}")

        cleanup_uploaded_local_copy(
            company_document_id=str(final_metadata["company_document_id"]),
            revision_number=str(final_metadata["revision_number"]),
            source_file=str(final_metadata["source_file"]),
            local_path=str(final_metadata["pdf_path"]),
        )
        print(f"[RAG-COMMIT] Completed job_id={job_id}")
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)


def run_commit_payload_safe(
    job_id: str,
    session_id: Optional[str],
    final_metadata: Dict[str, Any],
) -> None:
    try:
        with _SEMAPHORE:
            run_commit_payload(
                job_id=job_id,
                session_id=session_id,
                final_metadata=final_metadata,
            )
    except UploadCancellationError as e:
        cleanup = cleanup_cancelled_upload(job_id=job_id, metadata=final_metadata)
        mark_job_error(job_id, str(e))
        print(f"[RAG-COMMIT] Cancelled job_id={job_id} cleanup={cleanup}")
    except Exception as e:
        if is_upload_cancel_requested(job_id=job_id, session_id=session_id):
            cleanup = cleanup_cancelled_upload(job_id=job_id, metadata=final_metadata)
            mark_job_error(job_id, "Cancelled by user")
            print(
                f"[RAG-COMMIT] Cancelled job_id={job_id} after worker race "
                f"cleanup={cleanup} error={e}"
            )
            return
        mark_job_error(job_id, f"Commit failed: {e}")
        print(f"[RAG-COMMIT] Failed job_id={job_id}: {e}")
    finally:
        with _LOCK:
            _ACTIVE_JOBS.pop(job_id, None)


def _run_commit(
    job_id: str,
    session_id: Optional[str],
    final_metadata: Dict[str, Any],
) -> None:
    run_commit_payload_safe(
        job_id=job_id,
        session_id=session_id,
        final_metadata=final_metadata,
    )


def recover_stale_commit_jobs() -> Dict[str, int]:
    recovered = 0
    skipped = 0
    failed = 0

    try:
        stale_jobs = list_job_runs(
            status="PROCESSING",
            older_than_seconds=_RECOVERY_STALE_SEC,
            limit=_RECOVERY_LIMIT,
        )
    except Exception as e:
        print(f"[RAG-COMMIT] Recovery scan failed: {e}")
        return {"recovered": 0, "skipped": 0, "failed": 0}

    for persisted in stale_jobs:
        job_id = str(persisted.get("job_id") or "").strip()
        if not job_id:
            skipped += 1
            continue

        metadata = persisted.get("metadata")
        final_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        session_id = str(persisted.get("session_id") or "").strip() or None
        missing = [key for key in _required_keys() if not final_metadata.get(key)]

        if missing:
            failed += 1
            mark_job_error(
                job_id,
                f"Unable to recover job after restart; missing metadata: {missing}",
            )
            print(f"[RAG-COMMIT] Recovery failed job_id={job_id} missing={missing}")
            continue

        recovery_metadata = {
            **final_metadata,
            "replace_existing": True,
        }
        try:
            started = start_commit_job(
                job_id,
                session_id=session_id,
                metadata=recovery_metadata,
            )
        except Exception as e:
            failed += 1
            print(f"[RAG-COMMIT] Recovery failed job_id={job_id}: {e}")
            continue

        if not started:
            skipped += 1
            continue

        current_progress = max(40, int(persisted.get("progress") or 40))
        set_job_progress(
            job_id,
            value=current_progress,
            label="Resuming background processing from saved checkpoints.",
        )
        recovered += 1
        print(f"[RAG-COMMIT] Recovery resumed job_id={job_id}")

    return {"recovered": recovered, "skipped": skipped, "failed": failed}


def get_active_commit_jobs() -> Dict[str, str]:
    with _LOCK:
        return {
            job_id: ("alive" if thread.is_alive() else "done")
            for job_id, thread in _ACTIVE_JOBS.items()
        }
