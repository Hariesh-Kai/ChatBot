# backend/state/abort_signals.py
"""
Robust abort signal manager.

Design:
- Fast in-memory threading.Event per session
- Optional Redis-backed abort for cross-process signaling
- Redis is authoritative if enabled
- Thread-safe and stream-safe
"""

from typing import Dict, Optional, TYPE_CHECKING
import threading
import os
import traceback

if TYPE_CHECKING:
    import redis

# Optional Redis dependency
try:
    import redis as _redis_lib  # runtime import
except Exception:
    _redis_lib = None


# ============================================================
# CONFIG
# ============================================================

_USE_REDIS = os.getenv("USE_ABORT_REDIS", "0").lower() in ("1", "true", "yes")
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_REDIS_ABORT_TTL = int(os.getenv("ABORT_REDIS_TTL", "1800"))  # 30 min


# ============================================================
# REDIS CLIENT (OPTIONAL)
# ============================================================

_redis_client: Optional["redis.Redis"] = None

def _init_redis() -> Optional["redis.Redis"]:
    """
    Lazy Redis initialization.
    Safe for multi-worker environments.
    """
    global _redis_client

    if not _USE_REDIS or _redis_lib is None:
        return None

    if _redis_client:
        return _redis_client

    try:
        client = _redis_lib.from_url(
            _REDIS_URL,
            decode_responses=True,
        )
        client.ping()
        print(f"Abort signals: Redis enabled ({_REDIS_URL})")
        _redis_client = client
        return client
    except Exception as e:
        print(f"Redis unavailable ({e}) — abort signals in-memory only")
        _redis_client = None
        return None


# ============================================================
# IN-MEMORY STATE
# ============================================================

_abort_events: Dict[str, threading.Event] = {}
_lock = threading.Lock()


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _redis_key(session_id: str) -> str:
    return f"abort:{session_id}"


def _get_or_create_event(session_id: str) -> threading.Event:
    with _lock:
        ev = _abort_events.get(session_id)
        if ev is None:
            ev = threading.Event()
            _abort_events[session_id] = ev
        return ev


# ============================================================
# PUBLIC API
# ============================================================

def get_abort_event(session_id: str) -> threading.Event:
    """
    Always returns a valid threading.Event.
    If Redis indicates aborted, the event is force-set.
    """
    ev = _get_or_create_event(session_id)

    client = _init_redis()
    if client:
        try:
            if client.exists(_redis_key(session_id)):
                ev.set()
        except Exception:
            pass

    return ev


def signal_abort(session_id: str) -> None:
    """
    Trigger abort for a session.
    Redis is authoritative if enabled.
    """
    if not session_id:
        return

    client = _init_redis()

    # 🔥 Redis first (authoritative)
    if client:
        try:
            client.setex(
                _redis_key(session_id),
                _REDIS_ABORT_TTL,
                "1",
            )
        except Exception:
            print(f"Redis abort set failed for {session_id}")
            traceback.print_exc()

    # In-memory event (stream-safe)
    ev = _get_or_create_event(session_id)
    ev.set()

    print(f"[ABORT] session={session_id}")


def reset_abort_signal(session_id: str) -> None:
    """
    Clear abort state safely.

    IMPORTANT:
    - Must be called ONLY after stream fully finishes
    - Does NOT replace Event object (stream-safe)
    """
    if not session_id:
        return

    client = _init_redis()

    # Redis clear
    if client:
        try:
            client.delete(_redis_key(session_id))
        except Exception:
            print(f"Redis abort delete failed for {session_id}")
            traceback.print_exc()

    # Clear existing event (do NOT replace)
    with _lock:
        ev = _abort_events.get(session_id)
        if ev:
            ev.clear()

    print(f"[ABORT RESET] session={session_id}")


def is_aborted(session_id: str) -> bool:
    """
    Fast abort check.

    Redis (if enabled) is authoritative.
    🔥 FIX:
    - Refresh Redis TTL while abort is active
    - Cache abort locally to avoid repeated Redis hits
    """
    if not session_id:
        return False

    client = _init_redis()

    if client:
        try:
            val = client.get(_redis_key(session_id))
            if val:
                # 🔥 REFRESH TTL to prevent expiry mid-stream
                try:
                    client.expire(_redis_key(session_id), _REDIS_ABORT_TTL)
                except Exception:
                    # Abort already confirmed; TTL refresh failure must not unset abort
                    print(f"[ABORT WARNING] TTL refresh failed for {session_id}")
                    pass

                # Cache abort locally
                with _lock:
                    ev = _abort_events.get(session_id)
                    if ev is None:
                        ev = threading.Event()
                        _abort_events[session_id] = ev
                    ev.set()

                return True
        except Exception:
            pass

    # Fallback to in-memory
    with _lock:
        ev = _abort_events.get(session_id)
        return ev.is_set() if ev else False


def cleanup_session_abort(session_id: str) -> None:
    """
    Optional cleanup to free memory.

    WARNING:
    Call ONLY when:
    - job is finished
    - stream is fully closed
    - abort has been reset

    Never call during active streaming.
    """
    with _lock:
        _abort_events.pop(session_id, None)
    print(f"🧹 [ABORT CLEANUP] session={session_id}")