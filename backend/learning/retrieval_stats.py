# backend/learning/retrieval_stats.py

"""
Retrieval Statistics (Passive Observability)

Purpose:
- Record WHAT retrieval did
- NO judgments
- NO learning
- NO effect on live answers

This is telemetry, not intelligence.
"""

from typing import List, Dict, Any, Optional
import os
from contextlib import contextmanager
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor


# =========================================================
# CONFIG
# =========================================================

LEARNING_DB_URL = os.getenv(
    "LEARNING_DB_URL",
    os.getenv(
        "CHAT_DB_URL",
        "postgresql://postgres:1@localhost:5432/chat_memory_db",
    ),
)

MAX_SECTIONS_STORED = 10
MAX_TYPES_STORED = 5


# =========================================================
# DB CONNECTION (SAFE)
# =========================================================

@contextmanager
def get_connection():
    conn = None
    try:
        conn = psycopg2.connect(LEARNING_DB_URL)
        yield conn
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


# =========================================================
# INIT (SELF-HEALING)
# =========================================================

def _init_db():
    query = """
    CREATE TABLE IF NOT EXISTS retrieval_stats (
        id SERIAL PRIMARY KEY,

        session_id TEXT,
        job_id TEXT,

        company_document_id TEXT NOT NULL,
        revision_number TEXT NOT NULL,

        question TEXT NOT NULL,

        chunk_count INTEGER NOT NULL,
        chunk_types TEXT[],
        sections TEXT[],

        avg_score REAL,
        max_score REAL,

        confidence REAL,
        confidence_level TEXT,

        latency_ms INTEGER,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
        print("Learning DB: retrieval_stats table ready.")
    except Exception as e:
        print(f"Retrieval stats DB init warning: {e}")


_init_db()


# =========================================================
# INTERNAL HELPERS
# =========================================================

def _extract_types(chunks: List[Dict[str, Any]]) -> List[str]:
    seen = []
    for c in chunks:
        t = c.get("chunk_type")
        if t and t not in seen:
            seen.append(t)
        if len(seen) >= MAX_TYPES_STORED:
            break
    return seen


def _extract_sections(chunks: List[Dict[str, Any]]) -> List[str]:
    seen = []
    for c in chunks:
        s = c.get("section")
        if s and s not in seen:
            seen.append(s)
        if len(seen) >= MAX_SECTIONS_STORED:
            break
    return seen


def _score_stats(chunks: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    scores = [c.get("score") for c in chunks if isinstance(c.get("score"), (int, float))]
    if not scores:
        return {"avg": None, "max": None}

    return {
        "avg": round(sum(scores) / len(scores), 4),
        "max": round(max(scores), 4),
    }


# =========================================================
# PUBLIC API
# =========================================================

def record_retrieval_stats(
    *,
    session_id: Optional[str],
    job_id: Optional[str],
    company_document_id: str,
    revision_number: str,
    question: str,
    rag_chunks: List[Dict[str, Any]],
    confidence: Optional[float] = None,
    confidence_level: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> None:
    """
    Record passive retrieval statistics.

    MUST NEVER raise.
    """

    if not company_document_id or not revision_number:
        return

    try:
        chunk_count = len(rag_chunks)
        chunk_types = _extract_types(rag_chunks)
        sections = _extract_sections(rag_chunks)
        score_stats = _score_stats(rag_chunks)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO retrieval_stats (
                        session_id,
                        job_id,
                        company_document_id,
                        revision_number,
                        question,
                        chunk_count,
                        chunk_types,
                        sections,
                        avg_score,
                        max_score,
                        confidence,
                        confidence_level,
                        latency_ms
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        session_id,
                        job_id,
                        company_document_id,
                        str(revision_number),
                        question,
                        chunk_count,
                        chunk_types,
                        sections,
                        score_stats["avg"],
                        score_stats["max"],
                        confidence,
                        confidence_level,
                        latency_ms,
                    ),
                )
    except Exception as e:
        # 🔥 Telemetry must NEVER affect production
        print(f"Failed to record retrieval stats: {e}")


# =========================================================
# READ (OPTIONAL – ANALYTICS)
# =========================================================

def get_recent_stats(
    *,
    company_document_id: str,
    revision_number: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM retrieval_stats
                    WHERE company_document_id = %s
                      AND revision_number = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (company_document_id, str(revision_number), limit),
                )
                return cur.fetchall() or []
    except Exception:
        return []
