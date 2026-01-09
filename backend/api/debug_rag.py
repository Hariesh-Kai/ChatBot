# backend/api/debug_rag.py

"""
RAG DEBUG ENDPOINT (STEP 7.3)

Purpose:
- Expose last RAG execution snapshot for a session
- Read-only
- Human / developer debugging only

Rules:
- ❌ No LLM calls
- ❌ No DB writes
- ❌ No content leakage
- ✅ Redis only
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from backend.memory.redis_memory import get_rag_debug

# ============================================================
# API ROUTER
# ============================================================

router = APIRouter(prefix="/debug", tags=["RAG Debug"])


# ============================================================
# DEBUG ENDPOINT
# ============================================================

@router.get("/rag/{session_id}")
def debug_rag(session_id: str) -> Dict[str, Any]:
    """
    Fetch the last RAG debug snapshot for a session.

    Returns:
    - original_question
    - rewritten_question
    - topic_hint
    - intent
    - retrieved_chunks (ids + sections)
    - used_chunk_ids
    - confidence

    This endpoint is READ-ONLY.
    """

    if not session_id or not session_id.strip():
        raise HTTPException(
            status_code=400,
            detail="session_id is required",
        )

    snapshot = get_rag_debug(session_id)

    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail="No RAG debug data found for this session",
        )

    return {
        "session_id": session_id,
        "rag_debug": snapshot,
    }
