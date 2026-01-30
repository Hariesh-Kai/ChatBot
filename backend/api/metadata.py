# backend/api/metadata.py

import json
from typing import Dict, Any, Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.contracts.ui_events import (
    error_event,
    metadata_confirmed_event,
)

from backend.api.chat import UI_EVENT_PREFIX

from backend.state.job_state import (
    get_job_state,
    update_job_metadata,
)



from backend.memory.redis_memory import clear_used_chunk_ids


# ============================================================
# API ROUTER
# ============================================================

router = APIRouter(prefix="/metadata", tags=["Metadata"])


# ============================================================
# ALLOWED METADATA (FINAL CONTRACT)
# ============================================================

ALLOWED_METADATA_KEYS = {
    "document_type",
    "revision_code",
    "revision_date",
}

IMMUTABLE_KEYS = {
    "company_document_id",
    "revision_number",
}


# ============================================================
# REQUEST SCHEMA
# ============================================================

class MetadataUpdateRequest(BaseModel):
    job_id: str = Field(..., description="Job / document identifier")
    metadata: Dict[str, Any] = Field(
        ..., description="Corrected metadata key-value pairs"
    )


# ============================================================
# STREAM HELPERS
# ============================================================

def emit_event(event: dict) -> str:
    return UI_EVENT_PREFIX + json.dumps(event) + "\n"


# ============================================================
# METADATA UPDATE ENDPOINT (STREAMING)
# ============================================================

@router.post("/")
def update_metadata(req: MetadataUpdateRequest):

    def stream() -> Generator[str, None, None]:
        try:
            job_id = req.job_id.strip()
            if not job_id:
                yield emit_event(error_event("job_id is required"))
                return

            job_state = get_job_state(job_id) 
            if not job_state:
                yield emit_event(error_event("Job not found"))
                return

            if job_state.status != "WAIT_FOR_METADATA":
                yield emit_event(
                    error_event(
                        f"Job not waiting for metadata (status={job_state.status})"
                    )
                )
                return

            # ---------------------------------------------
            # VALIDATE METADATA KEYS
            # ---------------------------------------------

            invalid = set(req.metadata.keys()) - ALLOWED_METADATA_KEYS
            if invalid:
                yield emit_event(
                    error_event(
                        f"Invalid metadata keys: {sorted(invalid)}"
                    )
                )
                return

            forbidden = IMMUTABLE_KEYS & set(req.metadata.keys())
            if forbidden:
                yield emit_event(
                    error_event(
                        f"Immutable metadata keys cannot be updated: {sorted(forbidden)}"
                    )
                )
                return

            # ---------------------------------------------
            # UPDATE JOB METADATA
            # ---------------------------------------------

            updated_job = update_job_metadata(
                job_id=job_id,
                updated_metadata=req.metadata,
            )
            
            
            if updated_job.status != "PROCESSING":
                yield emit_event(
                    error_event(f"Job state invalid after metadata update: {updated_job.status}")
                )
                return
        
            # ---------------------------------------------
            # RESET RAG STATE
            # ---------------------------------------------

            if updated_job.session_id:
                clear_used_chunk_ids(updated_job.session_id)

            # ---------------------------------------------
            #  ✅ CONFIRM METADATA SAVED (PIPELINE RUNS IN /upload/commit)
            # --------------------------------------------
            yield emit_event(
                metadata_confirmed_event("Metadata saved. Ready to ingest.")
            )
            return
    
        except Exception as e:
            yield emit_event(error_event(str(e)))

    return StreamingResponse(stream(), media_type="text/plain")
