# backend/api/metadata.py

import json
from typing import Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.contracts.ui_events import metadata_confirmed_event
from backend.api.chat import UI_EVENT_PREFIX

from backend.state.job_state import (
    get_job_state,
    update_job_metadata,
)

from backend.memory.redis_memory import clear_used_chunk_ids


# ============================================================
# API ROUTER
# ============================================================

router = APIRouter(prefix="/metadata/correct", tags=["Metadata"])


# ============================================================
# ALLOWED METADATA (FINAL CONTRACT)
# ============================================================

ALLOWED_METADATA_KEYS = {
    "document_type",
    "revision_code",
    "revision_date",   # optional
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
# RESPONSE SCHEMA
# ============================================================

class MetadataUpdateResponse(BaseModel):
    job_id: str
    status: str
    updated_fields: Dict[str, Any]


# ============================================================
# METADATA UPDATE ENDPOINT
# ============================================================

@router.post("/", response_model=MetadataUpdateResponse)
def update_metadata(req: MetadataUpdateRequest):
    job_id = req.job_id.strip()

    if not job_id:
        raise HTTPException(400, "job_id is required")

    job_state = get_job_state(job_id)
    if not job_state:
        raise HTTPException(404, "Job not found")

    if job_state.status != "WAIT_FOR_METADATA":
        raise HTTPException(
            400,
            f"Job not waiting for metadata (status={job_state.status})",
        )

    # --------------------------------------------------
    # 🔒 VALIDATE METADATA KEYS
    # --------------------------------------------------

    invalid = set(req.metadata.keys()) - ALLOWED_METADATA_KEYS
    if invalid:
        raise HTTPException(
            400,
            f"Invalid metadata keys: {sorted(invalid)}",
        )

    forbidden = IMMUTABLE_KEYS & set(req.metadata.keys())
    if forbidden:
        raise HTTPException(
            400,
            f"Immutable metadata keys cannot be updated: {sorted(forbidden)}",
        )

    # --------------------------------------------------
    # UPDATE JOB METADATA
    # --------------------------------------------------

    updated_job = update_job_metadata(
        job_id=job_id,
        updated_metadata=req.metadata,
    )

    # --------------------------------------------------
    # RESET RAG STATE
    # --------------------------------------------------

    if updated_job.session_id:
        clear_used_chunk_ids(updated_job.session_id)

    # --------------------------------------------------
    # EMIT UI EVENT (UNBLOCK FRONTEND)
    # --------------------------------------------------

    print(
        UI_EVENT_PREFIX + json.dumps(
            metadata_confirmed_event("Metadata updated")
        )
    )

    return MetadataUpdateResponse(
        job_id=job_id,
        status=updated_job.status,
        updated_fields=req.metadata,
    )
