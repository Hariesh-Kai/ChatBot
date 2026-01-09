# backend/api/abort.py
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Abort manager (in-memory + optional redis) — same API as your state lib
from backend.state.abort_signals import (
    signal_abort,
    reset_abort_signal,
    is_aborted,
    get_abort_event,
)

router = APIRouter(prefix="/abort", tags=["Control"])


class AbortRequest(BaseModel):
    session_id: str
    # optional reason for logs/debug
    reason: Optional[str] = None


@router.post("/")
def abort(req: AbortRequest):
    """
    Trigger abort for a session.
    Example body: { "session_id": "sess-123" }
    """
    sid = (req.session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id required")

    # Fire the kill switch
    signal_abort(sid)

    # Defensive: ensure event exists so other parts can check immediately
    _ = get_abort_event(sid)

    return {"ok": True, "session_id": sid, "aborted": True}


@router.post("/reset")
def abort_reset(req: AbortRequest):
    """
    Reset abort signal for the session so a new request can start.
    Use with caution (only for admin / frontend flow when starting new request).
    """
    sid = (req.session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id required")

    reset_abort_signal(sid)
    return {"ok": True, "session_id": sid, "aborted": False}


@router.get("/{session_id}")
def abort_status(session_id: str):
    """
    Check whether a session is currently aborted.
    """
    sid = (session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id required")

    return {"session_id": sid, "aborted": bool(is_aborted(sid))}




