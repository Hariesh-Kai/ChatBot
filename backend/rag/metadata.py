# backend/rag/metadata.py

import json
import sys
import hashlib
import tiktoken
import time
import re  # ✅ Added for Regex patterns
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
    revision_number: str, 
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

    ✅ UPDATED: Uses Regex to distinguish Document ID vs Project Name.
    """

    with open(elements_file, "r", encoding="utf-8") as f:
        elements: List[Dict[str, Any]] = json.load(f)

    # Initialize with default confidence
    metadata = {
        "document_title": {"value": None, "confidence": 0.0},
        "revision_code": {"value": None, "confidence": 0.0},
        "project_name":  {"value": None, "confidence": 0.0}, # ✅ New field
        "document_number": {"value": None, "confidence": 0.0} # ✅ New field
    }

    # --------------------------------------------------------
    # 🧠 SMART HEURISTICS (Page 1 Only)
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
        
        elif "design basis" in lower and metadata["document_title"]["confidence"] < 0.8:
            clean_title = text.replace("\n", " ").strip()
            metadata["document_title"] = {"value": clean_title, "confidence": 0.8}

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
                    
                    # ✅ CRITICAL: Pass Page & BBox to DB for Frontend Highlighting
                    "page_number": base_meta.get("page_number", 1),
                    "bbox": base_meta.get("bbox", "") 
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
    print("✅ Chunk enrichment completed.")