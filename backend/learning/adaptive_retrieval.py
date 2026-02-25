# backend/learning/adaptive_retrieval.py

"""
Adaptive Retrieval Configuration

Purpose:
- Auto-tune retrieval parameters (K, similarity threshold) per document
  based on historical retrieval quality from retrieval_stats table
- Documents with high avg_score → can use tighter K
- Documents with low confidence → widen K for more recall
- Changes are SUGGESTIONS only — never override force_detailed

Design Rules:
- Reads from retrieval_stats table (already populated by Phase 1+)
- Returns config dict — never modifies live search directly
- Uses rolling average of last 20 interactions per document
- MUST NEVER raise — always returns safe defaults
"""

import os
from typing import Dict, Any, Optional
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor


# ============================================================
# CONFIG
# ============================================================

ADAPTIVE_DB_URL = os.getenv(
    "CHAT_DB_URL",
    "postgresql://postgres:1@localhost:5432/chat_memory_db",
)

# Default retrieval settings (safe baseline)
DEFAULT_CONFIG = {
    "k":               8,
    "candidate_k":     25,
    "force_detailed":  False,
    "confidence_gate": 0.30,    # minimum confidence to trust result
}

# Thresholds for auto-tuning
HIGH_CONFIDENCE_THRESHOLD = 0.75   # doc regularly scores high → tighten K
LOW_CONFIDENCE_THRESHOLD  = 0.40   # doc regularly scores low  → widen K
RECENT_STATS_LIMIT = 20


# ============================================================
# DB CONNECTION
# ============================================================

@contextmanager
def _get_conn():
    conn = None
    try:
        conn = psycopg2.connect(ADAPTIVE_DB_URL)
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
# STATS READER
# ============================================================

def _get_recent_stats(
    company_document_id: str,
    revision_number: str,
) -> Optional[Dict[str, float]]:
    """
    Fetch rolling average stats for a document from retrieval_stats.
    Returns None if insufficient data.
    """
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        AVG(confidence)    AS avg_confidence,
                        AVG(avg_score)     AS avg_retrieval_score,
                        AVG(chunk_count)   AS avg_chunk_count,
                        AVG(latency_ms)    AS avg_latency_ms,
                        COUNT(*)           AS sample_count
                    FROM (
                        SELECT confidence, avg_score, chunk_count, latency_ms
                        FROM retrieval_stats
                        WHERE company_document_id = %s
                          AND revision_number = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    ) recent
                    """,
                    (company_document_id, str(revision_number), RECENT_STATS_LIMIT),
                )
                row = cur.fetchone()
                if not row or not row["sample_count"] or int(row["sample_count"]) < 3:
                    return None
                return dict(row)
    except Exception as e:
        print(f"[ADAPTIVE] stats fetch failed (non-fatal): {e}")
        return None


# ============================================================
# MAIN API
# ============================================================

def get_adaptive_config(
    company_document_id: str,
    revision_number: str,
) -> Dict[str, Any]:
    """
    Return auto-tuned retrieval config for a document.

    Uses historical stats to decide:
    - High confidence doc  → K=6, tight retrieval
    - Normal doc           → K=8, standard
    - Low confidence doc   → K=12, wide retrieval + force_detailed=True

    Always returns a valid config dict. Never raises.
    """
    try:
        stats = _get_recent_stats(company_document_id, revision_number)

        if stats is None:
            # Not enough history — use defaults
            print(f"[ADAPTIVE] No history for {company_document_id[:12]}… — using defaults")
            return dict(DEFAULT_CONFIG)

        avg_conf  = float(stats["avg_confidence"] or 0.5)
        avg_score = float(stats["avg_retrieval_score"] or 0.5)

        config = dict(DEFAULT_CONFIG)

        if avg_conf >= HIGH_CONFIDENCE_THRESHOLD and avg_score >= 0.6:
            # Document retrieves well — can be more efficient
            config["k"]           = 6
            config["candidate_k"] = 18
            config["force_detailed"] = False
            print(f"[ADAPTIVE] doc={company_document_id[:12]}… → TIGHT (conf={avg_conf:.2f})")

        elif avg_conf <= LOW_CONFIDENCE_THRESHOLD or avg_score < 0.35:
            # Document retrieves poorly — widen the net
            config["k"]           = 12
            config["candidate_k"] = 35
            config["force_detailed"] = True
            print(f"[ADAPTIVE] doc={company_document_id[:12]}… → WIDE (conf={avg_conf:.2f})")

        else:
            print(f"[ADAPTIVE] doc={company_document_id[:12]}… → DEFAULT (conf={avg_conf:.2f})")

        return config

    except Exception as e:
        print(f"[ADAPTIVE] get_adaptive_config failed (non-fatal): {e}")
        return dict(DEFAULT_CONFIG)
