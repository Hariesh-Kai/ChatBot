# backend/rag/pipeline.py

from pathlib import Path
from typing import Dict, Any, List, Literal, Optional, Generator
import json


from langchain_core.documents import Document

from backend.memory.redis_memory import clear_used_chunk_ids
#  Import the streaming preprocessor
from backend.rag.preprocess import stream_pdf_to_elements
from backend.rag.chunk import ContextAwareChunker
from backend.rag.metadata import (
    extract_document_metadata,
    enrich_chunks,
)
from backend.rag.ingest import (
    ingest_to_pgvector,
    load_documents,
)
from backend.contracts.ui_events import progress_event


# ============================================================
# PIPELINE MODES
# ============================================================

PipelineMode = Literal["metadata", "commit"]


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
    def emit_progress(value: int, label: str):
        yield progress_event(value=value, label=label)

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
    if mode == "metadata":
        elements_path = job_dir / "page1_preview.json"
    else:
        elements_path = job_dir / "filtered_elements.json"

    chunks_path = job_dir / "chunks.json"
    enriched_path = job_dir / "enriched_chunks.json"

    # --------------------------------------------------
    # 1️⃣ PDF → ELEMENTS (STREAMING MODE)
    # --------------------------------------------------

    if not elements_path.exists():
        print(f"Parsing PDF in Streaming Mode (Mode={mode})...")
        yield from emit_progress(5, "Reading PDF pages…")
        all_elements = []
        
        # Consume the generator page-by-page
        print("[PIPELINE] About to call stream_pdf_to_elements()")
        for batch in stream_pdf_to_elements(pdf_path, str(elements_path)):
            print(f"[PIPELINE] Preprocess batch received | elements={len(batch)}")
            all_elements.extend(batch)
            
            # 🔥 CRITICAL OPTIMIZATION: 
            # If we only need metadata, we STOP after the first batch (Page 1).
            # This saves massive time/compute by not OCR-ing the rest of the doc.
            if mode == "metadata":
                print("[PIPELINE] Metadata mode → stopping after page 1")
                yield from emit_progress(15, "Metadata extracted (Page 1)")
                yield from emit_progress(20, "Metadata ready")
                print("[PIPELINE] Metadata extraction: Stopping OCR after Page 1.")
                break

        # Save the JSON (Partial or Full)
        with open(elements_path, "w", encoding="utf-8") as f:
            json.dump(all_elements, f, indent=2)
            
        print(f"Extracted {len(all_elements)} elements.")
        print(f"[PIPELINE] Elements written to {elements_path}")
        print(f"[PIPELINE] Total elements count = {len(all_elements)}")
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

        # 🔥 CRITICAL: Emit metadata to caller (upload.py)
        yield {
            "type": "REQUEST_METADATA",
            "fields": [
                {
                    "key": "document_type",
                    "label": "Document Type",
                    "placeholder": "e.g. Specification, Report",
                    "value": metadata.get("document_type", {}).get("value"),
                    "confidence": metadata.get("document_type", {}).get("confidence"),
                },
                {
                    "key": "revision_code",
                    "label": "Revision Code",
                    "placeholder": "e.g. A, B, 01",
                    "value": metadata.get("revision_code", {}).get("value"),
                    "confidence": metadata.get("revision_code", {}).get("confidence"),
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

    chunker = ContextAwareChunker()
    yield from emit_progress(30, "Chunking document…")

    chunker.process(
        input_file=str(elements_path),
        output_file=str(chunks_path),
    )

    if not chunks_path.exists():
        raise RuntimeError("Chunking failed: chunks.json not created")

    # --------------------------------------------------
    # 3️⃣ METADATA ENRICHMENT (AUTHORITATIVE)
    # --------------------------------------------------
    yield from emit_progress(45, "Enriching chunks with metadata…")
    enrich_chunks(
        chunks_file=str(chunks_path),
        output_file=str(enriched_path),
        pdf_path=pdf_path,
        company_document_id=company_document_id,
        extra_metadata=extra_metadata,
    )

    if not enriched_path.exists():
        raise RuntimeError("Metadata enrichment failed")

    # --------------------------------------------------
    # 4️⃣ LOAD DOCUMENTS (STRICT)
    # --------------------------------------------------
    yield from emit_progress(60, "Preparing chunks for indexing…")
    documents: List[Document] = load_documents(
        json_path=str(enriched_path)
    )
    

    if not documents:
        raise RuntimeError("No documents loaded for ingestion")

    # --------------------------------------------------
    # 5️⃣ INGEST INTO VECTOR DB (REVISION-SAFE)
    # --------------------------------------------------
    yield from emit_progress(80, "Indexing into vector database…")
    ingest_to_pgvector(
        documents=documents,
        connection_string=db_connection,
        company_document_id=company_document_id,
        revision_number=revision_number,
    )
    

    # --------------------------------------------------
    # 6️⃣ RESET RAG SESSION STATE
    # --------------------------------------------------

    session_id = extra_metadata.get("session_id")
    if session_id:
        clear_used_chunk_ids(session_id)
        yield from emit_progress(95, "Finalizing document index…")

    # --------------------------------------------------
    # 7️⃣ RESULT
    # --------------------------------------------------
    yield from emit_progress(100, "Document ready for querying")

    

    return