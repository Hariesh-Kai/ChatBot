# backend/api/update.py

from typing import Dict, Generator
from pathlib import Path
import json
import time

from fastapi import APIRouter, HTTPException
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

router = APIRouter(prefix="/metadata", tags=["Metadata"])

class MetadataUpdateRequest(BaseModel):
    job_id: str
    metadata: Dict[str, str]
    force: bool = False

# Helper to format stream events
def _sse_msg(stage: str, msg: str, progress: int) -> str:
    return json.dumps({"stage": stage, "message": msg, "progress": progress}) + "\n"

@router.post("/update")
def update_metadata(payload: MetadataUpdateRequest):
    """
    Finalizes metadata and commits document ingestion via Streaming Response.
    """
    
    def _process_stream() -> Generator[str, None, None]:
        # 1. VALIDATION
        job = get_job_state(payload.job_id)
        if not job:
            yield _sse_msg("error", "Invalid or expired job_id", 0)
            return

        # Update metadata state
        update_job_metadata(job.job_id, payload.metadata)
        
        # Check completion
        if job.missing_fields and not payload.force:
            yield _sse_msg("error", f"Missing fields: {job.missing_fields}", 0)
            return

        final_metadata = job.metadata
        
        # 2. MINIO UPLOAD
        yield _sse_msg("upload", f"Backing up {final_metadata['source_file']}...", 10)
        
        try:
            rev_val = final_metadata["revision_number"]
            rev_int = int(rev_val) if str(rev_val).isdigit() else 1

            minio_upload_pdf(
                local_path=final_metadata["pdf_path"],
                document_id=final_metadata["company_document_id"],
                revision=rev_int,
                filename=final_metadata["source_file"],
                overwrite=True
            )
            yield _sse_msg("upload", "Backup complete.", 30)
        except Exception as e:
            yield _sse_msg("error", f"MinIO Upload Failed: {str(e)}", 0)
            return

        # 3. RAG PIPELINE
        job_dir = (
            Path(__file__).resolve().parents[1]
            / "tmp"
            / "jobs"
            / job.job_id
        )

        try:
            yield _sse_msg("processing", "Analyzing document structure...", 40)
            
            # Note: run_pipeline is synchronous, so this step will hang until done.
            # Ideally, run_pipeline should accept a callback, but for now we wrap the heavy call.
            yield _sse_msg("processing", "Chunking and Embedding (this may take a moment)...", 60)
            
            run_pipeline(
                pdf_path=final_metadata["pdf_path"],
                job_dir=str(job_dir),
                company_document_id=final_metadata["company_document_id"], 
                db_connection=final_metadata["db_connection"],
                extra_metadata=final_metadata,
                mode="commit",
            )
            yield _sse_msg("processing", "Indexing complete.", 90)
            
        except Exception as e:
            yield _sse_msg("error", f"RAG Pipeline Failed: {str(e)}", 0)
            return

        # 4. FINALIZE
        mark_job_ready(job.job_id)
        save_active_document(
            session_id=job.session_id,
            company_document_id=final_metadata["company_document_id"],
            revision_number=str(final_metadata["revision_number"]),
        )

        # 5. DONE
        yield _sse_msg("done", "Document is ready.", 100)

    return StreamingResponse(_process_stream(), media_type="application/x-ndjson")