# backend/learning/rlhf_collector.py

"""
RLHF Training Data Collector

Purpose:
- Format feedback data (question + answer + rating) into training-ready JSONL
- Builds the dataset that will feed fine-tuning when GPU is available
- Exports in OpenAI JSONL format (compatible with most fine-tuning frameworks)

Design Rules:
- Reads from existing retrieval_feedback table (already populated)
- NEVER modifies live retrieval or answers
- Export is idempotent — safe to run multiple times
- Must NEVER raise to caller
"""

import os
import json
from typing import List, Dict, Any, Optional
from contextlib import contextmanager
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor


# ============================================================
# CONFIG
# ============================================================

RLHF_DB_URL = os.getenv(
    "CHAT_DB_URL",
    "postgresql://postgres:1@localhost:5432/chat_memory_db",
)

# Label mapping: feedback labels → RLHF quality score (0.0–1.0)
LABEL_SCORE_MAP = {
    "correct":      1.0,
    "helpful":      0.9,
    "thumbs_up":    0.9,
    "partial":      0.5,
    "incorrect":    0.0,
    "hallucination": 0.0,
    "missing_context": 0.2,
}


# ============================================================
# DB CONNECTION
# ============================================================

@contextmanager
def _get_conn():
    conn = None
    try:
        conn = psycopg2.connect(RLHF_DB_URL)
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
# DATA FETCHING
# ============================================================

def _fetch_feedback_rows(
    company_document_id: Optional[str] = None,
    min_score: Optional[float] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """Fetch feedback rows from the database."""
    try:
        filters = []
        params: List[Any] = []

        if company_document_id:
            filters.append("company_document_id = %s")
            params.append(company_document_id)

        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(limit)

        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT question, answer, feedback_label, feedback_score,
                           company_document_id, revision_number, created_at
                    FROM retrieval_feedback
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                return [dict(r) for r in (cur.fetchall() or [])]
    except Exception as e:
        print(f"[RLHF] fetch failed (non-fatal): {e}")
        return []


# ============================================================
# JSONL FORMATTING
# ============================================================

def _row_to_training_example(row: Dict[str, Any]) -> Optional[Dict]:
    """
    Convert a feedback row to OpenAI fine-tuning JSONL format.

    Format:
    {
        "messages": [
            {"role": "system", "content": "<system prompt>"},
            {"role": "user",   "content": "<question>"},
            {"role": "assistant", "content": "<answer>"}
        ],
        "quality_score": 0.9
    }
    """
    question = (row.get("question") or "").strip()
    answer   = (row.get("answer") or "").strip()
    label    = (row.get("feedback_label") or "").lower()

    if not question or not answer:
        return None

    quality_score = LABEL_SCORE_MAP.get(label, 0.5)

    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are KavinBase, a senior engineering assistant. "
                    "Answer using only the provided document context. "
                    "Cite page numbers. Copy numerical values and codes verbatim."
                ),
            },
            {"role": "user",      "content": question},
            {"role": "assistant", "content": answer},
        ],
        "quality_score": quality_score,
        "feedback_label": label,
        "company_document_id": row.get("company_document_id"),
        "revision_number": row.get("revision_number"),
    }


# ============================================================
# EXPORT API
# ============================================================

def export_rlhf_dataset(
    output_path: str,
    company_document_id: Optional[str] = None,
    min_quality_score: float = 0.5,
    limit: int = 5000,
) -> Dict[str, Any]:
    """
    Export training data to a JSONL file.

    Only includes examples above min_quality_score threshold.
    Returns stats dict. Never raises.

    Args:
        output_path: file path to write JSONL (e.g., "training_data.jsonl")
        company_document_id: filter to one document (None = all documents)
        min_quality_score: skip low-quality examples (default 0.5)
        limit: max feedback rows to process
    """
    try:
        rows = _fetch_feedback_rows(company_document_id, limit=limit)
        examples = []
        skipped = 0

        for row in rows:
            ex = _row_to_training_example(row)
            if ex is None:
                skipped += 1
                continue
            if ex["quality_score"] < min_quality_score:
                skipped += 1
                continue
            examples.append(ex)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            for ex in examples:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

        stats = {
            "total_rows": len(rows),
            "exported": len(examples),
            "skipped": skipped,
            "output_path": output_path,
            "exported_at": datetime.utcnow().isoformat(),
        }
        print(f"[RLHF] Exported {len(examples)} training examples to {output_path}")
        return stats

    except Exception as e:
        print(f"[RLHF] export failed (non-fatal): {e}")
        return {"error": str(e), "exported": 0}


def get_dataset_stats(
    company_document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return dataset readiness stats without exporting.
    Useful for dashboard display.
    """
    try:
        rows = _fetch_feedback_rows(company_document_id, limit=10000)
        label_counts: Dict[str, int] = {}
        total_quality = 0.0

        for row in rows:
            label = (row.get("feedback_label") or "unknown").lower()
            label_counts[label] = label_counts.get(label, 0) + 1
            total_quality += LABEL_SCORE_MAP.get(label, 0.5)

        positive = sum(v for k, v in label_counts.items()
                       if k in ("correct", "helpful", "thumbs_up"))
        negative = sum(v for k, v in label_counts.items()
                       if k in ("incorrect", "hallucination"))

        return {
            "total_samples": len(rows),
            "positive_samples": positive,
            "negative_samples": negative,
            "label_distribution": label_counts,
            "avg_quality_score": round(total_quality / max(len(rows), 1), 3),
            "ready_for_finetuning": positive >= 100,
        }
    except Exception as e:
        print(f"[RLHF] stats failed (non-fatal): {e}")
        return {"total_samples": 0, "ready_for_finetuning": False}
