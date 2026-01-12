# backend/rag/metadata.py

import json
import sys
import hashlib
import tiktoken
import time
from datetime import datetime
from typing import Dict, Any, List

# ============================================================
# TOKENIZER (STATS ONLY — NO MODEL USE)
# ============================================================

tokenizer = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(tokenizer.encode(text))


# ============================================================
# CHUNK ID (DETERMINISTIC, REVISION-SAFE)
# ============================================================

def generate_chunk_id(
    company_document_id: str,
    revision_number: str, # ✅ Changed to str
    content: str,
) -> str:
    """
    Deterministic, document-scoped chunk ID.

    Guarantees:
    - Stable across re-ingestion
    - No collision across documents or revisions
    """
    base = f"{company_document_id}:{revision_number}:{content}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()


# ============================================================
# METADATA EXTRACTION (PHASE 1 — NO DB, NO CHUNKS)
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

    STRICT RULES:
    - ❌ No chunking
    - ❌ No DB writes
    - ❌ No guessing / hallucination
    - ❌ NO IDENTITY FIELDS
    - ✅ Confidence-based output
    - ✅ SCANS PAGE 1 ONLY
    """

    with open(elements_file, "r", encoding="utf-8") as f:
        elements: List[Dict[str, Any]] = json.load(f)

    metadata: Dict[str, Dict[str, Any]] = {}

    # --------------------------------------------------------
    # REQUIRED USER-EDITABLE FIELDS ONLY
    # --------------------------------------------------------

    REQUIRED_FIELDS = [
        "document_type",
        "revision_code",
    ]

    for key in REQUIRED_FIELDS:
        metadata[key] = {
            "value": None,
            "confidence": 0.0,
        }

    # --------------------------------------------------------
    # SAFE HEURISTICS (OPTIONAL, NON-AUTHORITATIVE)
    # --------------------------------------------------------

    for el in elements:
        # ✅ NEW: Check Page Number
        page_number = el.get("metadata", {}).get("page_number", 1)
        
        # If we passed page 1, STOP scanning to save time.
        if page_number > 1:
            break

        text = el.get("text") or el.get("content") or ""
        if not text:
            continue

        lower = text.lower()

        # --- Simple Heuristics for Page 1 ---
        
        # Example: Detect Document Type from Title
        if (
            "basis of design" in lower
            and metadata["document_type"]["confidence"] < 0.9
        ):
            metadata["document_type"] = {
                "value": "Basis of Design",
                "confidence": 0.9,
            }
        elif (
            "design basis" in lower
            and metadata["document_type"]["confidence"] < 0.8
        ):
            metadata["document_type"] = {
                "value": "Basis of Design",
                "confidence": 0.8,
            }

    # --------------------------------------------------------
    # AUTHORITATIVE OVERRIDES (NON-IDENTITY ONLY)
    # --------------------------------------------------------

    if "revision_code" in extra_metadata:
        metadata["revision_code"] = {
            "value": extra_metadata["revision_code"],
            "confidence": 1.0,
        }

    if "document_type" in extra_metadata:
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

    STRICT RULES:
    - ❌ Do NOT infer metadata
    - ❌ Do NOT parse filenames
    - ✅ Identity lives ONLY in cmetadata
    """

    print(f"✨ Enriching chunks from: {chunks_file}")

    with open(chunks_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    # ✅ FIX: Treat revision as String (do not cast to int)
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

    for item in chunks:
        content = item.get("content")
        base_meta = item.get("metadata", {})

        if not content:
            continue

        enriched.append(
            {
                "page_content": content,

                # -----------------------------
                # NON-IDENTITY METADATA
                # -----------------------------
                "metadata": {
                    "section": base_meta.get("section", "Unknown"),
                    "chunk_type": base_meta.get("type", "text"),
                    "source_file": source_file,
                    "tokens": count_tokens(content),
                    "created_at": created_at,
                    
                    # ✅ NEW: Pass through location data to DB
                    "page_number": base_meta.get("page_number", 1),
                    "bbox": base_meta.get("bbox", "")
                },

                # -----------------------------
                # 🔒 RAG IDENTITY (FILTER KEYS)
                # -----------------------------
                "cmetadata": {
                    "company_document_id": company_document_id,
                    "revision_number": revision_number, # ✅ String (No int conversion)
                    "revision_code": revision_code,
                    "revision_date": revision_date,
                    "document_type": document_type,
                },

                # -----------------------------
                # INTERNAL (OPTIONAL)
                # -----------------------------
                "chunk_id": generate_chunk_id(
                    company_document_id,
                    revision_number, # ✅ String
                    content,
                ),
                "parent_id": base_meta.get("parent_id"), # None if parent
                "doc_id": base_meta.get("doc_id"),       # Only present on parent
            }
        )

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f"✅ Enriched {len(enriched)} chunks.")
    print(f"💾 Saved to: {output_file}")

    return {
        "company_document_id": company_document_id,
        "revision_number": revision_number, # ✅ String
        "revision_date": revision_date,
        "chunk_count": len(enriched),
        "source_file": source_file,
    }


# ============================================================
# CLI (DEBUG / MANUAL USE ONLY)
# ============================================================

if __name__ == "__main__":
    """
    Usage:
    python metadata.py <chunks.json> <output.json> <pdf_path> 
                        <company_document_id> <revision_number> 
                        <revision_date> <source_file>
    """

    if len(sys.argv) != 8:
        print(
            "Usage: python metadata.py "
            "<chunks.json> <output.json> <pdf_path> "
            "<company_document_id> <revision_number> "
            "<revision_date> <source_file>"
        )
        sys.exit(1)

    enrich_chunks(
        chunks_file=sys.argv[1],
        output_file=sys.argv[2],
        pdf_path=sys.argv[3],
        company_document_id=sys.argv[4],
        extra_metadata={
            "revision_number": sys.argv[5], # ✅ Treat as String
            "revision_date": sys.argv[6],
            "source_file": sys.argv[7],
        },
    )
    print("✅ Chunk enrichment completed.")