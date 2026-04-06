# backend/api/upload.py

import shutil
import uuid
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Response
from pydantic import BaseModel
from pdf2image import convert_from_bytes
from PIL import ImageDraw

from backend.rag.pipeline import run_pipeline
from backend.rag.preview import (
    build_preprocessing_page_preview,
    build_preprocessing_preview,
)
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)
from backend.rag.mode_profiles import normalize_rag_mode
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


def _resolve_job_payload(job_id: str):
    job = get_job_state(job_id)
    if job:
        return job

    persisted = get_job_run(job_id)
    if persisted:
        return type(
            "PersistedJob",
            (),
            {
                "job_id": str(persisted.get("job_id") or job_id),
                "session_id": persisted.get("session_id"),
                "status": str(persisted.get("status") or "PROCESSING"),
                "metadata": dict(persisted.get("metadata") or {}),
                "missing_fields": list(persisted.get("missing_fields") or []),
            },
        )()

    return None


def _get_preview_context(job_id: str):
    job = _resolve_job_payload(job_id)
    if not job:
        raise HTTPException(404, "Invalid job_id")

    metadata = dict(job.metadata or {})
    pdf_path = str(metadata.get("pdf_path") or "").strip()
    if not pdf_path:
        raise HTTPException(500, "Upload job is missing local PDF path")

    pdf_file = Path(pdf_path).resolve()
    upload_root = UPLOAD_DIR.resolve()
    try:
        pdf_file.relative_to(upload_root)
    except Exception as exc:
        raise HTTPException(400, "Preview path is outside the upload workspace") from exc

    if not pdf_file.exists():
        raise HTTPException(404, "Local PDF file not found for preview")

    company_document_id = str(metadata.get("company_document_id") or "").strip()
    if not company_document_id:
        raise HTTPException(500, "Upload job is missing company_document_id")

    return job, metadata, pdf_file


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _parse_bbox_payload(raw_bbox: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw_bbox or raw_bbox in {"null", ""}:
        return None

    try:
        payload = json.loads(raw_bbox)
    except Exception:
        return None

    if isinstance(payload, list):
        return {"points": payload}

    if isinstance(payload, dict):
        points = payload.get("points")
        if isinstance(points, list):
            return payload

        bbox = payload.get("bbox")
        if isinstance(bbox, list):
            return {"points": bbox}

    return None


def _scale_bbox_points(
    payload: Dict[str, Any],
    *,
    image_size: Tuple[int, int],
    dpi: int,
) -> List[Tuple[float, float]]:
    raw_points = payload.get("points")
    if not isinstance(raw_points, list):
        return []

    parsed_points: List[Tuple[float, float]] = []
    for point in raw_points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        x = _coerce_float(point[0])
        y = _coerce_float(point[1])
        if x is None or y is None:
            continue
        parsed_points.append((x, y))

    if len(parsed_points) < 3:
        return []

    image_width, image_height = image_size
    layout_width = _coerce_float(payload.get("layout_width"))
    layout_height = _coerce_float(payload.get("layout_height"))

    if layout_width and layout_height:
        scale_x = image_width / layout_width
        scale_y = image_height / layout_height
    else:
        max_x = max(x for x, _ in parsed_points)
        max_y = max(y for _, y in parsed_points)
        if max_x <= image_width * 1.2 and max_y <= image_height * 1.2:
            scale_x = 1.0
            scale_y = 1.0
        else:
            scale_x = dpi / 72.0
            scale_y = dpi / 72.0

    scaled_points: List[Tuple[float, float]] = []
    for x, y in parsed_points:
        scaled_x = min(max(x * scale_x, 0.0), float(image_width))
        scaled_y = min(max(y * scale_y, 0.0), float(image_height))
        scaled_points.append((scaled_x, scaled_y))
    return scaled_points


def _bbox_rect(
    points: List[Tuple[float, float]],
    *,
    image_size: Tuple[int, int],
    padding: int = 20,
) -> Optional[Tuple[int, int, int, int]]:
    if len(points) < 3:
        return None

    image_width, image_height = image_size
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]

    left = max(int(min(xs)) - padding, 0)
    top = max(int(min(ys)) - padding, 0)
    right = min(int(max(xs)) + padding, image_width)
    bottom = min(int(max(ys)) + padding, image_height)

    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


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


class PreprocessingPreviewResponse(BaseModel):
    job_id: str
    pdf_path: str
    company_document_id: str
    revision_number: str
    source_file: str
    metadata_candidates: Dict[str, Dict[str, object]]
    metadata_evidence: List[Dict[str, object]]
    tables: List[Dict[str, object]]
    chunks: List[Dict[str, object]]
    removed_elements: List[Dict[str, object]]
    summary: Dict[str, object]


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
    resolved_rag_mode = normalize_rag_mode(
        settings.get("rag_ingest_mode") or settings.get("rag_mode")
    )
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
                "rag_ingest_mode": resolved_rag_mode,
                "rag_preprocessor": resolved_rag_preprocessor,
                "rag_collection_name": resolved_rag_collection_name,
                "enable_fast_document_processing": bool(
                    settings.get("enable_fast_document_processing", False)
                ),
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
            "rag_ingest_mode": resolved_rag_mode,
            "rag_preprocessor": resolved_rag_preprocessor,
            "rag_collection_name": resolved_rag_collection_name,
            "enable_fast_document_processing": bool(
                settings.get("enable_fast_document_processing", False)
            ),
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


@router.get("/preview")
def get_preprocessing_preview(
    *,
    job_id: str = Query(..., description="Upload job id"),
    scope: str = Query(default="auto", description="Preview scope: auto, quick, or full"),
):
    job, metadata, pdf_file = _get_preview_context(job_id)
    job_dir = TMP_DIR / job.job_id

    try:
        preview = build_preprocessing_preview(
            pdf_path=str(pdf_file),
            job_dir=str(job_dir),
            company_document_id=str(metadata.get("company_document_id") or ""),
            extra_metadata=metadata,
            preprocessor=metadata.get("rag_preprocessor"),
            scope=str(scope or "auto").strip().lower() or "auto",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to build preprocessing preview: {exc}") from exc

    return preview


@router.get("/preview/page-data")
def get_preprocessing_preview_page_data(
    *,
    job_id: str = Query(..., description="Upload job id"),
    page: int = Query(..., ge=1, description="1-based page number"),
    scope: str = Query(default="auto", description="Preview scope: auto, quick, or full"),
):
    job, metadata, pdf_file = _get_preview_context(job_id)
    job_dir = TMP_DIR / job.job_id

    try:
        preview = build_preprocessing_page_preview(
            pdf_path=str(pdf_file),
            job_dir=str(job_dir),
            company_document_id=str(metadata.get("company_document_id") or ""),
            extra_metadata=metadata,
            page_number=int(page),
            preprocessor=metadata.get("rag_preprocessor"),
            scope=str(scope or "auto").strip().lower() or "auto",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to build page preview: {exc}") from exc

    return preview


@router.get("/preview/page")
def render_preprocessing_preview_page(
    *,
    job_id: str = Query(..., description="Upload job id"),
    page: int = Query(1, ge=1, description="1-based page number"),
    bbox: Optional[str] = Query(None, description="Optional JSON bbox polygon"),
    crop: bool = Query(False, description="Crop image to the bbox instead of the full page"),
):
    _job, _metadata, pdf_file = _get_preview_context(job_id)
    dpi = 150

    try:
        pdf_bytes = pdf_file.read_bytes()
    except Exception as exc:
        raise HTTPException(500, f"Failed to read local preview PDF: {exc}") from exc

    try:
        images = convert_from_bytes(
            pdf_bytes,
            first_page=page,
            last_page=page,
            fmt="png",
            dpi=dpi,
        )
        if not images:
            raise ValueError("Page out of range")
        image = images[0]
    except Exception as exc:
        raise HTTPException(500, f"Failed to render preview page: {exc}") from exc

    bbox_payload = _parse_bbox_payload(bbox)
    scaled_points = (
        _scale_bbox_points(bbox_payload, image_size=image.size, dpi=dpi)
        if bbox_payload
        else []
    )

    if scaled_points:
        if crop:
            rect = _bbox_rect(scaled_points, image_size=image.size)
            if rect:
                left, top, right, bottom = rect
                image = image.crop((left, top, right, bottom))
                scaled_points = [(x - left, y - top) for x, y in scaled_points]

        try:
            draw = ImageDraw.Draw(image, "RGBA")
            draw.polygon(
                scaled_points,
                outline="red",
                width=5,
                fill=(255, 0, 0, 40),
            )
        except Exception:
            pass

    img_bytes = io.BytesIO()
    image.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    return Response(content=img_bytes.getvalue(), media_type="image/png")


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
