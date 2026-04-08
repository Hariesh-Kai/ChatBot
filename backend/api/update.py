# backend/api/update.py

from typing import Dict, Generator, Tuple
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.state.job_state import (
    get_job_state,
    update_job_metadata,
    set_job_progress,
)
from backend.state.job_persistence import get_job_run, upsert_job_run
from backend.rag.commit_worker import start_commit_job
from backend.storage.minio_outbox import enqueue_minio_upload
from backend.rag.upload_cancellation import is_upload_cancel_requested

from backend.contracts.ui_events import (
    metadata_confirmed_event,
    error_event,
    progress_event,
)
from backend.api.chat import UI_EVENT_PREFIX


router = APIRouter(prefix="/metadata", tags=["Metadata"])


class MetadataUpdateRequest(BaseModel):
    job_id: str
    metadata: Dict[str, str]
    force: bool = False


def emit_event(event: dict) -> str:
    return UI_EVENT_PREFIX + json.dumps(event) + "\n"


def progress(value: int, label: str) -> str:
    # Keep frontend contract: PROGRESS events use { value, label }.
    return emit_event(progress_event(value=value, label=label))


def _queue_backup_and_start_commit(job) -> Tuple[int, bool]:
    """
    Merge helper:
    - queue MinIO backup in outbox
    - start async commit worker
    """
    final_metadata = dict(job.metadata or {})

    required_keys = [
        "pdf_path",
        "company_document_id",
        "revision_number",
        "source_file",
        "db_connection",
    ]
    missing = [k for k in required_keys if not final_metadata.get(k)]
    if missing:
        raise RuntimeError(f"Missing required metadata fields: {missing}")

    outbox_id = enqueue_minio_upload(
        job_id=job.job_id,
        session_id=job.session_id,
        company_document_id=str(final_metadata["company_document_id"]),
        revision_number=str(final_metadata["revision_number"]),
        source_file=str(final_metadata["source_file"]),
        local_path=str(final_metadata["pdf_path"]),
    )
    started = start_commit_job(
        job.job_id,
        session_id=job.session_id,
        metadata=final_metadata,
    )
    return outbox_id, started


@router.post("/update")
def update_metadata(payload: MetadataUpdateRequest):
    """
    Finalizes metadata and starts background ingestion (streaming ack).
    """

    def stream() -> Generator[str, None, None]:
        try:
            # 1. Load job
            job = get_job_state(payload.job_id)
            if not job:
                persisted = get_job_run(payload.job_id)
                if persisted:
                    job = type(
                        "PersistedJob",
                        (),
                        {
                            "job_id": str(persisted.get("job_id") or payload.job_id),
                            "session_id": persisted.get("session_id"),
                            "status": str(persisted.get("status") or "PROCESSING"),
                            "metadata": dict(persisted.get("metadata") or {}),
                            "missing_fields": list(persisted.get("missing_fields") or []),
                        },
                    )()
            if not job:
                yield emit_event(error_event("Invalid or expired job_id"))
                return

            # 2. Apply user metadata first
            safe_metadata = {
                k: v for k, v in (payload.metadata or {}).items()
                if k not in ("company_document_id", "revision_number")
            }

            if job.status == "WAIT_FOR_METADATA":
                try:
                    update_job_metadata(job.job_id, safe_metadata)
                    job = get_job_state(payload.job_id) or job
                except Exception:
                    # Cross-process fallback when job is not in this process memory.
                    job.metadata.update(safe_metadata)
                    job.missing_fields = [
                        f for f in list(job.missing_fields or [])
                        if f not in safe_metadata
                    ]
                    if not job.missing_fields:
                        job.status = "PROCESSING"
                    upsert_job_run(
                        job_id=job.job_id,
                        session_id=job.session_id,
                        status=job.status,
                        metadata=dict(job.metadata or {}),
                        missing_fields=list(job.missing_fields or []),
                    )
            elif safe_metadata:
                # Process-local object; mutate metadata in place.
                job.metadata.update(safe_metadata)
            elif job.status != "PROCESSING":
                yield emit_event(
                    error_event(f"Job not accepting metadata (status={job.status})")
                )
                return

            # 3. Validate after merge
            if job.missing_fields and not payload.force:
                yield emit_event(error_event(f"Missing fields: {job.missing_fields}"))
                return

            if job.missing_fields and payload.force:
                job.missing_fields = []
                if job.status == "WAIT_FOR_METADATA":
                    job.status = "PROCESSING"

            if is_upload_cancel_requested(job_id=job.job_id, session_id=job.session_id):
                yield emit_event(error_event("Cancelled by user"))
                return

            # 4. Queue background work
            outbox_id, started = _queue_backup_and_start_commit(job)
            set_job_progress(
                job.job_id,
                value=40,
                label="Queued for background processing.",
            )
            yield progress(10, f"Queued backup for MinIO (outbox #{outbox_id}).")
            if started:
                yield progress(20, "Document processing started in background.")
            else:
                yield progress(20, "Document processing is already running.")

            # 5. Confirm to frontend
            yield emit_event(
                metadata_confirmed_event(
                    "Metadata saved. Document processing continues in background."
                )
            )
        except Exception as e:
            yield emit_event(error_event(str(e) or "Metadata update failed"))

    return StreamingResponse(stream(), media_type="text/plain")
