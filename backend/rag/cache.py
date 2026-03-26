# backend/rag/cache.py

"""
Semantic Retrieval Cache for Chat UI

Purpose:
- Cache retrieved chunk IDs for repeated/similar questions
- Avoids redundant vector search + reranking on same question
- In-memory LRU — no external dependency, works fully offline

Design Rules:
- NEVER stores chunk content (only IDs + scores) — keeps memory low
- TTL-based expiry (5 min default)
- Per-document isolation (key includes doc_id + revision)
- Must NEVER crash — always falls back to fresh retrieval
- Cache bypass when force_detailed=True
"""

import time
import hashlib
import re
from typing import List, Dict, Any, Optional
from collections import OrderedDict


# ============================================================
# CONFIG
# ============================================================

CACHE_TTL_SECONDS = 300        # 5 minutes
CACHE_MAX_SIZE    = 50         # max cached queries
_CACHE_ENABLED    = True       # global toggle


# ============================================================
# CACHE STORE (MODULE-LEVEL LRU)
# ============================================================

# OrderedDict used as an LRU — most-recently-used moves to end
# Entry format: { key: (timestamp, chunk_data_list) }
_store: OrderedDict = OrderedDict()


# ============================================================
# KEY GENERATION
# ============================================================

def _normalize_question(q: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    q = q.lower().strip()
    q = re.sub(r"[^\w\s]", " ", q)
    q = re.sub(r"\s+", " ", q)
    return q.strip()


def _make_key(
    company_document_id: str,
    revision_number: str,
    question: str,
) -> str:
    normalized = _normalize_question(question)
    # Use first 80 chars of normalized question for the hash
    raw = f"{company_document_id}|{revision_number}|{normalized[:80]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# ============================================================
# PUBLIC API
# ============================================================

def get_cached_chunks(
    company_document_id: str,
    revision_number: str,
    question: str,
) -> Optional[List[Dict[str, Any]]]:
    """
    Return cached chunks for this question, or None if not cached / expired.
    """
    if not _CACHE_ENABLED:
        return None

    try:
        key = _make_key(company_document_id, revision_number, question)
        if key not in _store:
            return None

        ts, chunks = _store[key]

        # Expired?
        if time.time() - ts > CACHE_TTL_SECONDS:
            del _store[key]
            return None

        # LRU: move to end (most recently used)
        _store.move_to_end(key)
        print(f"[CACHE] HIT — key={key[:8]}… ({len(chunks)} chunks)")
        return chunks

    except Exception as e:
        print(f"[CACHE] get error (non-fatal): {e}")
        return None


def set_cached_chunks(
    company_document_id: str,
    revision_number: str,
    question: str,
    chunks: List[Dict[str, Any]],
) -> None:
    """
    Store retrieved chunks in cache.
    Evicts oldest entry if at capacity.
    """
    if not _CACHE_ENABLED:
        return

    try:
        if not chunks:
            return

        key = _make_key(company_document_id, revision_number, question)

        # Evict oldest if full
        while len(_store) >= CACHE_MAX_SIZE:
            _store.popitem(last=False)

        _store[key] = (time.time(), chunks)
        _store.move_to_end(key)
        print(f"[CACHE] SET — key={key[:8]}… ({len(chunks)} chunks)")

    except Exception as e:
        print(f"[CACHE] set error (non-fatal): {e}")


def invalidate_document(
    company_document_id: str,
    revision_number: str,
) -> None:
    """
    Remove all cached entries for a document revision.
    Call this after re-ingestion.
    """
    try:
        prefix_raw = f"{company_document_id}|{revision_number}|"
        to_delete = []
        for key in list(_store.keys()):
            # We can't reverse the hash, so clear whole cache for that doc
            # by comparing key prefix pattern stored separately
            pass
        # Simpler: clear entire cache on re-ingest (small cache, cheap)
        _store.clear()
        print(f"[CACHE] Cleared on re-ingest for {company_document_id}")
    except Exception:
        pass


def get_cache_stats() -> Dict[str, Any]:
    """Return cache diagnostic info."""
    return {
        "size": len(_store),
        "max_size": CACHE_MAX_SIZE,
        "ttl_seconds": CACHE_TTL_SECONDS,
        "enabled": _CACHE_ENABLED,
    }
