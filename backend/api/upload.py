# backend/api/upload.py

import shutil
import uuid
from pathlib import Path
from typing import Optional, Dict, List

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from pydantic import BaseModel

from backend.rag.pipeline import run_pipeline
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)
from backend.rag.preprocessor_registry import (
    DEFAULT_RAG_PREPROCESSOR,
    normalize_rag_preprocessor,
)
from backend.state.job_state import (
    create_job,
    get_job_state,
    get_active_document,
    set_job_progress,
)
from backend.state.dev_settings import get_dev_settings
from backend.state.job_persistence import (
    get_job_run,
    get_latest_job_run_for_session,
)
from backend.rag.commit_worker import start_commit_job
from backend.storage.minio_outbox import enqueue_minio_upload

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


class UploadStatusResponse(BaseModel):
    job_id: Optional[str] = None
    session_id: Optional[str] = None
    status: str
    ready: bool
    message: str
    error: Optional[str] = None
    progress: Optional[int] = None
    progress_label: Optional[str] = None
    active_document: Optional[Dict[str, str]] = None


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
    rag_preprocessor: Optional[str] = Form(default=None),
    rag_collection_name: Optional[str] = Form(default=None),
):
    # --- LOG START ---
    print(f"\n------------------------------------------------")
    print(f"[PHASE 1] Receiving upload: {file.filename}")
    print(f"------------------------------------------------")

    if not session_id or not session_id.strip():
        raise HTTPException(400, "session_id is required")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    try:
        settings = get_dev_settings()
    except Exception:
        settings = {}
    resolved_rag_preprocessor = normalize_rag_preprocessor(
        rag_preprocessor or settings.get("rag_preprocessor") or DEFAULT_RAG_PREPROCESSOR
    )
    resolved_rag_collection_name = normalize_collection_name(
        rag_collection_name or settings.get("rag_collection_name") or DEFAULT_RAG_COLLECTION_NAME
    )

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
        print(f"[PHASE 1] File saved locally: {pdf_path}")
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
                "rag_preprocessor": resolved_rag_preprocessor,
                "rag_collection_name": resolved_rag_collection_name,
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
        raise HTTPException(500, f"Metadata extraction failed: {e}")


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
            "rag_preprocessor": resolved_rag_preprocessor,
            "rag_collection_name": resolved_rag_collection_name,
        },
        missing_fields=missing,
    )
    # Do NOT mark READY here.
    # Phase 1 only extracts metadata; Phase 2 (commit) performs chunking/indexing.


    # If missing is NOT empty, frontend will show the form
    next_action = "WAIT_FOR_METADATA" if missing else "READY_TO_COMMIT"
    print(f"[PHASE 1] Decision: {next_action}")

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
    print("\n------------------------------------------------")
    print(f"[PHASE 2] Committing Job: {payload.job_id}")
    print("------------------------------------------------")

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
        raise HTTPException(404, "Invalid job_id")

    if job.status != "PROCESSING":
        raise HTTPException(
            400,
            f"Job not ready for commit (state={job.status})"
        )

    # Only block if NOT forced.
    if job.missing_fields and not payload.force:
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
    required_keys = [
        "pdf_path",
        "company_document_id",
        "revision_number",
        "source_file",
        "db_connection",
    ]
    missing_required = [k for k in required_keys if not final_metadata.get(k)]
    if missing_required:
        raise HTTPException(500, f"Missing required metadata fields: {missing_required}")

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
    set_job_progress(
        job.job_id,
        value=40,
        label="Queued for background processing.",
    )
    print(
        f"[PHASE 2] Background commit queued | outbox_id={outbox_id} "
        f"| worker_started={started}"
    )

    return CommitResponse(
        job_id=payload.job_id,
        company_document_id=final_metadata["company_document_id"],
        revision_number=str(final_metadata["revision_number"]),
        status="processing",
    )


@router.get("/status", response_model=UploadStatusResponse)
def get_upload_status(
    *,
    job_id: Optional[str] = Query(
        default=None,
        description="Optional upload/ingestion job id.",
    ),
    session_id: Optional[str] = Query(
        default=None,
        description="Optional chat session id.",
    ),
):
    """
    Poll upload/ingestion state for UI auto-refresh.
    Resolves by job_id first, then by session_id.
    Falls back to persisted active document for READY state.
    """
    clean_job_id = (job_id or "").strip() or None
    clean_session_id = (session_id or "").strip() or None

    if not clean_job_id and not clean_session_id:
        raise HTTPException(400, "job_id or session_id is required")

    persisted = None
    if clean_job_id:
        try:
            persisted = get_job_run(clean_job_id)
        except Exception:
            persisted = None

    if not persisted and clean_session_id:
        try:
            persisted = get_latest_job_run_for_session(clean_session_id)
        except Exception:
            persisted = None

    # If this job id is gone but session has a different active job, report replacement.
    latest_for_session = None
    if clean_job_id and clean_session_id:
        try:
            current = get_job_run(clean_job_id)
        except Exception:
            current = None
        try:
            latest_for_session = get_latest_job_run_for_session(clean_session_id)
        except Exception:
            latest_for_session = None

    if clean_job_id and clean_session_id and not current:
        if latest_for_session and latest_for_session.get("job_id") != clean_job_id:
            return UploadStatusResponse(
                job_id=clean_job_id,
                session_id=clean_session_id,
                status="ERROR",
                ready=False,
                message="This ingestion job was replaced by a newer upload.",
                error="Replaced by new job",
                progress=0,
                progress_label="Replaced by new job.",
            )

    if persisted:
        status = str(persisted.get("status") or "").upper()
        status_message = {
            "WAIT_FOR_METADATA": "Waiting for metadata.",
            "PROCESSING": "Document processing is running in background.",
            "READY": "Document is ready.",
            "ERROR": "Document processing failed.",
        }.get(status, f"Job is in state: {status}")

        response_job_id = str(persisted.get("job_id") or clean_job_id or "")
        response_session_id = (
            str(persisted.get("session_id"))
            if persisted.get("session_id") is not None
            else clean_session_id
        )
        error_text = (
            str(persisted.get("error")).strip()
            if persisted.get("error") is not None
            else None
        )
        if not error_text:
            error_text = None

        return UploadStatusResponse(
            job_id=response_job_id or None,
            session_id=response_session_id,
            status=status,
            ready=status == "READY",
            message=status_message,
            error=error_text if status == "ERROR" else None,
            progress=int(persisted.get("progress") or 0),
            progress_label=(
                str(persisted.get("progress_label"))
                if persisted.get("progress_label") is not None
                else None
            ),
        )

    # Fallback to in-process memory for very early lifecycle edge cases.
    job = None
    if clean_job_id:
        job = get_job_state(clean_job_id)
    elif clean_session_id:
        job = get_job_state(clean_session_id)

    if job:
        status = str(job.status or "").upper()
        status_message = {
            "WAIT_FOR_METADATA": "Waiting for metadata.",
            "PROCESSING": "Document processing is running in background.",
            "READY": "Document is ready.",
            "ERROR": "Document processing failed.",
        }.get(status, f"Job is in state: {status}")

        return UploadStatusResponse(
            job_id=job.job_id,
            session_id=job.session_id,
            status=status,
            ready=status == "READY",
            message=status_message,
            error=job.error if status == "ERROR" else None,
            progress=int(getattr(job, "progress", 0) or 0),
            progress_label=getattr(job, "progress_label", None),
        )

    if clean_session_id:
        active_doc = get_active_document(clean_session_id)
        if active_doc:
            normalized_doc = {
                "company_document_id": str(
                    active_doc.get("company_document_id") or ""
                ),
                "revision_number": str(active_doc.get("revision_number") or ""),
                "filename": str(active_doc.get("filename") or ""),
            }
            return UploadStatusResponse(
                job_id=clean_job_id,
                session_id=clean_session_id,
                status="READY",
                ready=True,
                message="Document is ready.",
                progress=100,
                progress_label="Document ready.",
                active_document=normalized_doc,
            )

    return UploadStatusResponse(
        job_id=clean_job_id,
        session_id=clean_session_id,
        status="NOT_FOUND",
        ready=False,
        message="No active ingestion job found.",
        progress=0,
    )
