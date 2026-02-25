# backend/llm/few_shot.py

"""
Dynamic Few-Shot Example Injection

Purpose:
- Select the most relevant Q&A examples from past correct answers
- Inject them into the LLM prompt to guide answer style and format
- Improves answer quality without retraining (in-context learning)

Design Rules:
- Examples sourced from retrieval_feedback (label = 'correct' or 'helpful')
- Similarity: keyword overlap between current question and example questions
- Max 2 examples to avoid prompt bloat
- MUST NEVER raise — gracefully returns empty list on any error
- Must NOT inject examples from different documents (doc isolation)
"""

import re
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
import os


# ============================================================
# CONFIG
# ============================================================

FEWSHOT_DB_URL = os.getenv(
    "CHAT_DB_URL",
    "postgresql://postgres:1@localhost:5432/chat_memory_db",
)

MAX_EXAMPLES = 2
MIN_TOKEN_OVERLAP = 2       # minimum keyword overlap to include an example
MAX_EXAMPLE_LEN  = 300      # max chars per example answer in prompt


# ============================================================
# DB CONNECTION
# ============================================================

@contextmanager
def _get_conn():
    conn = None
    try:
        conn = psycopg2.connect(FEWSHOT_DB_URL)
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
# KEYWORD SCORING
# ============================================================

_STOPWORDS = {
    "what", "is", "the", "are", "a", "an", "of", "in", "for",
    "to", "and", "or", "how", "much", "many", "does", "can",
    "which", "who", "when", "where", "why",
}

def _keywords(text: str) -> set:
    tokens = re.findall(r"[a-zA-Z0-9]{2,}", text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


def _similarity(q1: str, q2: str) -> int:
    """Count shared keyword tokens between two questions."""
    return len(_keywords(q1) & _keywords(q2))


# ============================================================
# EXAMPLE FETCHING
# ============================================================

def _fetch_positive_examples(
    company_document_id: str,
    revision_number: str,
) -> List[Dict[str, Any]]:
    """Fetch recent correct/helpful Q&A pairs for this document."""
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT question, answer
                    FROM retrieval_feedback
                    WHERE company_document_id = %s
                      AND revision_number = %s
                      AND feedback_label IN ('correct', 'helpful', 'thumbs_up')
                      AND LENGTH(answer) > 20
                    ORDER BY created_at DESC
                    LIMIT 50
                    """,
                    (company_document_id, str(revision_number)),
                )
                return [dict(r) for r in (cur.fetchall() or [])]
    except Exception as e:
        print(f"[FEW-SHOT] fetch failed (non-fatal): {e}")
        return []


# ============================================================
# PUBLIC API
# ============================================================

def get_few_shot_examples(
    question: str,
    company_document_id: str,
    revision_number: str,
) -> List[Dict[str, str]]:
    """
    Return up to MAX_EXAMPLES relevant Q&A pairs for few-shot injection.

    Returns list of {"question": ..., "answer": ...} dicts.
    Empty list if no suitable examples found.
    Never raises.
    """
    try:
        candidates = _fetch_positive_examples(company_document_id, revision_number)
        if not candidates:
            return []

        # Score and rank by keyword overlap with current question
        scored = []
        for ex in candidates:
            score = _similarity(question, ex.get("question", ""))
            if score >= MIN_TOKEN_OVERLAP:
                scored.append((score, ex))

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:MAX_EXAMPLES]

        return [
            {
                "question": ex["question"].strip(),
                "answer": (ex["answer"] or "")[:MAX_EXAMPLE_LEN].strip(),
            }
            for _, ex in top
        ]

    except Exception as e:
        print(f"[FEW-SHOT] get_few_shot_examples failed (non-fatal): {e}")
        return []


def format_few_shot_block(examples: List[Dict[str, str]]) -> str:
    """
    Format examples into a prompt-injectable string block.

    Returns empty string if no examples.
    """
    if not examples:
        return ""

    lines = ["REFERENCE EXAMPLES (from verified past answers):"]
    for i, ex in enumerate(examples, 1):
        lines.append(f"Example {i}:")
        lines.append(f"  Q: {ex['question']}")
        lines.append(f"  A: {ex['answer']}")
    lines.append("")  # blank separator

    return "\n".join(lines)
