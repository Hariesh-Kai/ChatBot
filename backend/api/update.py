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


def progress(stage: str, msg: str, progress: int) -> str:
    return emit_event({
        "type": "PROGRESS",
        "stage": stage,
        "message": msg,
        "progress": progress,
    })



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
                k: v for k, v in payload.metadata.items()
                if k not in ("company_document_id", "revision_number")
            }

            update_job_metadata(job.job_id, safe_metadata)


            # Re-fetch updated job
            job = get_job_state(payload.job_id)

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

            yield progress(
                "upload",
                f"Backing up {final_metadata['source_file']}…",
                10,
            )

            minio_upload_pdf(
                local_path=final_metadata["pdf_path"],
                document_id=final_metadata["company_document_id"],
                revision=rev_int,
                filename=final_metadata["source_file"],
                overwrite=True,
            )

            yield progress("upload", "Backup complete.", 30)

            # --------------------------------------------------
            # 5. RAG PIPELINE
            # --------------------------------------------------
            job_dir = (
                Path(__file__).resolve().parents[1]
                / "tmp"
                / "jobs"
                / job.job_id
            )

            yield progress(
                "processing",
                "Chunking and embedding document…",
                60,
            )

            run_pipeline(
                pdf_path=final_metadata["pdf_path"],
                job_dir=str(job_dir),
                company_document_id=final_metadata["company_document_id"],
                db_connection=final_metadata["db_connection"],
                extra_metadata=final_metadata,
                mode="commit",
            )

            yield progress("processing", "Indexing complete.", 90)

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
            yield progress(
                "finalizing",
                "Finalizing document and updating index…",
                95,
            )

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

