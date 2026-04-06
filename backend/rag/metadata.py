# backend/rag/metadata.py

import json
import sys
import hashlib
import tiktoken
import logging
import time
import re  #  Added for Regex patterns
from datetime import datetime
from typing import Dict, Any, List

# ============================================================
# TOKENIZER (STATS ONLY — NO MODEL USE)
# ============================================================

_tokenizer = None
_tokenizer_error = None
_tokenizer_warned = False


def _get_tokenizer():
    global _tokenizer, _tokenizer_error
    if _tokenizer is not None:
        return _tokenizer
    if _tokenizer_error is not None:
        return None
    try:
        _tokenizer = tiktoken.get_encoding("cl100k_base")
        return _tokenizer
    except Exception as exc:  # Offline / proxy-safe fallback
        _tokenizer_error = exc
        return None


def count_tokens(text: str) -> int:
    global _tokenizer_warned
    tokenizer = _get_tokenizer()
    if tokenizer is not None:
        return len(tokenizer.encode(text))

    # Fallback: approximate tokens to avoid hard failure in offline environments.
    if not _tokenizer_warned:
        _tokenizer_warned = True
        logging.getLogger("chatui.rag.metadata").warning(
            "tiktoken unavailable; using approximate token counts."
        )
    if not text:
        return 0
    # Rough heuristic: ~4 chars per token
    return max(1, int(len(text) / 4))


# ============================================================
# CHUNK ID (DETERMINISTIC, REVISION-SAFE)
# ============================================================

CHUNK_ID_VERSION = 2


def generate_chunk_id(
    company_document_id: str,
    revision_number: str,
    content: str,
    *,
    page_number: Any = None,
    chunk_type: str = "",
    section: str = "",
    parent_id: Any = None,
    doc_id: Any = None,
    table_row_index: Any = None,
    chunk_index: Any = None,
    occurrence_index: int = 0,
) -> str:
    """
    Deterministic, document-scoped chunk ID.

    Guarantees:
    - Stable across re-ingestion
    - No collision across documents or revisions
    """
    base = "::".join(
        [
            str(company_document_id or "").strip(),
            str(revision_number or "").strip(),
            str(page_number or "").strip(),
            str(chunk_type or "").strip(),
            str(section or "").strip(),
            str(parent_id or "").strip(),
            str(doc_id or "").strip(),
            str(table_row_index if table_row_index is not None else "").strip(),
            str(chunk_index if chunk_index is not None else "").strip(),
            str(int(occurrence_index or 0)),
            str(content or ""),
        ]
    )
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def _default_extraction_source(
    *,
    extra_metadata: Dict[str, Any],
) -> str:
    preprocessor = str(extra_metadata.get("rag_preprocessor") or "unstructured").strip().lower()
    rag_mode = str(extra_metadata.get("rag_ingest_mode") or "balanced").strip().lower()

    if preprocessor == "pymupdf4llm":
        return "pymupdf"
    if preprocessor == "docling":
        return "docling"
    if preprocessor == "pypdf_text":
        return "pypdf_text"
    if rag_mode == "fast":
        return "unstructured_fast"
    return "unstructured_hi_res"


def _score_chunk_quality(content: str) -> Dict[str, Any]:
    """
    Lightweight ingest-time quality signal used later by retrieval.
    """
    default = {"quality_score": 0.5, "quality_tier": "medium"}
    if not content:
        return default

    try:
        length = len(content)
        if length < 50:
            length_score = 0.1
        elif length < 200:
            length_score = 0.5
        elif length <= 1500:
            length_score = 1.0
        elif length <= 3000:
            length_score = 0.7
        else:
            length_score = 0.4

        alpha_chars = sum(1 for ch in content if ch.isalpha())
        density_score = min(alpha_chars / max(length, 1), 1.0)

        words = re.findall(r"[a-zA-Z]{4,}", content.lower())
        distinct_words = len(set(words))
        richness_score = min(distinct_words / 50.0, 1.0)

        overall = round(
            0.3 * length_score + 0.3 * density_score + 0.4 * richness_score,
            3,
        )
        if overall >= 0.70:
            tier = "high"
        elif overall >= 0.40:
            tier = "medium"
        else:
            tier = "low"

        return {"quality_score": overall, "quality_tier": tier}
    except Exception:
        return default


# ============================================================
# METADATA EXTRACTION (PHASE 1 — SMART HEURISTICS)
# ============================================================

def extract_document_metadata(
    *,
    elements_file: str,
    pdf_path: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Extract document-level metadata ONLY FROM THE FIRST PAGE.

     UPDATED: Uses Regex to distinguish Document ID vs Project Name.
    """

    with open(elements_file, "r", encoding="utf-8") as f:
        elements: List[Dict[str, Any]] = json.load(f)

    # Initialize with default confidence
    metadata = {
        # Keep both keys for backward compatibility.
        # `document_type` is what upload/pipeline contracts expect.
        "document_type": {"value": None, "confidence": 0.0},
        "document_title": {"value": None, "confidence": 0.0},
        "revision_code": {"value": None, "confidence": 0.0},
        "project_name":  {"value": None, "confidence": 0.0}, #  New field
        "document_number": {"value": None, "confidence": 0.0} #  New field
    }

    # --------------------------------------------------------
    #  SMART HEURISTICS (Page 1 Only)
    # --------------------------------------------------------

    for el in elements:
        # Check Page Number (Stop scanning after page 1 to save time/errors)
        page_number = el.get("metadata", {}).get("page_number", 1)
        if page_number > 1:
            break

        text = (el.get("text") or el.get("content") or "").strip()
        if not text:
            continue

        lower = text.lower()

        # --- 1. Detect Document Number (Technical ID) ---
        # Pattern: Long alphanumeric string (e.g., 363010BGRB00508 or with dashes)
        # Rule: Must contain digits, >8 chars, no spaces
        if len(text) > 8 and len(text) < 40 and any(c.isdigit() for c in text):
            # Check for ID-like structure (no spaces, mix of letters/numbers)
            if " " not in text and re.search(r'[A-Z0-9]+', text):
                if metadata["document_number"]["confidence"] < 0.8:
                    metadata["document_number"] = {"value": text, "confidence": 0.9}
                    continue # If it is an ID, it is not a title

        # --- 2. Detect Document Title ---
        # Capture the FULL text line if it contains title keywords
        if "basis of design" in lower:
            clean_title = text.replace("\n", " ").strip()
            if len(clean_title) > 10 and metadata["document_title"]["confidence"] < 0.9:
                metadata["document_title"] = {"value": clean_title, "confidence": 0.9}
                metadata["document_type"] = {"value": clean_title, "confidence": 0.9}
        
        elif "design basis" in lower and metadata["document_title"]["confidence"] < 0.8:
            clean_title = text.replace("\n", " ").strip()
            metadata["document_title"] = {"value": clean_title, "confidence": 0.8}
            metadata["document_type"] = {"value": clean_title, "confidence": 0.8}

        # --- 3. Detect Revision Code (Rev 01, Rev A) ---
        # Regex: Starts with 'Rev' followed by short alphanumeric
        rev_match = re.search(r'\brev\.?\s*([a-zA-Z0-9]{1,3})\b', lower)
        if rev_match:
            metadata["revision_code"] = {"value": rev_match.group(1).upper(), "confidence": 0.8}

        # --- 4. Detect Project Name ---
        # Rule: Contains "Project" or "Development", isn't an ID, isn't a whole paragraph
        if "project" in lower or "development" in lower or "field" in lower:
            if 10 < len(text) < 100:
                metadata["project_name"] = {"value": text, "confidence": 0.6}

    # --------------------------------------------------------
    # AUTHORITATIVE OVERRIDES (NON-IDENTITY ONLY)
    # --------------------------------------------------------

    if "revision_code" in extra_metadata:
        metadata["revision_code"] = {
            "value": extra_metadata["revision_code"],
            "confidence": 1.0,
        }

    # Map generic doc_type to title if title wasn't found automatically
    if "document_type" in extra_metadata:
        if not metadata["document_title"]["value"]:
            metadata["document_title"] = {
                "value": extra_metadata["document_type"],
                "confidence": 1.0,
            }
        metadata["document_type"] = {
            "value": extra_metadata["document_type"],
            "confidence": 1.0,
        }

    return metadata


# ============================================================
# CHUNK ENRICHMENT (PHASE 2 — AUTHORITATIVE)
# ============================================================

def enrich_chunks(
    *,
    chunks_file: str,
    output_file: str,
    pdf_path: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enrich chunk JSON with REQUIRED RAG metadata.
    """

    print(f"[METADATA] Enriching chunks from: {chunks_file}")

    with open(chunks_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    #  FIX: Treat revision as String (do not cast to int)
    revision_number = str(extra_metadata.get("revision_number", ""))
    revision_code = extra_metadata.get("revision_code")
    revision_date = extra_metadata.get("revision_date", int(time.time()))
    document_type = extra_metadata.get("document_type")
    source_file = extra_metadata.get("source_file")

    if not revision_number:
        raise RuntimeError("extra_metadata.revision_number is required")

    if not source_file:
        raise RuntimeError("extra_metadata.source_file is required")

    created_at = int(time.time())
    enriched: List[Dict[str, Any]] = []
    seen_chunk_seeds: Dict[str, int] = {}

    for item in chunks:
        content = item.get("content")
        base_meta = item.get("metadata", {})

        if not content:
            continue

        chunk_type = str(base_meta.get("type") or "text").strip().lower() or "text"
        section = str(base_meta.get("section") or "Unknown").strip() or "Unknown"
        parent_id = str(base_meta.get("parent_id") or "").strip() or None
        doc_id = str(base_meta.get("doc_id") or parent_id or "").strip() or None
        element_type = str(
            base_meta.get("element_type")
            or ("Table" if chunk_type in {"parent", "child"} else "NarrativeText")
        ).strip() or "NarrativeText"
        extraction_source = str(
            base_meta.get("extraction_source")
            or base_meta.get("source_weight_key")
            or _default_extraction_source(extra_metadata=extra_metadata)
        ).strip() or "unstructured_fast"
        source_weight_key = str(
            base_meta.get("source_weight_key")
            or extraction_source
        ).strip() or extraction_source
        quality_meta = _score_chunk_quality(content)
        ocr_used = bool(base_meta.get("ocr_used", False))
        extraction_backend = str(base_meta.get("extraction_backend") or extra_metadata.get("rag_preprocessor") or "").strip()
        page_number = base_meta.get("page_number", 1)
        bbox = base_meta.get("bbox", "")
        table_row_index = base_meta.get("table_row_index")
        chunk_index = base_meta.get("chunk_index")

        chunk_seed = "::".join(
            [
                str(company_document_id or "").strip(),
                str(revision_number or "").strip(),
                str(page_number or "").strip(),
                str(chunk_type or "").strip(),
                str(section or "").strip(),
                str(parent_id or "").strip(),
                str(doc_id or "").strip(),
                str(table_row_index if table_row_index is not None else "").strip(),
                str(chunk_index if chunk_index is not None else "").strip(),
                str(content or ""),
            ]
        )
        occurrence_index = int(seen_chunk_seeds.get(chunk_seed, 0))
        seen_chunk_seeds[chunk_seed] = occurrence_index + 1

        enriched.append(
            {
                "page_content": content,

                # -----------------------------
                # NON-IDENTITY METADATA
                # -----------------------------
                "metadata": {
                    "section": section,
                    "chunk_type": chunk_type,
                    "element_type": element_type,
                    "source_file": source_file,
                    "tokens": count_tokens(content),
                    "created_at": created_at,
                    "extraction_backend": extraction_backend,
                    "extraction_source": extraction_source,
                    "source_weight_key": source_weight_key,
                    "ocr_used": ocr_used,
                    "quality_score": quality_meta["quality_score"],
                    "quality_tier": quality_meta["quality_tier"],
                    
                    #  CRITICAL: Pass Page & BBox to DB for Frontend Highlighting
                    "page_number": page_number,
                    "bbox": bbox,

                    #  Table linkage (required for parent resolution)
                    "parent_id": parent_id,
                    "doc_id": doc_id,
                    "table_row_index": table_row_index,
                    "chunk_index": chunk_index,
                    "chunk_id_version": CHUNK_ID_VERSION,
                },

                # -----------------------------
                # 🔒 RAG IDENTITY (FILTER KEYS)
                # -----------------------------
                "cmetadata": {
                    "company_document_id": company_document_id,
                    "revision_number": revision_number, 
                    "revision_code": revision_code,
                    "revision_date": revision_date,
                    "document_type": document_type,
                },

                # -----------------------------
                # INTERNAL (OPTIONAL)
                # -----------------------------
                "chunk_id": generate_chunk_id(
                    company_document_id,
                    revision_number,
                    content,
                    page_number=page_number,
                    chunk_type=chunk_type,
                    section=section,
                    parent_id=parent_id,
                    doc_id=doc_id,
                    table_row_index=table_row_index,
                    chunk_index=chunk_index,
                    occurrence_index=occurrence_index,
                ),
                "chunk_id_version": CHUNK_ID_VERSION,
                "parent_id": parent_id, # None if parent
                "doc_id": doc_id,
            }
        )

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f" Enriched {len(enriched)} chunks.")
    print(f"[METADATA] Saved to: {output_file}")

    return {
        "company_document_id": company_document_id,
        "revision_number": revision_number,
        "revision_date": revision_date,
        "chunk_count": len(enriched),
        "source_file": source_file,
    }


# ============================================================
# CLI (DEBUG / MANUAL USE ONLY)
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) != 8:
        print("Usage: python metadata.py <chunks.json> <output.json> <pdf_path> <doc_id> <rev_num> <rev_date> <filename>")
        sys.exit(1)

    enrich_chunks(
        chunks_file=sys.argv[1],
        output_file=sys.argv[2],
        pdf_path=sys.argv[3],
        company_document_id=sys.argv[4],
        extra_metadata={
            "revision_number": sys.argv[5],
            "revision_date": sys.argv[6],
            "source_file": sys.argv[7],
        },
    )
    print("Chunk enrichment completed.")
