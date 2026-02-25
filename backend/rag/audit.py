# backend/rag/audit.py

"""
RAG Audit Log

Purpose:
- Log every RAG decision for debugging, compliance, and quality monitoring
- Replace scattered print() debug statements with structured, queryable records
- Stored in PostgreSQL (same chat_memory_db schema)

Design Rules:
- Async-safe: writes never block the main stream
- Append-only: no deletes
- MUST NEVER raise — logging failures are silent
- Queryable via get_recent_audits() for dev dashboard
"""

import os
import time
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json


# ============================================================
# CONFIG
# ============================================================

AUDIT_DB_URL = os.getenv(
    "CHAT_DB_URL",
    "postgresql://postgres:1@localhost:5432/chat_memory_db",
)

AUDIT_ENABLED = True
AUDIT_MAX_ANSWER_CHARS = 500   # truncate long answers in log


# ============================================================
# CONNECTION
# ============================================================

@contextmanager
def _get_conn():
    conn = None
    try:
        conn = psycopg2.connect(AUDIT_DB_URL)
        yield conn
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


# ============================================================
# TABLE INIT (self-healing)
# ============================================================

_TABLE_READY = False

def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rag_audit_log (
                        id                  SERIAL PRIMARY KEY,
                        session_id          TEXT,
                        company_document_id TEXT,
                        revision_number     TEXT,

                        question            TEXT,
                        rewritten_question  TEXT,
                        intent              TEXT,

                        chunk_ids           TEXT[],
                        chunk_count         INTEGER,
                        grounding_score     REAL,
                        eval_quality        TEXT,
                        eval_scores         JSONB,

                        answer_snippet      TEXT,
                        latency_ms          INTEGER,
                        cache_hit           BOOLEAN DEFAULT FALSE,
                        multi_query_used    BOOLEAN DEFAULT FALSE,

                        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS rag_audit_session_idx
                        ON rag_audit_log (session_id);
                    CREATE INDEX IF NOT EXISTS rag_audit_doc_idx
                        ON rag_audit_log (company_document_id, revision_number);
                """)
        _TABLE_READY = True
    except Exception as e:
        print(f"[AUDIT] Table init warning (non-fatal): {e}")


# ============================================================
# WRITE (non-blocking best-effort)
# ============================================================

def log_rag_turn(
    *,
    session_id: str,
    company_document_id: str,
    revision_number: str,
    question: str,
    rewritten_question: Optional[str] = None,
    intent: Optional[str] = None,
    chunk_ids: Optional[List[str]] = None,
    grounding_score: Optional[float] = None,
    eval_scores: Optional[Dict[str, Any]] = None,
    answer_snippet: Optional[str] = None,
    latency_ms: Optional[int] = None,
    cache_hit: bool = False,
    multi_query_used: bool = False,
) -> None:
    """
    Write one RAG turn to the audit log.
    Silent on error — never raises, never blocks.
    """
    if not AUDIT_ENABLED:
        return

    try:
        _ensure_table()

        eval_quality = eval_scores.get("quality") if eval_scores else None
        chunk_count  = len(chunk_ids) if chunk_ids else 0
        snippet = (answer_snippet or "")[:AUDIT_MAX_ANSWER_CHARS]

        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rag_audit_log (
                        session_id, company_document_id, revision_number,
                        question, rewritten_question, intent,
                        chunk_ids, chunk_count,
                        grounding_score, eval_quality, eval_scores,
                        answer_snippet, latency_ms,
                        cache_hit, multi_query_used
                    ) VALUES (
                        %s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s,%s
                    )
                    """,
                    (
                        session_id, company_document_id, str(revision_number),
                        question, rewritten_question, intent,
                        chunk_ids, chunk_count,
                        grounding_score, eval_quality,
                        Json(eval_scores) if eval_scores else None,
                        snippet, latency_ms,
                        cache_hit, multi_query_used,
                    ),
                )
    except Exception as e:
        print(f"[AUDIT] log_rag_turn failed (non-fatal): {e}")


# ============================================================
# READ (for dev dashboard / debugging)
# ============================================================

def get_recent_audits(
    session_id: Optional[str] = None,
    company_document_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fetch recent audit entries for inspection.
    Returns [] on any error.
    """
    try:
        _ensure_table()
        filters = []
        params: List[Any] = []

        if session_id:
            filters.append("session_id = %s")
            params.append(session_id)
        if company_document_id:
            filters.append("company_document_id = %s")
            params.append(company_document_id)

        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(limit)

        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT * FROM rag_audit_log
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                return [dict(r) for r in (cur.fetchall() or [])]
    except Exception as e:
        print(f"[AUDIT] get_recent_audits failed: {e}")
        return []
