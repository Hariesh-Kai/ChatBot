# backend/learning/retrieval_policy.py

"""
Retrieval Policy (Learning-Aware)

Purpose:
- Define how learning signals influence retrieval
- Feedback-driven chunk boosting: chunks that received 👍 rise higher
- Policy is the ONLY allowed place retrieval behavior may be modified

Design Rules:
- Must NEVER raise to caller
- Must NEVER mutate input list (returns new list)
- Boosting is additive and capped — no chunk can dominate unfairly
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass


# =========================================================
# CONFIG FLAGS
# =========================================================

ENABLE_RETRIEVAL_POLICY = True      # Now enabled
BOOST_PER_POSITIVE_FEEDBACK = 0.15  # Score added per positive feedback event
MAX_BOOST = 0.50                    # Cap boost to prevent a single chunk dominating
MIN_FEEDBACKS_FOR_BOOST = 1         # At least 1 positive feedback to boost


# =========================================================
# POLICY RESULT MODEL
# =========================================================

@dataclass
class PolicyResult:
    """
    Result returned by a retrieval policy.
    Forces transparency: original vs adjusted chunks.
    """
    chunks: List[Dict[str, Any]]
    policy_applied: bool
    reason: Optional[str] = None


# =========================================================
# FEEDBACK BOOSTING (reads from DB)
# =========================================================

def get_boosted_chunk_ids(
    company_document_id: str,
    revision_number: str,
) -> Dict[str, float]:
    """
    Query retrieval_feedback table for chunks that received positive feedback.
    Returns {chunk_id: boost_score} where boost_score is capped at MAX_BOOST.

    Always returns empty dict on error — never raises.
    """
    boosted: Dict[str, float] = {}

    try:
        from backend.learning.retrieval_feedback import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT unnest(chunk_ids) AS chunk_id, COUNT(*) AS hit_count
                    FROM retrieval_feedback
                    WHERE company_document_id = %s
                      AND revision_number = %s
                      AND feedback_label IN ('correct', 'helpful', 'thumbs_up')
                      AND chunk_ids IS NOT NULL
                    GROUP BY chunk_id
                    HAVING COUNT(*) >= %s
                    """,
                    (
                        company_document_id,
                        str(revision_number),
                        MIN_FEEDBACKS_FOR_BOOST,
                    ),
                )
                rows = cur.fetchall() or []

        for row in rows:
            chunk_id = row[0]
            hit_count = int(row[1])
            boost = min(hit_count * BOOST_PER_POSITIVE_FEEDBACK, MAX_BOOST)
            boosted[chunk_id] = boost

    except Exception as e:
        print(f"[POLICY] get_boosted_chunk_ids failed (non-fatal): {e}")

    return boosted


# =========================================================
# SCORE BOOST APPLICATION
# =========================================================

def _apply_boost(
    chunks: List[Dict[str, Any]],
    boosted: Dict[str, float],
) -> List[Dict[str, Any]]:
    """
    Apply feedback boost scores to chunks and re-sort.
    Returns a NEW list — does not mutate input.
    """
    if not boosted:
        return chunks

    boosted_chunks = []
    for c in chunks:
        chunk_copy = dict(c)
        cid = chunk_copy.get("id")
        if cid and cid in boosted:
            old_score = float(chunk_copy.get("score", 0.0))
            chunk_copy["score"] = round(old_score + boosted[cid], 4)
            chunk_copy["_feedback_boosted"] = True
        boosted_chunks.append(chunk_copy)

    # Re-sort by score descending after boost
    boosted_chunks.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return boosted_chunks


# =========================================================
# MAIN POLICY FUNCTION
# =========================================================

def apply_retrieval_policy(
    *,
    question: str,
    rag_chunks: List[Dict[str, Any]],
    company_document_id: str,
    revision_number: str,
    confidence: Optional[float] = None,
) -> PolicyResult:
    """
    Apply retrieval policy to RAG chunks.

    GUARANTEES:
    - Does NOT mutate input chunks
    - Safe to call unconditionally
    - Never raises
    """

    if not ENABLE_RETRIEVAL_POLICY:
        return PolicyResult(
            chunks=rag_chunks,
            policy_applied=False,
            reason="policy_disabled",
        )

    if not rag_chunks:
        return PolicyResult(
            chunks=rag_chunks,
            policy_applied=False,
            reason="no_chunks",
        )

    try:
        # Fetch feedback-boosted chunk IDs
        boosted = get_boosted_chunk_ids(company_document_id, revision_number)

        if not boosted:
            return PolicyResult(
                chunks=rag_chunks,
                policy_applied=False,
                reason="no_feedback_data",
            )

        adjusted = _apply_boost(rag_chunks, boosted)
        boosted_count = sum(1 for c in adjusted if c.get("_feedback_boosted"))

        print(f"[POLICY] Applied feedback boost to {boosted_count}/{len(adjusted)} chunks")

        return PolicyResult(
            chunks=adjusted,
            policy_applied=True,
            reason=f"feedback_boost:{boosted_count}_chunks",
        )

    except Exception as e:
        print(f"[POLICY] apply_retrieval_policy error (non-fatal): {e}")
        return PolicyResult(
            chunks=rag_chunks,
            policy_applied=False,
            reason=f"policy_error:{e}",
        )


# =========================================================
# SAFETY CHECKS
# =========================================================

def validate_policy_result(result: PolicyResult) -> bool:
    """Defensive validation before using policy output."""
    if not isinstance(result.chunks, list):
        return False
    for c in result.chunks:
        if not isinstance(c, dict):
            return False
        if "content" not in c:
            return False
    return True



# =========================================================
# POLICY RESULT MODEL
# =========================================================

@dataclass
class PolicyResult:
    """
    Result returned by a retrieval policy.

    This structure forces transparency:
    - original chunks
    - adjusted chunks (if any)
    - reason for adjustment
    """
    chunks: List[Dict[str, Any]]
    policy_applied: bool
    reason: Optional[str] = None


# =========================================================
# BASE POLICY (NO-OP)
# =========================================================

def apply_retrieval_policy(
    *,
    question: str,
    rag_chunks: List[Dict[str, Any]],
    company_document_id: str,
    revision_number: str,
    confidence: Optional[float] = None,
) -> PolicyResult:
    """
    Apply retrieval policy to RAG chunks.

    GUARANTEES:
    - Does NOT mutate input chunks
    - Does NOT reorder unless enabled
    - Safe to call unconditionally

    This function is the ONLY allowed place
    where retrieval behavior may be modified.
    """

    # -----------------------------------------------------
    # 🔒 POLICY DISABLED → PASS THROUGH
    # -----------------------------------------------------
    if not ENABLE_RETRIEVAL_POLICY:
        return PolicyResult(
            chunks=rag_chunks,
            policy_applied=False,
            reason="policy_disabled",
        )

    # -----------------------------------------------------
    # FUTURE POLICIES GO BELOW (EXPLICIT)
    # -----------------------------------------------------

    # Example (INTENTIONALLY COMMENTED):
    #
    # if confidence is not None and confidence < 0.3:
    #     # reweight chunks, widen retrieval, etc.
    #     pass

    return PolicyResult(
        chunks=rag_chunks,
        policy_applied=False,
        reason="no_policy_matched",
    )


# =========================================================
# SAFETY CHECKS (OPTIONAL)
# =========================================================

def validate_policy_result(result: PolicyResult) -> bool:
    """
    Defensive validation before using policy output.
    """
    if not isinstance(result.chunks, list):
        return False

    for c in result.chunks:
        if not isinstance(c, dict):
            return False
        if "content" not in c:
            return False

    return True


# =========================================================
# FINAL POLICY ENTRYPOINT
# =========================================================

def apply_retrieval_policy(
    *,
    question: str,
    rag_chunks: List[Dict[str, Any]],
    company_document_id: str,
    revision_number: str,
    confidence: Optional[float] = None,
) -> PolicyResult:
    """
    Final retrieval policy entrypoint.

    This definition intentionally sits at the end of the module so duplicate
    legacy stubs above cannot override the active feedback-aware policy.
    """

    if not ENABLE_RETRIEVAL_POLICY:
        return PolicyResult(
            chunks=rag_chunks,
            policy_applied=False,
            reason="policy_disabled",
        )

    if not rag_chunks:
        return PolicyResult(
            chunks=rag_chunks,
            policy_applied=False,
            reason="no_chunks",
        )

    try:
        boosted = get_boosted_chunk_ids(company_document_id, revision_number)
        if not boosted:
            return PolicyResult(
                chunks=rag_chunks,
                policy_applied=False,
                reason="no_feedback_data",
            )

        adjusted = _apply_boost(rag_chunks, boosted)
        boosted_count = sum(1 for c in adjusted if c.get("_feedback_boosted"))
        print(f"[POLICY] Applied feedback boost to {boosted_count}/{len(adjusted)} chunks")
        return PolicyResult(
            chunks=adjusted,
            policy_applied=True,
            reason=f"feedback_boost:{boosted_count}_chunks",
        )
    except Exception as e:
        print(f"[POLICY] apply_retrieval_policy error (non-fatal): {e}")
        return PolicyResult(
            chunks=rag_chunks,
            policy_applied=False,
            reason=f"policy_error:{e}",
        )
