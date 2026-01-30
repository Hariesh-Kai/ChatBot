# backend/learning/retrieval_policy.py

"""
Retrieval Policy (Learning-Aware, Currently Disabled)

Purpose:
- Define how learning signals MAY influence retrieval
- MUST NOT change behavior unless explicitly enabled
- Acts as a policy boundary, not logic dumping ground

Current State:
- NO-OP (pass-through)
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass


# =========================================================
# CONFIG FLAGS (EXPLICIT OPT-IN)
# =========================================================

ENABLE_RETRIEVAL_POLICY = False   # 🔒 HARD OFF by default


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
