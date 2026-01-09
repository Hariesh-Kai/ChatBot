# backend/state/job_state.py

"""
JOB / DOCUMENT STATE MANAGEMENT (RAG-SAFE)

Single source of truth for:
- job lifecycle
- session ↔ job binding
- active document persistence
- metadata readiness
- error handling

GUARANTEES:
- One active document per session
- RAG survives backend restart (via DB persistence)
- ERROR jobs never block chat
- READY jobs are immutable
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from threading import Lock

from backend.state.abort_signals import reset_abort_signal

# ✅ NEW: Import DB functions to persist state across reloads
from backend.memory.pg_memory import (
    save_active_document as db_save_active_doc,
    get_active_document as db_get_active_doc,
    clear_active_document as db_clear_active_doc,
)

# ==========================================================
# STATUS CONSTANTS
# ==========================================================

STATUS_WAIT_FOR_METADATA = "WAIT_FOR_METADATA"
STATUS_READY = "READY"
STATUS_ERROR = "ERROR"

# ==========================================================
# JOB STATE MODEL
# ==========================================================

@dataclass
class JobState:
    job_id: str
    status: str
    session_id: Optional[str] = None

    metadata: Dict[str, Any] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)

    error: Optional[str] = None


# ==========================================================
# IN-MEMORY STORES (PROCESS LOCAL - JOBS ARE TRANSIENT)
# ==========================================================

_JOB_STORE: Dict[str, JobState] = {}
_SESSION_JOB_MAP: Dict[str, str] = {}

_LOCK = Lock()

# ==========================================================
# INTERNAL HELPERS
# ==========================================================

def _remove_job(job_id: str) -> None:
    job = _JOB_STORE.pop(job_id, None)
    if job and job.session_id:
        _SESSION_JOB_MAP.pop(job.session_id, None)


# ==========================================================
# ACTIVE DOCUMENT (🔥 FIXED: CONNECTED TO DB)
# ==========================================================

def save_active_document(
    *,
    session_id: str,
    company_document_id: str,
    revision_number: int,
    filename: Optional[str] = None,
) -> None:
    """
    Persist the active document for a session.
    Routes to Postgres so it survives server reloads.
    """
    # We cast revision to string here to match the DB schema TEXT column
    db_save_active_doc(
        session_id=session_id,
        company_document_id=company_document_id,
        revision_number=str(revision_number),
    )


def get_active_document(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Restore active document for a session from Postgres.
    """
    return db_get_active_doc(session_id)


def clear_active_document(session_id: str) -> None:
    """
    Remove active document binding from Postgres.
    """
    db_clear_active_doc(session_id)


# ==========================================================
# JOB CREATION & BINDING
# ==========================================================

def create_job(
    *,
    job_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    missing_fields: Optional[List[str]] = None,
    session_id: Optional[str] = None,
) -> JobState:
    """
    Create a new job.

    RULE:
    - Replaces any existing job bound to the session
    """

    with _LOCK:
        if session_id and session_id in _SESSION_JOB_MAP:
            old_job_id = _SESSION_JOB_MAP.get(session_id)
            old_job = _JOB_STORE.get(old_job_id)

            if old_job:
                old_job.status = STATUS_ERROR
                old_job.error = "Replaced by new job"

            _SESSION_JOB_MAP.pop(session_id, None)
            _JOB_STORE.pop(old_job_id, None)

        metadata = dict(metadata or {})
        missing_fields = list(missing_fields or [])

        status = (
            STATUS_WAIT_FOR_METADATA
            if missing_fields
            else STATUS_READY
        )

        job = JobState(
            job_id=job_id,
            status=status,
            session_id=session_id,
            metadata=metadata,
            missing_fields=missing_fields,
        )

        _JOB_STORE[job_id] = job

        if session_id:
            _SESSION_JOB_MAP[session_id] = job_id

        return job


def bind_session_to_job(session_id: str, job_id: str) -> None:
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            raise KeyError("Job not found")

        old_job_id = _SESSION_JOB_MAP.get(session_id)
        if old_job_id and old_job_id != job_id:
            _remove_job(old_job_id)

        job.session_id = session_id
        _SESSION_JOB_MAP[session_id] = job_id


# ==========================================================
# JOB LOOKUP (PUBLIC API)
# ==========================================================

def get_job_state(identifier: str) -> Optional[JobState]:
    """
    Resolve job by:
    - job_id OR
    - session_id

    ERROR jobs are ignored.
    """

    with _LOCK:
        job = _JOB_STORE.get(identifier)
        if job and job.status != STATUS_ERROR:
            return job

        job_id = _SESSION_JOB_MAP.get(identifier)
        if job_id:
            job = _JOB_STORE.get(job_id)
            if job and job.status != STATUS_ERROR:
                return job

        return None


# ==========================================================
# METADATA UPDATES
# ==========================================================

def update_job_metadata(
    job_id: str,
    updated_metadata: Dict[str, Any],
) -> JobState:
    """
    Merge user metadata and advance state automatically.
    """

    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            raise KeyError("Job not found")

        if job.status not in (STATUS_WAIT_FOR_METADATA, STATUS_READY):
            raise RuntimeError(
                f"Cannot update metadata for job in state '{job.status}'"
            )

        job.metadata.update(updated_metadata)

        job.missing_fields = [
            f for f in job.missing_fields
            if f not in updated_metadata
        ]

        if not job.missing_fields:
            job.status = STATUS_READY

        return job


# ==========================================================
# STATE TRANSITIONS
# ==========================================================

def mark_job_ready(job_id: str) -> None:
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            raise KeyError("Job not found")

        job.status = STATUS_READY
        job.error = None


def mark_job_error(job_id: str, error: str) -> None:
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            return

        job.status = STATUS_ERROR
        job.error = str(error)

        if job.session_id:
            _SESSION_JOB_MAP.pop(job.session_id, None)
            clear_active_document(job.session_id)


# ==========================================================
# CLEANUP
# ==========================================================

def clear_job_for_session(session_id: str) -> None:
    with _LOCK:
        job_id = _SESSION_JOB_MAP.pop(session_id, None)
        if job_id:
            _JOB_STORE.pop(job_id, None)

        clear_active_document(session_id)

    reset_abort_signal(session_id)


def delete_job(job_id: str) -> None:
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if job and job.session_id:
            clear_active_document(job.session_id)

        _remove_job(job_id)