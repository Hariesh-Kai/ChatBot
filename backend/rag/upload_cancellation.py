from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from backend.state.abort_signals import is_aborted
from backend.state.job_persistence import get_job_run
from backend.storage.minio_outbox import cancel_outbox_uploads

BASE_DIR = Path(__file__).resolve().parents[1]
TMP_ROOT = (BASE_DIR / "tmp" / "jobs").resolve()
UPLOAD_ROOT = (BASE_DIR / "storage" / "uploads").resolve()


class UploadCancellationError(RuntimeError):
    pass


def _is_cancel_error_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and "cancel" in text


def is_upload_cancel_requested(
    *,
    job_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    clean_job_id = (job_id or "").strip()
    clean_session_id = (session_id or "").strip()

    if clean_job_id:
        if is_aborted(clean_job_id):
            return True
        persisted = get_job_run(clean_job_id)
        if isinstance(persisted, dict):
            status = str(persisted.get("status") or "").strip().upper()
            if status == "ERROR" and _is_cancel_error_text(persisted.get("error")):
                return True

    if clean_session_id and is_aborted(clean_session_id):
        return True

    return False


def raise_if_upload_cancel_requested(
    *,
    job_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    if is_upload_cancel_requested(job_id=job_id, session_id=session_id):
        raise UploadCancellationError("Cancelled by user")


def _safe_delete_dir(path: Optional[str], *, root: Path) -> bool:
    candidate = str(path or "").strip()
    if not candidate:
        return False

    target = Path(candidate).resolve()
    if not target.exists():
        return False

    try:
        target.relative_to(root)
    except Exception:
        return False

    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


def _safe_delete_file(path: Optional[str], *, root: Path) -> bool:
    candidate = str(path or "").strip()
    if not candidate:
        return False

    target = Path(candidate).resolve()
    if not target.exists() or not target.is_file():
        return False

    try:
        target.relative_to(root)
    except Exception:
        return False

    target.unlink(missing_ok=True)

    for parent in (target.parent, target.parent.parent):
        try:
            parent.relative_to(root)
        except Exception:
            continue
        try:
            parent.rmdir()
        except Exception:
            pass

    return not target.exists()


def cleanup_cancelled_upload(
    *,
    job_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    clean_job_id = (job_id or "").strip()
    payload = dict(metadata or {})

    cleanup: Dict[str, Any] = {
        "job_id": clean_job_id,
        "job_dir_deleted": False,
        "pdf_deleted": False,
        "vector_rows_deleted": 0,
        "outbox_deleted": 0,
        "outbox_local_deleted": 0,
        "outbox_remote_deleted": 0,
    }

    if not clean_job_id:
        return cleanup

    job_dir = str((TMP_ROOT / clean_job_id).resolve())
    cleanup["job_dir_deleted"] = _safe_delete_dir(job_dir, root=TMP_ROOT)

    cleanup["pdf_deleted"] = _safe_delete_file(
        payload.get("pdf_path"),
        root=UPLOAD_ROOT,
    )

    try:
        outbox_cleanup = cancel_outbox_uploads(
            job_id=clean_job_id,
            company_document_id=str(payload.get("company_document_id") or "") or None,
            revision_number=str(payload.get("revision_number") or "") or None,
            source_file=str(payload.get("source_file") or "") or None,
            delete_remote=True,
        )
    except Exception as exc:
        print(f"[UPLOAD-CANCEL] Outbox cleanup failed for job_id={clean_job_id}: {exc}")
        outbox_cleanup = {"deleted": 0, "local_deleted": 0, "remote_deleted": 0}

    cleanup["outbox_deleted"] = int(outbox_cleanup.get("deleted") or 0)
    cleanup["outbox_local_deleted"] = int(outbox_cleanup.get("local_deleted") or 0)
    cleanup["outbox_remote_deleted"] = int(outbox_cleanup.get("remote_deleted") or 0)

    connection_string = str(payload.get("db_connection") or "").strip()
    company_document_id = str(payload.get("company_document_id") or "").strip()
    revision_number = str(payload.get("revision_number") or "").strip()
    collection_name = str(payload.get("rag_collection_name") or "").strip() or None

    if connection_string and company_document_id and revision_number:
        try:
            from backend.rag.ingest import delete_document_revision

            cleanup["vector_rows_deleted"] = delete_document_revision(
                connection_string=connection_string,
                company_document_id=company_document_id,
                revision_number=revision_number,
                collection_name=collection_name or "rag_documents",
            )
        except Exception as exc:
            print(f"[UPLOAD-CANCEL] Vector cleanup failed for job_id={clean_job_id}: {exc}")

    return cleanup
