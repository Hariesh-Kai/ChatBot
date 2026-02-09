# backend/state/rag_overrides.py

from __future__ import annotations

from threading import Lock
from typing import List, Optional

"""
RAG override controls (session/user disable).

Used by the Developer Dashboard to disable retrieval for:
- specific sessions
- specific users
"""

_LOCK = Lock()
_DISABLED_SESSIONS: set[str] = set()
_DISABLED_USERS: set[str] = set()


def disable_rag_for_session(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    with _LOCK:
        _DISABLED_SESSIONS.add(sid)


def enable_rag_for_session(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    with _LOCK:
        _DISABLED_SESSIONS.discard(sid)


def disable_rag_for_user(username: str) -> None:
    uname = (username or "").strip().lower()
    if not uname:
        return
    with _LOCK:
        _DISABLED_USERS.add(uname)


def enable_rag_for_user(username: str) -> None:
    uname = (username or "").strip().lower()
    if not uname:
        return
    with _LOCK:
        _DISABLED_USERS.discard(uname)


def is_rag_disabled(session_id: Optional[str] = None, username: Optional[str] = None) -> bool:
    sid = (session_id or "").strip()
    uname = (username or "").strip().lower()
    with _LOCK:
        if sid and sid in _DISABLED_SESSIONS:
            return True
        if uname and uname in _DISABLED_USERS:
            return True
    return False


def list_overrides() -> dict:
    with _LOCK:
        disabled_sessions = sorted(_DISABLED_SESSIONS)
        disabled_users = sorted(_DISABLED_USERS)
        return {
            "enabled": bool(disabled_sessions or disabled_users),
            "disabled_sessions": disabled_sessions,
            "disabled_users": disabled_users,
        }
