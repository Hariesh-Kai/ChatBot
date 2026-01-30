# backend/rag/keyword_search.py

"""
SQL Keyword Search for Hybrid RAG Retrieval

Purpose:
- Exact / partial keyword matching (IDs, numbers, well names)
- Complements vector similarity search
- Uses PostgreSQL ILIKE

Design Rules:
- NO embeddings
- NO LLM calls
- NO side effects
- Must NEVER crash chat or upload
"""

from typing import List, Optional, Dict
import re
import hashlib

from sqlalchemy import text
from langchain_core.documents import Document
from langchain_postgres import PGVector


# ============================================================
# CONFIG
# ============================================================

DEFAULT_LIMIT = 8
MIN_TOKEN_LENGTH = 3

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

    keywords = [
        t for t in tokens
        if len(t) >= MIN_TOKEN_LENGTH and t not in STOP_TOKENS
    ]

    seen = set()
    out: List[str] = []
    for t in keywords:
        if t not in seen:
            seen.add(t)
            out.append(t)

    return out


# ============================================================
# KEYWORD SEARCH (REVISION-SAFE)
# ============================================================

def keyword_search(
    *,
    question: str,
    vector_store: PGVector,
    metadata_filter: Optional[Dict] = None,
    limit: int = DEFAULT_LIMIT,
) -> List[Document]:

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


    clauses = []
    params: Dict[str, str] = {}

    for i, kw in enumerate(keywords):
        key = f"kw{i}"
        clauses.append(f"document ILIKE :{key}")
        params[key] = f"%{kw}%"

    where_sql = "(" + " OR ".join(clauses) + ")"

    # --------------------------------------------------------
    # 🔒 METADATA FILTER (FINAL SCHEMA)
    # --------------------------------------------------------
    if metadata_filter:
        for k, v in metadata_filter.items():
            where_sql += f" AND cmetadata->>'{k}' = :val_{k}"
            params[f"val_{k}"] = str(v)

    sql = text(f"""
        SELECT document, cmetadata
        FROM langchain_pg_embedding
        WHERE {where_sql}
        ORDER BY LENGTH(document) ASC
        LIMIT :limit
    """)

    params["limit"] = limit

    try:
        engine = vector_store._engine
        with engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        print(f"Keyword search failed: {e}")
        return []

    documents: List[Document] = []
    seen_hashes = set()

    for row in rows:
        try:
            text_content = row[0]
            cmetadata = row[1] or {}

            if not text_content:
                continue

            h = hashlib.md5(text_content.encode("utf-8")).hexdigest()
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            documents.append(
                Document(
                    page_content=text_content,
                    metadata={},        # non-identity
                    cmetadata=cmetadata # 🔒 authoritative
                )
            )
        except Exception:
            continue

    return documents


# ============================================================
# OPTIONAL: keyword overlap score
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
