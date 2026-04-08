# backend/rag/pipeline.py

from pathlib import Path
from typing import Dict, Any, List, Literal, Optional, Generator
import json


from langchain_core.documents import Document

from backend.memory.redis_memory import clear_used_chunk_ids
#  Import the streaming preprocessor
from backend.rag.preprocess import stream_pdf_to_elements
from backend.rag.mode_profiles import DEFAULT_RAG_MODE, normalize_rag_mode
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)
from backend.rag.preprocessor_registry import DEFAULT_RAG_PREPROCESSOR, normalize_rag_preprocessor
from backend.rag.chunk import ContextAwareChunker
from backend.rag.chunk_strategy import get_chunk_config
from backend.rag.filtering import FILTER_VERSION, filter_element_dicts
from backend.rag.metadata import (
    extract_document_metadata,
    enrich_chunks,
)
from backend.rag.ingest import (
    ingest_to_pgvector,
    load_documents,
)
from backend.rag.upload_cancellation import (
    is_upload_cancel_requested,
    raise_if_upload_cancel_requested,
)
from backend.contracts.ui_events import progress_event
from backend.state.dev_settings import get_dev_settings


# ============================================================
# PIPELINE MODES
# ============================================================

PipelineMode = Literal["metadata", "preview", "commit"]


def _load_chunk_content_sample(elements_path: Path, *, max_chars: int = 1500) -> str:
    """Load a small text sample so chunk strategy can detect document type."""
    try:
        raw = json.loads(elements_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    parts: List[str] = []
    total_chars = 0

    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        parts.append(text)
        total_chars += len(text)
        if total_chars >= max_chars:
            break

    return " ".join(parts)[:max_chars]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _filter_report_version(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0

    try:
        return int(payload.get("filter_version") or 0)
    except Exception:
        return 0


def _check_cancel(extra_metadata: Dict[str, Any]) -> None:
    raise_if_upload_cancel_requested(
        job_id=str(extra_metadata.get("job_id") or "").strip() or None,
        session_id=str(extra_metadata.get("session_id") or "").strip() or None,
    )


# ============================================================
# MAIN PIPELINE
# ============================================================

def run_pipeline(
    *,
    pdf_path: str,
    job_dir: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
    db_connection: Optional[str] = None,
    mode: PipelineMode = "commit",
    rag_mode: Optional[str] = None,
    preprocessor: Optional[str] = None,
    collection_name: Optional[str] = None,
) -> Generator[dict, None, None]:
    """
    Enterprise RAG ingestion pipeline (OPTIMIZED).

    MODES
    ─────────────────────────────────────
    metadata → extract metadata ONLY (Page 1 scan)
    commit   → chunk + enrich + ingest (Full document)

    OPTIMIZATION:
    - Metadata mode STOPS OCR after Page 1.
    - Metadata mode uses a separate 'page1_preview.json' to avoid corrupting the full cache.
    """

    # --------------------------------------------------
    # INIT
    # --------------------------------------------------
    print(f"[PIPELINE] run_pipeline called | mode={mode}")
    print(f"[PIPELINE] pdf_path={pdf_path}")
    print(f"[PIPELINE] job_dir={job_dir}")
    try:
        settings = get_dev_settings()
    except Exception:
        settings = {}
    fast_document_processing = bool(
        extra_metadata.get("enable_fast_document_processing")
        if extra_metadata.get("enable_fast_document_processing") is not None
        else settings.get("enable_fast_document_processing", False)
    )
    resolved_rag_mode = normalize_rag_mode(
        rag_mode
        or extra_metadata.get("rag_ingest_mode")
        or extra_metadata.get("rag_mode")
        or (DEFAULT_RAG_MODE if fast_document_processing else "high_fidelity")
    )
    resolved_preprocessor = normalize_rag_preprocessor(
        preprocessor
        or extra_metadata.get("rag_preprocessor")
        or (DEFAULT_RAG_PREPROCESSOR if fast_document_processing else "unstructured")
    )
    resolved_collection_name = normalize_collection_name(
        collection_name
        or extra_metadata.get("rag_collection_name")
        or settings.get("rag_collection_name")
        or DEFAULT_RAG_COLLECTION_NAME
    )
    print(f"[PIPELINE] rag_ingest_mode={resolved_rag_mode}")
    print(f"[PIPELINE] rag_preprocessor={resolved_preprocessor}")
    print(f"[PIPELINE] rag_collection_name={resolved_collection_name}")

    # Normalize retrieval-quality metadata early so later chunk/enrichment
    # stages can score chunks even when they are loaded from an existing cache.
    extra_metadata = dict(extra_metadata or {})
    extra_metadata.setdefault("enable_fast_document_processing", fast_document_processing)
    extra_metadata.setdefault("rag_preprocessor", resolved_preprocessor)
    extra_metadata.setdefault("rag_ingest_mode", resolved_rag_mode)


    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    #  FIX: Treat revision as String (TEXT) to support "05", "A", etc.
    revision_number = str(extra_metadata.get("revision_number", ""))
    source_file = extra_metadata.get("source_file")

    if not revision_number:
        raise RuntimeError("extra_metadata.revision_number is required")

    if not source_file:
        raise RuntimeError("extra_metadata.source_file is required")

    # --------------------------------------------------
    # PATHS & MODE HANDLING
    # --------------------------------------------------

    #  OPTIMIZATION: Separate cache for Preview vs Full Ingest
    # This ensures we don't accidentally treat a partial Page 1 scan as the full document later.
    preview_elements_path = job_dir / "page1_preview.json"
    raw_elements_path = job_dir / "raw_elements.json"
    filtered_elements_path = job_dir / "filtered_elements.json"
    removed_elements_path = job_dir / "removed_elements.json"
    filter_report_path = job_dir / "filter_report.json"
    chunks_path = job_dir / "chunks.json"
    enriched_path = job_dir / "enriched_chunks.json"

    if mode == "metadata":
        elements_path = preview_elements_path
    else:
        elements_path = raw_elements_path

    # --------------------------------------------------
    # 1️⃣ PDF → ELEMENTS (STREAMING MODE)
    # --------------------------------------------------

    if not elements_path.exists():
        _check_cancel(extra_metadata)
        print(f"Parsing PDF in Streaming Mode (Mode={mode})...")
        yield  progress_event(value=5, label="Reading PDF pages…")
        all_elements = []
        
        # Consume the generator page-by-page
        print("[PIPELINE] About to call stream_pdf_to_elements()")
        for batch in stream_pdf_to_elements(
            pdf_path,
            str(elements_path),
            rag_mode=resolved_rag_mode,
            pipeline_mode=mode,
            preprocessor=resolved_preprocessor,
        ):
            _check_cancel(extra_metadata)
            print(f"[PIPELINE] Preprocess batch received | elements={len(batch)}")
            all_elements.extend(batch)
            
            # Metadata mode: stop as soon as we have at least one parsed element.
            # This preserves the fast path but avoids empty page-1 edge cases.
            if mode == "metadata":
                if all_elements:
                    print("[PIPELINE] Metadata mode -> parsed preview ready")
                    yield progress_event(value=15, label="Metadata extracted (preview)")
                    yield  progress_event(value=20, label="Metadata ready")
                    print("[PIPELINE] Metadata extraction: Stopping after preview parse.")
                    break
                print("[PIPELINE] Metadata mode -> empty batch, checking next page")

        if not all_elements:
            raise RuntimeError(
                "Preprocess returned no parsable elements. "
                "Check PDF quality or unstructured dependencies."
            )

        # Save the JSON (Partial or Full)
        with open(elements_path, "w", encoding="utf-8") as f:
            json.dump(all_elements, f, indent=2)
            
        print(f"Extracted {len(all_elements)} elements.")
        print(f"[PIPELINE] Elements written to {elements_path}")
        print(f"[PIPELINE] Total elements count = {len(all_elements)}")
    if not elements_path.exists():
        raise RuntimeError(f"Preprocess failed: {elements_path.name} not created")

    existing_filter_version = _filter_report_version(filter_report_path)
    if mode != "metadata" and (
        not filtered_elements_path.exists()
        or not removed_elements_path.exists()
        or not filter_report_path.exists()
        or existing_filter_version != FILTER_VERSION
    ):
        _check_cancel(extra_metadata)
        try:
            raw_elements = json.loads(raw_elements_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Failed to read raw preprocessing artifacts: {exc}") from exc

        yield progress_event(
            value=22,
            label="Removing headers, footers, image placeholders, and boilerplateâ€¦",
        )
        filter_result = filter_element_dicts(raw_elements if isinstance(raw_elements, list) else [])
        _check_cancel(extra_metadata)
        _write_json(filtered_elements_path, filter_result["filtered_elements"])
        _write_json(removed_elements_path, filter_result["removed_elements"])
        _write_json(filter_report_path, filter_result["summary"])
        print(
            "[PIPELINE] Filtering complete | "
            f"kept={len(filter_result['filtered_elements'])} "
            f"removed={len(filter_result['removed_elements'])}"
        )

    if mode != "metadata":
        elements_path = filtered_elements_path
        if not elements_path.exists():
            raise RuntimeError(f"Preprocess failed: {elements_path.name} not created")

    # ==================================================
    # 🔹 MODE: METADATA ONLY (NO CHUNKS, NO DB)
    # ==================================================

    if mode == "metadata":
        metadata = extract_document_metadata(
            elements_file=str(elements_path),
            pdf_path=pdf_path,
            company_document_id=company_document_id,
            extra_metadata=extra_metadata,
        )

        doc_type = (
            metadata.get("document_type", {}).get("value")
            or metadata.get("document_title", {}).get("value")
        )
        doc_type_confidence = (
            metadata.get("document_type", {}).get("confidence")
            or metadata.get("document_title", {}).get("confidence")
        )

        # 🔥 CRITICAL: Emit metadata to caller (upload.py)
        yield {
            "type": "REQUEST_METADATA",
            "fields": [
                {
                    "key": "document_type",
                    "label": "Document Type",
                    "placeholder": "e.g. Specification, Report",
                    "value": doc_type,
                    "confidence": doc_type_confidence,
                },
                {
                    "key": "revision_code",
                    "label": "Revision Code",
                    "placeholder": "e.g. A, B, 01",
                    "value": metadata.get("revision_code", {}).get("value"),
                    "confidence": metadata.get("revision_code", {}).get("confidence"),
                },
                {
                    "key": "document_number",
                    "label": "Document Number",
                    "placeholder": "e.g. 363010BGRB00508",
                    "value": metadata.get("document_number", {}).get("value"),
                    "confidence": metadata.get("document_number", {}).get("confidence"),
                },
                {
                    "key": "project_name",
                    "label": "Project Name",
                    "placeholder": "e.g. Agogo Integrated West Hub",
                    "value": metadata.get("project_name", {}).get("value"),
                    "confidence": metadata.get("project_name", {}).get("confidence"),
                },
            ],
        }

        return



    # ==================================================
    # 🔹 MODE: COMMIT (FULL INGEST)
    # ==================================================

    if not db_connection:
        raise RuntimeError("db_connection is required in commit mode")

    # --------------------------------------------------
    # 2️⃣ CONTEXT-AWARE CHUNKING
    # --------------------------------------------------

    chunk_config = get_chunk_config(
        filename=str(source_file or pdf_path),
        document_title=str(
            extra_metadata.get("document_title")
            or extra_metadata.get("document_type")
            or ""
        ),
        content_sample=_load_chunk_content_sample(elements_path),
        metadata=extra_metadata,
    )
    print(
        "[PIPELINE] chunk_config="
        f"{chunk_config.doc_type} size={chunk_config.chunk_size} overlap={chunk_config.chunk_overlap}"
    )

    chunker = ContextAwareChunker(
        chunk_size=chunk_config.chunk_size,
        chunk_overlap=chunk_config.chunk_overlap,
    )
    _check_cancel(extra_metadata)
    yield  progress_event(value=30, label="Chunking document…")

    chunker.process(
        input_file=str(elements_path),
        output_file=str(chunks_path),
    )
    _check_cancel(extra_metadata)

    if not chunks_path.exists():
        raise RuntimeError("Chunking failed: chunks.json not created")

    # --------------------------------------------------
    # 3️⃣ METADATA ENRICHMENT (AUTHORITATIVE)
    # --------------------------------------------------
    yield progress_event(value=45, label="Enriching chunks with metadata…")
    _check_cancel(extra_metadata)
    enrich_chunks(
        chunks_file=str(chunks_path),
        output_file=str(enriched_path),
        pdf_path=pdf_path,
        company_document_id=company_document_id,
        extra_metadata=extra_metadata,
    )
    _check_cancel(extra_metadata)

    if not enriched_path.exists():
        raise RuntimeError("Metadata enrichment failed")

    # --------------------------------------------------
    # 4️⃣ LOAD DOCUMENTS (STRICT)
    # --------------------------------------------------
    yield progress_event(value=60, label="Preparing chunks for indexing…")
    _check_cancel(extra_metadata)
    documents: List[Document] = load_documents(
        json_path=str(enriched_path)
    )
    

    if not documents:
        raise RuntimeError("No documents loaded for ingestion")

    # --------------------------------------------------
    # 5️⃣ INGEST INTO VECTOR DB (REVISION-SAFE)
    # --------------------------------------------------
    yield progress_event(value=80, label="Embedding and indexing into vector database...")
    ingest_to_pgvector(
        documents=documents,
        connection_string=db_connection,
        company_document_id=company_document_id,
        revision_number=revision_number,
        collection_name=resolved_collection_name,
        replace_existing=bool(extra_metadata.get("replace_existing", False)),
        should_cancel=lambda: is_upload_cancel_requested(
            job_id=str(extra_metadata.get("job_id") or "").strip() or None,
            session_id=str(extra_metadata.get("session_id") or "").strip() or None,
        ),
    )
    

    # --------------------------------------------------
    # 6️⃣ RESET RAG SESSION STATE
    # --------------------------------------------------

    session_id = extra_metadata.get("session_id")
    if session_id:
        clear_used_chunk_ids(session_id)
        yield progress_event(value=95, label="Finalizing document index…")

    # --------------------------------------------------
    # 7️⃣ RESULT
    # --------------------------------------------------
    yield progress_event(value=100, label="Document ready for querying")

    

    return
