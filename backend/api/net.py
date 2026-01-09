# backend/api/net.py

"""
Net status & safety endpoints.

Responsibilities:
- Expose Net availability to frontend
- Show active provider & model (NO keys)
- Enforce simple runtime rate limits (best-effort)
"""
from threading import Lock

import time
from typing import Dict

from fastapi import APIRouter, HTTPException

from backend.llm.net_models import (
    get_active_net_provider,
    resolve_active_net_model,
    NET_MAX_REQUESTS_PER_MIN,
    NET_MAX_CONCURRENT_STREAMS,
)
from backend.secrets.net_keys import has_net_api_key

# ============================================================
# ROUTER
# ============================================================

router = APIRouter(prefix="/net", tags=["Net"])

# ============================================================
# IN-MEMORY RATE LIMIT STATE (PROCESS-LOCAL)
# ============================================================

# session_id -> [timestamps]
_REQUEST_LOG: Dict[str, list[float]] = {}

# active net streams
_ACTIVE_STREAMS = 0


# ============================================================
# HELPERS
# ============================================================

def _prune_old(ts_list: list[float], window: int = 60) -> list[float]:
    now = time.time()
    return [t for t in ts_list if now - t <= window]


def check_rate_limit(session_id: str):
    """
    Best-effort per-session rate limit.
    """
    global _REQUEST_LOG

    now = time.time()
    logs = _REQUEST_LOG.get(session_id, [])
    logs = _prune_old(logs)

    if len(logs) >= NET_MAX_REQUESTS_PER_MIN:
        raise HTTPException(
            status_code=429,
            detail="Net rate limit exceeded. Please wait.",
        )

    logs.append(now)
    _REQUEST_LOG[session_id] = logs

_STREAM_LOCK = Lock()

def acquire_stream_slot():
    """
    Guard concurrent Net streams.
    """
    global _ACTIVE_STREAMS
    
    with _STREAM_LOCK:
        if _ACTIVE_STREAMS >= NET_MAX_CONCURRENT_STREAMS:
            raise HTTPException(
                status_code=429,
                detail="Too many active Net streams. Try again shortly.",
            )
        _ACTIVE_STREAMS += 1


def release_stream_slot():
    global _ACTIVE_STREAMS
    with _STREAM_LOCK:
        _ACTIVE_STREAMS = max(0, _ACTIVE_STREAMS - 1)


# ============================================================
# STATUS ENDPOINT
# ============================================================

@router.get("/status")
def net_status():
    """
    Lightweight Net availability check.
    SAFE for UI polling.
    """
    try:
        provider = get_active_net_provider()
        model = resolve_active_net_model()
    except Exception:
        return {
            "available": False,
            "provider": None,
            "model": None,
        }

    return {
        "available": has_net_api_key(provider),
        "provider": provider,
        "model": model,
        "max_requests_per_min": NET_MAX_REQUESTS_PER_MIN,
        "max_concurrent_streams": NET_MAX_CONCURRENT_STREAMS,
    }
