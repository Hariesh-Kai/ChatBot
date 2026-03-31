from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.memory.pg_memory import save_active_document
from backend.queue.celery_app import celery_app, is_celery_enabled
from backend.rag.pipeline import run_pipeline
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)
from backend.state.dev_settings import get_dev_settings
from backend.state.job_persistence import get_job_run
from backend.state.job_state import (
    get_job_state,
    mark_job_error,
    mark_job_ready,
    set_job_progress,
)
from backend.storage.minio_outbox import (
    cleanup_uploaded_local_copy,
    process_due_uploads_once,
)

_LOCK = threading.Lock()
_ACTIVE_JOBS: Dict[str, threading.Thread] = {}
_MAX_CONCURRENT_COMMITS = max(1, int(os.getenv("RAG_COMMIT_CONCURRENCY", "1")))
_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT_COMMITS)
_CELERY_TASK_NAME = os.getenv("CELERY_COMMIT_TASK_NAME", "chatui.rag.commit")


def _required_keys() -> Tuple[str, ...]:
    return (
        "pdf_path",
        "company_document_id",
        "revision_number",
        "source_file",
        "db_connection",
    )


def _resolve_commit_payload(
    *,
    job_id: str,
    session_id: Optional[str],
    final_metadata: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Dict[str, Any]]:
    clean_session_id = (session_id or "").strip() or None
    metadata = dict(final_metadata or {})

    if not metadata or not clean_session_id:
        job = get_job_state(job_id)
        if job:
            if not clean_session_id:
                clean_session_id = (job.session_id or "").strip() or None
            if not metadata:
                metadata = dict(job.metadata or {})

    if not metadata or not clean_session_id:
        persisted = get_job_run(job_id)
        if persisted:
            if not clean_session_id:
                clean_session_id = (persisted.get("session_id") or "").strip() or None
            if not metadata:
                maybe_meta = persisted.get("metadata")
                if isinstance(maybe_meta, dict):
                    metadata = dict(maybe_meta)

    missing = [k for k in _required_keys() if not metadata.get(k)]
    if missing:
        raise RuntimeError(f"Missing required metadata fields: {missing}")

    return clean_session_id, metadata


def _enqueue_celery_commit(
    *,
    job_id: str,
    session_id: Optional[str],
    final_metadata: Dict[str, Any],
) -> bool:
    if celery_app is None:
        return False

    celery_app.send_task(
        _CELERY_TASK_NAME,
        kwargs={
            "job_id": job_id,
            "session_id": session_id,
            "final_metadata": final_metadata,
        },
    )
    set_job_progress(
        job_id,
        value=max(int(final_metadata.get("progress", 40) or 40), 40),
        label="Queued on RabbitMQ worker.",
    )
    print(f"[RAG-COMMIT] Celery task queued job_id={job_id}")
    return True


def start_commit_job(
    job_id: str,
    *,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Start asynchronous commit for a job_id.
    Returns False when the same job is already running.
    """
    job_id = (job_id or "").strip()
    if not job_id:
        return False

    resolved_session_id, resolved_metadata = _resolve_commit_payload(
        job_id=job_id,
        session_id=session_id,
        final_metadata=metadata,
    )

    if is_celery_enabled():
        try:
            return _enqueue_celery_commit(
                job_id=job_id,
                session_id=resolved_session_id,
                final_metadata=resolved_metadata,
            )
        except Exception as e:
            print(f"[RAG-COMMIT] Celery queue failed, falling back to local thread: {e}")

    with _LOCK:
        existing = _ACTIVE_JOBS.get(job_id)
        if existing and existing.is_alive():
            return False

        t = threading.Thread(
            target=_run_commit,
            args=(job_id, resolved_session_id, resolved_metadata),
            daemon=True,
            name=f"rag-commit-{job_id[:8]}",
        )
        _ACTIVE_JOBS[job_id] = t
        t.start()
        return True


def run_commit_payload(
    *,
    job_id: str,
    session_id: Optional[str],
    final_metadata: Dict[str, Any],
) -> None:
    set_job_progress(
        job_id,
        value=42,
        label="Background processing started.",
    )

    job_dir = (
        Path(__file__).resolve().parents[1]
        / "tmp"
        / "jobs"
        / job_id
    )
    for evt in run_pipeline(
        pdf_path=final_metadata["pdf_path"],
        job_dir=str(job_dir),
        company_document_id=final_metadata["company_document_id"],
        db_connection=final_metadata["db_connection"],
        extra_metadata={**final_metadata, "session_id": session_id},
        mode="commit",
    ):
        if isinstance(evt, dict) and evt.get("type") == "PROGRESS":
            set_job_progress(
                job_id,
                value=evt.get("value"),
                label=evt.get("label"),
            )

    rev_text = str(final_metadata["revision_number"])
    rev_number = int(rev_text) if rev_text.isdigit() else rev_text
    try:
        settings = get_dev_settings()
    except Exception:
        settings = {}
    resolved_collection_name = normalize_collection_name(
        final_metadata.get("rag_collection_name")
        or settings.get("rag_collection_name")
        or DEFAULT_RAG_COLLECTION_NAME
    )

    if session_id:
        save_active_document(
            session_id=session_id,
            company_document_id=final_metadata["company_document_id"],
            revision_number=rev_number,
            collection_name=resolved_collection_name,
            filename=final_metadata["source_file"],
        )

    mark_job_ready(job_id)

    # Trigger one immediate outbox pass so end-to-end flow completes even when
    # local outbox thread is disabled in Celery mode.
    try:
        process_due_uploads_once(limit=1)
    except Exception as e:
        print(f"[RAG-COMMIT] Outbox tick failed job_id={job_id}: {e}")

    cleanup_uploaded_local_copy(
        company_document_id=str(final_metadata["company_document_id"]),
        revision_number=str(final_metadata["revision_number"]),
        source_file=str(final_metadata["source_file"]),
        local_path=str(final_metadata["pdf_path"]),
    )
    print(f"[RAG-COMMIT] Completed job_id={job_id}")


def _run_commit(
    job_id: str,
    session_id: Optional[str],
    final_metadata: Dict[str, Any],
) -> None:
    try:
        with _SEMAPHORE:
            run_commit_payload(
                job_id=job_id,
                session_id=session_id,
                final_metadata=final_metadata,
            )
    except Exception as e:
        mark_job_error(job_id, f"Commit failed: {e}")
        print(f"[RAG-COMMIT] Failed job_id={job_id}: {e}")
    finally:
        with _LOCK:
            _ACTIVE_JOBS.pop(job_id, None)


def get_active_commit_jobs() -> Dict[str, str]:
    with _LOCK:
        return {
            job_id: ("alive" if thread.is_alive() else "done")
            for job_id, thread in _ACTIVE_JOBS.items()
        }
