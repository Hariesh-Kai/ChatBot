# backend/api/update.py

from typing import Dict
from pathlib import Path
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.contracts.ui_events import metadata_confirmed_event
from backend.state.job_state import (
    get_job_state,
    update_job_metadata,
    mark_job_ready,
)
from backend.rag.pipeline import run_pipeline

# ✅ NEW: Import MinIO Upload Function
from backend.storage.minio_client import upload_pdf as minio_upload_pdf

# ✅ NEW: persist active document
from backend.memory.pg_memory import save_active_document

# ============================================================
# API ROUTER
# ============================================================

router = APIRouter(prefix="/metadata", tags=["Metadata"])


# ============================================================
# SCHEMAS
# ============================================================

class MetadataUpdateRequest(BaseModel):
    job_id: str
    metadata: Dict[str, str]
    force: bool = False


class MetadataUpdateResponse(BaseModel):
    job_id: str
    company_document_id: str
    revision_number: str # ✅ STRING (Fixed)
    status: str  # committed


# ============================================================
# METADATA UPDATE + COMMIT
# ============================================================

@router.post("/update", response_model=MetadataUpdateResponse)
def update_metadata(payload: MetadataUpdateRequest):
    """
    Finalizes metadata and commits document ingestion.

    FLOW:
    - Validate job (smart lookup via Session OR Job ID)
    - Merge metadata
    - Ensure completeness
    - Upload to MinIO (Backup)
    - Run commit pipeline
    - Mark job READY
    """
    
    # --- LOG START ---
    print(f"\n------------------------------------------------")
    print(f"🚀 [PHASE 2] Metadata Update & Commit: {payload.job_id}")
    print(f"------------------------------------------------")

    # --------------------------------------------------
    # 1️⃣ LOAD JOB (SMART LOOKUP)
    # --------------------------------------------------
    # get_job_state handles both job_id AND session_id
    job = get_job_state(payload.job_id)
    if not job:
        raise HTTPException(404, "Invalid or expired job_id")

    # --------------------------------------------------
    # 2️⃣ PREVENT IDENTITY OVERRIDE
    # --------------------------------------------------

    forbidden = {"company_document_id", "revision_number"}
    if forbidden & payload.metadata.keys():
        raise HTTPException(
            400,
            "company_document_id and revision_number cannot be overridden",
        )

    # --------------------------------------------------
    # 3️⃣ MERGE + VALIDATE METADATA
    # --------------------------------------------------

    # 🔥 CRITICAL FIX: Use the resolved job.job_id, NOT payload.job_id
    # This ensures we pass the UUID even if frontend sent the Session ID
    job = update_job_metadata(
        job_id=job.job_id,
        updated_metadata=payload.metadata,
    )

    if job.missing_fields and not payload.force:
        raise HTTPException(
            400,
            f"Missing required metadata fields: {job.missing_fields}",
        )

    final_metadata = job.metadata

    # --------------------------------------------------
    # ☁️ MINIO UPLOAD (Added)
    # --------------------------------------------------
    try:
        print(f"☁️  [MINIO] Uploading: {final_metadata['source_file']} ...")
        
        # Helper: Convert to int for MinIO path versioning if possible
        rev_val = final_metadata["revision_number"]
        rev_int = int(rev_val) if str(rev_val).isdigit() else 1

        minio_path = minio_upload_pdf(
            local_path=final_metadata["pdf_path"],
            document_id=final_metadata["company_document_id"],
            revision=rev_int,
            filename=final_metadata["source_file"],
            overwrite=True
        )
        print(f"✅ [MINIO] Upload Success! Path: {minio_path}")
    except Exception as e:
        print(f"❌ [MINIO] Upload Failed: {e}")
        # We abort if the backup fails to ensure data safety
        raise HTTPException(500, f"MinIO Backup Failed: {e}")

    # --------------------------------------------------
    # 4️⃣ COMMIT PIPELINE
    # --------------------------------------------------

    job_dir = (
        Path(__file__).resolve().parents[1]
        / "tmp"
        / "jobs"
        / job.job_id # ✅ Use canonical ID
    )

    try:
        print(f"⚙️  [RAG] Starting Chunking & Embedding...")
        run_pipeline(
            pdf_path=final_metadata["pdf_path"],
            job_dir=str(job_dir),
            company_document_id=final_metadata["company_document_id"], 
            db_connection=final_metadata["db_connection"],
            extra_metadata=final_metadata,
            mode="commit",
        )
        print(f"✅ [RAG] Pipeline Complete. Chunks saved to DB.")
    except Exception as e:
        print(f"❌ [RAG] Pipeline Failed: {e}")
        raise HTTPException(500, f"RAG Pipeline Failed: {e}")

    # --------------------------------------------------
    # 5️⃣ MARK JOB READY
    # --------------------------------------------------

    mark_job_ready(job.job_id) # ✅ Use canonical ID
    
    # ✅ SAVE ACTIVE DOCUMENT (So the chatbot knows what to look at)
    save_active_document(
        session_id=job.session_id,
        company_document_id=final_metadata["company_document_id"],
        revision_number=str(final_metadata["revision_number"]), # Ensure string
    )

    # Emit UI unblock event
    print(
        "__UI_EVENT__"
        + json.dumps(
            metadata_confirmed_event("Document committed successfully")
        )
    )

    return MetadataUpdateResponse(
        job_id=job.job_id,
        company_document_id=final_metadata["company_document_id"],
        revision_number=str(final_metadata["revision_number"]),
        status="committed",
    )