# backend/api/update.py

from typing import Dict, Generator
from pathlib import Path
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.state.job_state import (
    get_job_state,
    update_job_metadata,
    mark_job_ready,
)
from backend.rag.pipeline import run_pipeline
from backend.storage.minio_client import upload_pdf as minio_upload_pdf
from backend.memory.pg_memory import save_active_document

from backend.contracts.ui_events import (
    metadata_confirmed_event,
    error_event,
    progress_event,
)
from backend.api.chat import UI_EVENT_PREFIX


# ============================================================
# ROUTER (🔥 FIXED: NO COLLISION)
# ============================================================

router = APIRouter(prefix="/metadata", tags=["Metadata"])



# ============================================================
# REQUEST SCHEMA
# ============================================================

class MetadataUpdateRequest(BaseModel):
    job_id: str
    metadata: Dict[str, str]
    force: bool = False


# ============================================================
# STREAM HELPERS
# ============================================================

def emit_event(event: dict) -> str:
    return UI_EVENT_PREFIX + json.dumps(event) + "\n"


def progress(value: int, label: str) -> str:
    # Keep frontend contract: PROGRESS events use { value, label }.
    return emit_event(progress_event(value=value, label=label))



# ============================================================
# FINAL METADATA COMMIT ENDPOINT
# ============================================================

@router.post("/update")
def update_metadata(payload: MetadataUpdateRequest):
    """
    Finalizes metadata and commits document ingestion (STREAMING).
    """

    def stream() -> Generator[str, None, None]:
        try:
            # --------------------------------------------------
            # 1. LOAD JOB
            # --------------------------------------------------
            job = get_job_state(payload.job_id)
            if not job:
                yield emit_event(error_event("Invalid or expired job_id"))
                return

            # --------------------------------------------------
            # 2. APPLY USER METADATA FIRST (CRITICAL)
            # --------------------------------------------------
            safe_metadata = {
                k: v for k, v in (payload.metadata or {}).items()
                if k not in ("company_document_id", "revision_number")
            }

            # If the job is waiting for metadata, merge and advance state.
            # If it's already processing (no missing fields), allow commit with empty metadata.
            if job.status == "WAIT_FOR_METADATA":
                update_job_metadata(job.job_id, safe_metadata)
                job = get_job_state(payload.job_id)
            elif safe_metadata:
                # job is process-local object; mutating the dict updates state in-place
                job.metadata.update(safe_metadata)

            # --------------------------------------------------
            # 3. VALIDATE AFTER MERGE
            # --------------------------------------------------
            if job.missing_fields and not payload.force:
                yield emit_event(
                    error_event(f"Missing fields: {job.missing_fields}")
                )
                return

            final_metadata = job.metadata

            # --------------------------------------------------
            # 4. MINIO BACKUP
            # --------------------------------------------------
            rev_val = final_metadata["revision_number"]
            rev_int = int(rev_val) if str(rev_val).isdigit() else 1

            required_keys = [
                "pdf_path",
                "company_document_id",
                "revision_number",
                "source_file",
            ]

            missing = [k for k in required_keys if k not in final_metadata]
            if missing:
                yield emit_event(
                    error_event(f"Missing required metadata fields: {missing}")
                )
                return

            yield progress(10, f"Backing up {final_metadata['source_file']}...")

            minio_upload_pdf(
                local_path=final_metadata["pdf_path"],
                document_id=final_metadata["company_document_id"],
                revision=rev_int,
                filename=final_metadata["source_file"],
                overwrite=True,
            )

            yield progress(30, "Backup complete.")

            # --------------------------------------------------
            # 5. RAG PIPELINE
            # --------------------------------------------------
            job_dir = (
                Path(__file__).resolve().parents[1]
                / "tmp"
                / "jobs"
                / job.job_id
            )

            # Stream the pipeline's own progress events (chunking, enrichment, indexing, finalizing).
            for evt in run_pipeline(
                pdf_path=final_metadata["pdf_path"],
                job_dir=str(job_dir),
                company_document_id=final_metadata["company_document_id"],
                db_connection=final_metadata["db_connection"],
                extra_metadata={**final_metadata, "session_id": job.session_id},
                mode="commit",
            ):
                if isinstance(evt, dict) and evt.get("type"):
                    yield emit_event(evt)

            # --------------------------------------------------
            # 6. FINALIZE JOB
            # --------------------------------------------------
            save_active_document(
                session_id=job.session_id,
                company_document_id=final_metadata["company_document_id"],
                revision_number=rev_int,
                filename=final_metadata["source_file"],
            )

            mark_job_ready(job.job_id)

            # --------------------------------------------------
            # 7. CONFIRM TO FRONTEND
            # --------------------------------------------------
            # NOTE: do not emit additional progress here; the pipeline already emits 95/100.

            # notify frontend to resume UI + streaming
            yield emit_event(
                metadata_confirmed_event("Document is ready")
            )

        except Exception as e:
            yield emit_event(
                error_event(str(e) or "Metadata update failed")
            )
            # optional but recommended
            # if job:
                # mark_job_error(job.job_id)


    return StreamingResponse(stream(), media_type="text/plain")

