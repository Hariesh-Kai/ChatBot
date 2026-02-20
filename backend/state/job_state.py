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
- ERROR jobs are visible to callers
- READY jobs are immutable
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from threading import Lock

# Abort reset must be called ONLY when a job is fully done
from backend.state.abort_signals import reset_abort_signal, signal_abort

# DB-backed active document persistence
from backend.memory.pg_memory import (
    save_active_document as db_save_active_doc,
    get_active_document as db_get_active_doc,
    clear_active_document as db_clear_active_doc,
)
from backend.state.job_persistence import (
    upsert_job_run,
    delete_job_run,
    get_job_run,
)

# ==========================================================
# STATUS CONSTANTS
# ==========================================================

STATUS_WAIT_FOR_METADATA = "WAIT_FOR_METADATA"
STATUS_PROCESSING = "PROCESSING"
STATUS_READY = "READY"
STATUS_ERROR = "ERROR"

TERMINAL_STATES = {STATUS_READY, STATUS_ERROR}

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
    progress: int = 0
    progress_label: Optional[str] = None


# ==========================================================
# IN-MEMORY STORES (PROCESS-LOCAL)
# ==========================================================

_JOB_STORE: Dict[str, JobState] = {}
_SESSION_JOB_MAP: Dict[str, str] = {}

_LOCK = Lock()


def _persist_job_snapshot(job: JobState, *, replace_error: bool = True) -> None:
    try:
        upsert_job_run(
            job_id=job.job_id,
            session_id=job.session_id,
            status=job.status,
            progress=job.progress,
            progress_label=job.progress_label,
            error=job.error,
            replace_error=replace_error,
            metadata=dict(job.metadata or {}),
            missing_fields=list(job.missing_fields or []),
        )
    except Exception as e:
        print(f"[JOB-STATE] Persist snapshot failed job_id={job.job_id}: {e}")


# ==========================================================
# INTERNAL HELPERS
# ==========================================================

def _remove_job(job_id: str) -> None:
    """
    Remove job from all in-memory mappings.
    Does NOT touch abort signals.
    """
    job = _JOB_STORE.pop(job_id, None)
    if job and job.session_id:
        _SESSION_JOB_MAP.pop(job.session_id, None)


# ==========================================================
# ACTIVE DOCUMENT (DB-BACKED)
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
    Survives backend restarts.
    """
    db_save_active_doc(
        session_id=session_id,
        company_document_id=company_document_id,
        revision_number=str(revision_number),
        filename=filename,
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
    - Replaces any existing job bound to the same session
    - Old job is marked ERROR explicitly
    """

    with _LOCK:
        if session_id and session_id in _SESSION_JOB_MAP:
            old_job_id = _SESSION_JOB_MAP.pop(session_id)
            old_job = _JOB_STORE.pop(old_job_id, None)

            if old_job:
                signal_abort(old_job.session_id)
                old_job.status = STATUS_ERROR
                old_job.error = "Replaced by new job"
                old_job.progress_label = "Replaced by new job."
                _persist_job_snapshot(old_job, replace_error=True)
                if old_job.session_id:
                    clear_active_document(old_job.session_id)

        metadata = dict(metadata or {})
        missing_fields = list(missing_fields or [])

        status = (
            STATUS_WAIT_FOR_METADATA
            if missing_fields
            else STATUS_PROCESSING
        )

        job = JobState(
            job_id=job_id,
            status=status,
            session_id=session_id,
            metadata=metadata,
            missing_fields=missing_fields,
            progress=35 if status == STATUS_WAIT_FOR_METADATA else 40,
            progress_label=(
                "Waiting for metadata."
                if status == STATUS_WAIT_FOR_METADATA
                else "Queued for processing."
            ),
        )

        _JOB_STORE[job_id] = job

        if session_id:
            _SESSION_JOB_MAP[session_id] = job_id

        _persist_job_snapshot(job, replace_error=True)

        return job


def bind_session_to_job(session_id: str, job_id: str) -> None:
    """
    Explicitly bind a session to an existing job.
    """
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            raise KeyError("Job not found")

        old_job_id = _SESSION_JOB_MAP.get(session_id)
        if old_job_id and old_job_id != job_id:
            signal_abort(session_id)
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

    🔥 FIX:
    - ERROR jobs are RETURNED (not hidden)
    """
    with _LOCK:
        job = _JOB_STORE.get(identifier)
        if job:
            return job

        job_id = _SESSION_JOB_MAP.get(identifier)
        if job_id:
            return _JOB_STORE.get(job_id)

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

    🔥 FIX:
    - Metadata can ONLY be updated in WAIT_FOR_METADATA
    - READY jobs are immutable
    """

    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            raise KeyError("Job not found")

        if job.status != STATUS_WAIT_FOR_METADATA:
            raise RuntimeError(
                f"Cannot update metadata for job in state '{job.status}'"
            )

        job.metadata.update(updated_metadata)

        job.missing_fields = [
            f for f in job.missing_fields
            if f not in updated_metadata
        ]

        if not job.missing_fields:
            job.status = STATUS_PROCESSING
            job.progress = max(job.progress, 40)
            job.progress_label = "Metadata confirmed. Queued for processing."

        _persist_job_snapshot(job, replace_error=False)
        return job


# ==========================================================
# STATE TRANSITIONS
# ==========================================================

def mark_job_ready(job_id: str) -> None:
    """
    Mark job READY explicitly.
    """
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if job:
            if job.status != STATUS_PROCESSING:
                raise RuntimeError(
                    f"Cannot mark job READY from state '{job.status}'"
                )
            job.status = STATUS_READY
            job.error = None
            job.progress = 100
            job.progress_label = "Document ready."
            _persist_job_snapshot(job, replace_error=True)
            return

    # Cross-process worker path: job may not exist in this process memory.
    try:
        upsert_job_run(
            job_id=job_id,
            status=STATUS_READY,
            progress=100,
            progress_label="Document ready.",
            error=None,
            replace_error=True,
        )
    except Exception as e:
        print(f"[JOB-STATE] Persist READY failed job_id={job_id}: {e}")


def mark_job_error(job_id: str, error: str) -> None:
    """
    Mark job ERROR and clean session bindings.
    """
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if job:
            job.status = STATUS_ERROR
            job.error = str(error)
            job.progress_label = "Processing failed."
            _persist_job_snapshot(job, replace_error=True)

            if job.session_id:
                _SESSION_JOB_MAP.pop(job.session_id, None)
                clear_active_document(job.session_id)
            return

    try:
        persisted = get_job_run(job_id)
        upsert_job_run(
            job_id=job_id,
            session_id=(
                str(persisted.get("session_id"))
                if isinstance(persisted, dict) and persisted.get("session_id") is not None
                else None
            ),
            status=STATUS_ERROR,
            progress_label="Processing failed.",
            error=str(error),
            replace_error=True,
        )
        if isinstance(persisted, dict) and persisted.get("session_id"):
            clear_active_document(str(persisted.get("session_id")))
    except Exception as e:
        print(f"[JOB-STATE] Persist ERROR failed job_id={job_id}: {e}")


# ==========================================================
# CLEANUP (CALL ONLY AFTER STREAM END)
# ==========================================================

def clear_job_for_session(session_id: str) -> None:
    """
    Clear job bound to a session.

    IMPORTANT:
    - Must be called ONLY after stream fully finishes
    - Safe place to reset abort signal
    """
    with _LOCK:
        job_id = _SESSION_JOB_MAP.pop(session_id, None)
        if job_id:
            _JOB_STORE.pop(job_id, None)

        # Keep the active document binding so the user can continue chatting
        # about the same PDF across multiple turns (ChatGPT-style).
        # Active doc is cleared only when replaced by a new upload or explicitly cleared.

    # 🔥 Abort reset happens ONLY here
    reset_abort_signal(session_id)


def set_job_progress(
    job_id: str,
    *,
    value: Optional[int] = None,
    label: Optional[str] = None,
) -> None:
    """
    Update progress fields for a job.
    Safe no-op if job does not exist.
    """
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        safe_value: Optional[int] = None
        if value is not None:
            try:
                parsed = int(value)
            except Exception:
                parsed = 0
            safe_value = max(0, min(100, parsed))

        if job:
            if safe_value is not None:
                job.progress = safe_value
            if label is not None:
                job.progress_label = str(label)
            _persist_job_snapshot(job, replace_error=False)
            return

    try:
        upsert_job_run(
            job_id=job_id,
            progress=safe_value,
            progress_label=str(label) if label is not None else None,
            replace_error=False,
        )
    except Exception as e:
        print(f"[JOB-STATE] Persist progress failed job_id={job_id}: {e}")


def delete_job(job_id: str) -> None:
    """
    Delete a job explicitly by job_id.
    """
    with _LOCK:
        job = _JOB_STORE.get(job_id)
        if job and job.session_id:
            clear_active_document(job.session_id)

        _remove_job(job_id)
    try:
        delete_job_run(job_id)
    except Exception as e:
        print(f"[JOB-STATE] Delete persisted job failed job_id={job_id}: {e}")
