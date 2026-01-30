# backend/api/upload.py

import shutil
import uuid
from pathlib import Path
from typing import Optional, Dict, List

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

from backend.rag.pipeline import run_pipeline
from backend.state.job_state import (
    create_job,
    get_job_state,
    mark_job_ready,
)

#  Import MinIO Upload Function
from backend.storage.minio_client import upload_pdf as minio_upload_pdf

#  Import active document persistence
from backend.state.job_state import save_active_document

#  Import duplicate checker
from backend.rag.ingest import metadata_exists

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[1]  # backend/
TMP_DIR = BASE_DIR / "tmp" / "jobs"
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"

TMP_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DB = "postgresql+psycopg2://postgres:1@localhost:5432/rag_db"

# ============================================================
# HELPERS
# ============================================================

def generate_company_document_id(filename: str) -> str:
    base = filename.lower().strip()
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, base))


def resolve_next_revision_number(doc_dir: Path) -> int:
    if not doc_dir.exists():
        return 1

    revisions = [
        int(p.name[1:])
        for p in doc_dir.iterdir()
        if p.is_dir() and p.name.startswith("v") and p.name[1:].isdigit()
    ]

    return max(revisions) + 1 if revisions else 1


# ============================================================
# API ROUTER
# ============================================================

router = APIRouter(prefix="/upload", tags=["Upload"])

# ============================================================
# SCHEMAS
# ============================================================

class MetadataField(BaseModel):
    key: str
    value: Optional[str]
    confidence: Optional[float]


class UploadResponse(BaseModel):
    job_id: str
    company_document_id: str
    revision_number: int
    filename: str
    status: str
    metadata: Dict[str, MetadataField]
    missing_metadata: List[str]
    next_action: str  # WAIT_FOR_METADATA | READY_TO_COMMIT


class CommitRequest(BaseModel):
    job_id: str
    metadata: Dict[str, str]
    force: bool = False


class CommitResponse(BaseModel):
    job_id: str
    company_document_id: str
    revision_number: str 
    status: str


CONFIDENCE_THRESHOLD = 0.6


# ============================================================
# PHASE 1 — UPLOAD + METADATA EXTRACTION ONLY
# ============================================================

@router.post("/", response_model=UploadResponse)
def upload_pdf(
    *,
    file: UploadFile = File(...),
    session_id: str = Form(...),
    db_connection: Optional[str] = Form(DEFAULT_DB),
):
    # --- LOG START ---
    print(f"\n------------------------------------------------")
    print(f"📥 [PHASE 1] Receiving Upload: {file.filename}")
    print(f"------------------------------------------------")

    if not session_id or not session_id.strip():
        raise HTTPException(400, "session_id is required")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    job_id = str(uuid.uuid4())
    company_document_id = generate_company_document_id(file.filename)

    doc_dir = UPLOAD_DIR / company_document_id
    revision_number = resolve_next_revision_number(doc_dir)

    revision_dir = doc_dir / f"v{revision_number}"
    revision_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = revision_dir / file.filename

    job_dir = TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # SAVE PDF LOCALLY
    # --------------------------------------------------------
    try:
        with pdf_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        print(f"💾 [PHASE 1] File saved locally: {pdf_path}")
    except Exception as e:
        print(f" [PHASE 1] Save Failed: {e}")
        raise HTTPException(500, f"Failed to save PDF: {e}")


    # --------------------------------------------------------
    # METADATA-ONLY PIPELINE (PHASE 1)
    # --------------------------------------------------------

    metadata: Dict[str, MetadataField] = {}
    missing: List[str] = []

    try:
        for event in run_pipeline(
            pdf_path=str(pdf_path),
            job_dir=str(job_dir),
            company_document_id=company_document_id,
            extra_metadata={
                "company_document_id": company_document_id,
                "revision_number": str(revision_number),
                "source_file": file.filename,
            },
            mode="metadata",
        ):
            # We only care about metadata extraction result
            if isinstance(event, dict) and event.get("type") == "REQUEST_METADATA":
                for field in event["fields"]:
                    key = field["key"]
                    value = field.get("value")
                    confidence = field.get("confidence")

                    metadata[key] = MetadataField(
                        key=key,
                        value=value,
                        confidence=confidence,
                    )

                    # 🔥 CONFIDENCE → MISSING LOGIC
                    if not value or confidence is None or confidence < CONFIDENCE_THRESHOLD:
                        missing.append(key)

    except Exception as e:
        print(f"[PHASE 1] Metadata extraction failed: {e}")
        raise HTTPException(500, "Metadata extraction failed")


    # --------------------------------------------------------
    # 🔥 DUPLICATE CHECK (Force Popup if Exists)
    # --------------------------------------------------------
    
    # Even if the AI is 100% sure, we check if this specific version exists in DB
    is_duplicate = metadata_exists(
        connection_string=db_connection,
        metadata={
            "company_document_id": company_document_id,
            "revision_number": str(revision_number)
        }
    )

    if is_duplicate:
        print(f"[PHASE 1] Duplicate detected! Forcing metadata popup.")
        # Trigger popup by flagging a field as 'missing' even if it isn't
        if "revision_code" not in missing:
            missing.append("revision_code")

    # Create Job State
    create_job(
        job_id=job_id,
        session_id=session_id,
        metadata={
            "company_document_id": company_document_id,
            "revision_number": str(revision_number),
            "source_file": file.filename,
            "pdf_path": str(pdf_path),
            "db_connection": db_connection,
        },
        missing_fields=missing,
    )

    # If missing is NOT empty, frontend will show the form
    next_action = "WAIT_FOR_METADATA" if missing else "READY_FOR_PROCESSING"
    print(f"👉 [PHASE 1] Decision: {next_action}")

    return UploadResponse(
        job_id=job_id,
        company_document_id=company_document_id,
        revision_number=revision_number,
        filename=file.filename,
        status="uploaded",
        metadata=metadata,
        missing_metadata=missing,
        next_action=next_action,
    )


# ============================================================
# PHASE 2 — COMMIT (CHUNK + STORE + MINIO)
# ============================================================

@router.post("/commit", response_model=CommitResponse)
def commit_upload(payload: CommitRequest):
    # --- LOG START ---
    print(f"\n------------------------------------------------")
    print(f"🚀 [PHASE 2] Committing Job: {payload.job_id}")
    print(f"------------------------------------------------")

    job = get_job_state(payload.job_id)
    if not job:
        raise HTTPException(404, "Invalid job_id")

    if job.status != "PROCESSING":
        raise HTTPException(
            400,
            f"Job not ready for commit (state={job.status})"
        )


    # Only block if NOT forced
    if job.missing_fields and not payload.force:
        # Check if user actually provided the missing fields in payload
        # If they filled everything, we can proceed
        filled_keys = set(payload.metadata.keys())
        still_missing = [f for f in job.missing_fields if f not in filled_keys]
        
        if still_missing:
            raise HTTPException(
                400,
                f"Missing metadata fields: {still_missing}",
            )

    forbidden = {"company_document_id", "revision_number"}
    if forbidden & payload.metadata.keys():
        raise HTTPException(
            400,
            "company_document_id and revision_number cannot be overridden",
        )



    job.metadata.update(payload.metadata)
    job.missing_fields = []

    final_metadata = {
        **job.metadata,
        **payload.metadata,
    }
    

    # --------------------------------------------------------
    # 1. UPLOAD TO MINIO
    # --------------------------------------------------------
    try:
        print(f"☁️  [MINIO] Uploading: {final_metadata['source_file']} ...")
        
        rev_val = final_metadata["revision_number"]
        rev_int = int(rev_val) if str(rev_val).isdigit() else 1

        minio_path = minio_upload_pdf(
            local_path=final_metadata["pdf_path"],
            document_id=final_metadata["company_document_id"],
            revision=rev_int,
            filename=final_metadata["source_file"],
            overwrite=True
        )
        print(f"[MINIO] Upload Success! Path: {minio_path}")
    except Exception as e:
        print(f"[MINIO] Upload Failed: {e}")
        raise HTTPException(500, f"MinIO Backup Failed: {e}")

    # --------------------------------------------------------
    # 2. RUN PIPELINE (CHUNKING & DB)
    # --------------------------------------------------------
    try:
        print(f"⚙️  [RAG] Starting Chunking & Embedding...")
        for _ in run_pipeline(
            pdf_path=final_metadata["pdf_path"],
            job_dir=str(TMP_DIR / payload.job_id),
            company_document_id=final_metadata["company_document_id"],
            db_connection=final_metadata["db_connection"],
            extra_metadata=final_metadata,
            mode="commit",
        ):
            pass
        print(f" [RAG] Pipeline Complete. Chunks saved to DB.")
    except Exception as e:
        print(f" [RAG] Pipeline Failed: {e}")
        raise HTTPException(500, f"Commit failed: {e}")

    #  MARK JOB READY
    mark_job_ready(payload.job_id)

    #  SAVE ACTIVE DOC
    save_active_document(
        session_id=job.session_id,
        company_document_id=final_metadata["company_document_id"],
        revision_number=str(final_metadata["revision_number"]),
        filename=final_metadata.get("source_file"),
    )


    return CommitResponse(
        job_id=payload.job_id,
        company_document_id=final_metadata["company_document_id"],
        revision_number=str(final_metadata["revision_number"]),
        status="committed",
    )