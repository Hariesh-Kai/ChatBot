# backend/rag/grounding.py

"""
Grounding / Hallucination Check for Chat UI RAG

Purpose:
- After LLM generates an answer, verify that key factual tokens
  (numbers, codes, abbreviations, page refs) actually appear in
  the retrieved source chunks.
- Pure text matching — NO LLM calls, NO embeddings.
- NEVER raises — always returns a valid result.

Design Rules:
- Must NEVER block or crash the chat stream.
- Returns a grounding result dict; caller decides what to do with it.
- False positives are acceptable; false negatives (missed warnings) are acceptable too.
- This is a soft safety net, not an authoritative judge.
"""

import re
from typing import List, Dict, Any


# ============================================================
# CONFIG
# ============================================================

# Minimum grounding score to consider the answer grounded
GROUNDING_THRESHOLD = 0.50

# Minimum number of candidate tokens before we bother checking
MIN_CANDIDATES_TO_CHECK = 2


# ============================================================
# TOKEN EXTRACTION
# ============================================================

def _extract_factual_tokens(text: str) -> List[str]:
    """
    Extract candidate factual tokens from the LLM answer:
    - Numbers (integers, decimals, with optional units)
    - ALL-CAPS abbreviations (e.g., SBTW, BGRB, API)
    - Alphanumeric codes with hyphens/dots (e.g., 363010-BGRB, P-101A)
    - Page references like [Page 12]
    """
    if not text:
        return []

    candidates = set()

    # 1. Numbers with optional units (e.g., "8000", "50.5 bar", "25%")
    for m in re.finditer(r'\b\d[\d,\.]*\s*(?:[a-zA-Z%°/]{1,6})?\b', text):
        token = m.group().strip()
        if token:
            candidates.add(token)

    # 2. ALL-CAPS abbreviations (2+ chars)
    for m in re.finditer(r'\b[A-Z]{2,}\b', text):
        candidates.add(m.group())

    # 3. Alphanumeric codes with hyphens/dots (e.g., P-101A, 363010-BGRB)
    for m in re.finditer(r'\b[A-Z0-9]{2,}[-\.][A-Z0-9]{2,}[-\.A-Z0-9]*\b', text):
        candidates.add(m.group())

    # 4. Page references (e.g., [Page 12], Page 4)
    for m in re.finditer(r'(?:\[Page\s+\d+\]|Page\s+\d+)', text, re.IGNORECASE):
        # Don't verify page refs — they're metadata not in chunk text
        pass

    return list(candidates)


# ============================================================
# CHUNK CORPUS BUILDER
# ============================================================

def _build_chunk_corpus(rag_chunks: List[Dict[str, Any]]) -> str:
    """
    Concatenate all retrieved chunk content into a single searchable corpus.
    """
    parts = []
    for chunk in rag_chunks:
        content = chunk.get("content", "")
        if content:
            parts.append(content)
    return "\n".join(parts)


# ============================================================
# MAIN GROUNDING CHECK
# ============================================================

def check_grounding(
    answer: str,
    rag_chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Checks whether the key factual tokens in the answer
    appear in the retrieved source chunks.

    Returns:
    {
        "is_grounded": bool,
        "grounding_score": float (0.0–1.0),
        "unverified_tokens": List[str],
        "checked_tokens": int,
    }

    Always returns a valid dict — never raises.
    """
    default_result = {
        "is_grounded": True,
        "grounding_score": 1.0,
        "unverified_tokens": [],
        "checked_tokens": 0,
    }

    try:
        if not answer or not rag_chunks:
            return default_result

        # Extract candidate tokens from the answer
        candidates = _extract_factual_tokens(answer)

        if len(candidates) < MIN_CANDIDATES_TO_CHECK:
            # Not enough factual content to check meaningfully
            return default_result

        # Build the source corpus from retrieved chunks
        corpus = _build_chunk_corpus(rag_chunks).lower()

        if not corpus.strip():
            return default_result

        # Check each candidate
        verified = []
        unverified = []

        for token in candidates:
            # Normalize for comparison
            token_lower = token.lower().strip()
            # Remove commas from numbers for matching (e.g., "1,000" matches "1000")
            token_normalized = token_lower.replace(",", "")

            if token_lower in corpus or token_normalized in corpus:
                verified.append(token)
            else:
                unverified.append(token)

        total = len(candidates)
        matched = len(verified)
        score = round(matched / total, 2) if total > 0 else 1.0

        is_grounded = score >= GROUNDING_THRESHOLD

        return {
            "is_grounded": is_grounded,
            "grounding_score": score,
            "unverified_tokens": unverified,
            "checked_tokens": total,
        }

    except Exception as e:
        # NEVER crash the caller
        print(f"[GROUNDING] Check failed (non-fatal): {e}")
        return default_result
