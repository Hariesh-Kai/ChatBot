# backend/rag/keyword_search.py

"""
BM25-style Full-Text Keyword Search for Hybrid RAG Retrieval

Purpose:
- Full-text search using PostgreSQL tsvector/tsquery (GIN index)
- Returns scored results (ts_rank) for Reciprocal Rank Fusion
- Complements vector similarity search
- Falls back to ILIKE for short/non-FTS-friendly queries

Design Rules:
- NO embeddings
- NO LLM calls
- NO side effects
- Must NEVER crash chat or upload
"""

from typing import List, Optional, Dict, Tuple
import re
import hashlib

from sqlalchemy import text
from langchain_core.documents import Document
from langchain_postgres import PGVector


# ============================================================
# CONFIG
# ============================================================

DEFAULT_LIMIT = 10
MIN_TOKEN_LENGTH = 3
MAX_SHORT_NUMERIC_TOKENS = 2

STOP_TOKENS = {
    "the", "what", "which", "when", "where",
    "is", "are", "was", "were", "of", "in",
    "for", "to", "and", "or",
}


# ============================================================
# TOKEN EXTRACTION
# ============================================================

def extract_keywords(question: str) -> List[str]:
    if not question:
        return []

    tokens = re.findall(r"[a-zA-Z0-9\-\.]+", question.lower())

    def _has_digit(t: str) -> bool:
        return any(ch.isdigit() for ch in t)

    short_numeric_used = 0
    keywords: List[str] = []
    for t in tokens:
        if t in STOP_TOKENS:
            continue

        if _has_digit(t) and len(t) < MIN_TOKEN_LENGTH:
            if short_numeric_used >= MAX_SHORT_NUMERIC_TOKENS:
                continue
            short_numeric_used += 1
            keywords.append(t)
            continue

        if len(t) >= MIN_TOKEN_LENGTH:
            keywords.append(t)

    seen = set()
    out: List[str] = []
    for t in keywords:
        if t not in seen:
            seen.add(t)
            out.append(t)

    return out


def _build_tsquery(keywords: List[str]) -> str:
    """
    Build a PostgreSQL tsquery string.
    Joins keywords with OR for broad recall.
    Prefix-matches longer tokens with :* for partial matching.
    """
    parts = []
    for kw in keywords:
        # Sanitize: remove chars not valid in tsquery tokens
        safe = re.sub(r"[^a-zA-Z0-9\-]", "", kw)
        if not safe:
            continue
        # Use prefix matching for tokens >= 4 chars
        if len(safe) >= 4:
            parts.append(f"{safe}:*")
        else:
            parts.append(safe)
    return " | ".join(parts) if parts else ""


# ============================================================
# BM25-STYLE KEYWORD SEARCH (PRIMARY — uses GIN index)
# ============================================================

def keyword_search(
    *,
    question: str,
    vector_store: PGVector,
    metadata_filter: Optional[Dict] = None,
    limit: int = DEFAULT_LIMIT,
) -> List[Tuple[Document, float]]:
    """
    Full-text search using PostgreSQL tsvector + ts_rank scoring.

    Returns: list of (Document, score) tuples where score is ts_rank (0–1 range).
    Falls back to ILIKE search if tsquery fails (e.g., special chars).
    Returns empty list on any error — NEVER raises.
    """
    keywords = extract_keywords(question)
    if not keywords:
        return []

    if not metadata_filter:
        return []

    if (
        "company_document_id" not in metadata_filter
        or "revision_number" not in metadata_filter
    ):
        return []

    # Try FTS first
    result = _fts_search(keywords, metadata_filter, vector_store, limit)
    if result:
        return result

    # Fallback to ILIKE if FTS returns nothing (e.g., column missing)
    return _ilike_search(keywords, metadata_filter, vector_store, limit)


# ============================================================
# FTS SEARCH (ts_rank scored)
# ============================================================

def _fts_search(
    keywords: List[str],
    metadata_filter: Dict,
    vector_store: PGVector,
    limit: int,
) -> List[Tuple[Document, float]]:
    tsquery_str = _build_tsquery(keywords)
    if not tsquery_str:
        return []

    # Build metadata filter SQL
    meta_where = ""
    params: Dict = {"limit": limit, "tsq": tsquery_str}
    for k, v in metadata_filter.items():
        meta_where += f" AND cmetadata->>'{ k }' = :val_{k}"
        params[f"val_{k}"] = str(v)

    sql = text(f"""
        SELECT document, cmetadata,
               ts_rank(content_tsv, to_tsquery('english', :tsq)) AS rank
        FROM langchain_pg_embedding
        WHERE content_tsv @@ to_tsquery('english', :tsq)
        {meta_where}
        ORDER BY rank DESC
        LIMIT :limit
    """)

    try:
        engine = vector_store._engine
        with engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        print(f"[KEYWORD] FTS search failed: {e}")
        return []

    return _rows_to_documents(rows, has_score=True)


# ============================================================
# ILIKE FALLBACK (unscored — score assigned as 0.1)
# ============================================================

def _ilike_search(
    keywords: List[str],
    metadata_filter: Dict,
    vector_store: PGVector,
    limit: int,
) -> List[Tuple[Document, float]]:
    clauses = []
    params: Dict = {"limit": limit}

    for i, kw in enumerate(keywords):
        key = f"kw{i}"
        clauses.append(f"document ILIKE :{key}")
        params[key] = f"%{kw}%"

    where_sql = "(" + " OR ".join(clauses) + ")"

    for k, v in metadata_filter.items():
        where_sql += f" AND cmetadata->>'{k}' = :val_{k}"
        params[f"val_{k}"] = str(v)

    sql = text(f"""
        SELECT document, cmetadata, 0.1 AS rank
        FROM langchain_pg_embedding
        WHERE {where_sql}
        ORDER BY LENGTH(document) ASC
        LIMIT :limit
    """)

    try:
        engine = vector_store._engine
        with engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        print(f"[KEYWORD] ILIKE fallback failed: {e}")
        return []

    return _rows_to_documents(rows, has_score=True)


# ============================================================
# ROW → DOCUMENT CONVERTER
# ============================================================

def _rows_to_documents(rows, has_score: bool) -> List[Tuple[Document, float]]:
    results: List[Tuple[Document, float]] = []
    seen_hashes = set()

    for row in rows:
        try:
            text_content = row[0]
            cmetadata = row[1] or {}
            score = float(row[2]) if has_score and row[2] is not None else 0.0

            if not text_content:
                continue

            h = hashlib.md5(text_content.encode("utf-8")).hexdigest()
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            doc = Document(
                page_content=text_content,
                metadata=cmetadata,
            )
            results.append((doc, score))
        except Exception:
            continue

    return results


# ============================================================
# OPTIONAL: keyword overlap score (unchanged)
# ============================================================

def keyword_match_score(*, question: str, content: str) -> float:
    if not question or not content:
        return 0.0

    q_tokens = set(extract_keywords(question))
    if not q_tokens:
        return 0.0

    content_lower = content.lower()
    hits = sum(1 for t in q_tokens if t in content_lower)

    return min(hits / len(q_tokens), 1.0)
