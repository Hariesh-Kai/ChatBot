from __future__ import annotations

import os
from typing import Any, Dict, Optional

from backend.queue.celery_app import celery_app

if celery_app is None:  # pragma: no cover
    raise RuntimeError("Celery is not available. Install celery before starting workers.")


@celery_app.task(name="chatui.rag.commit")
def rag_commit_task(
    *,
    job_id: str,
    session_id: Optional[str] = None,
    final_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from backend.rag.commit_worker import run_commit_payload_safe
    from backend.state.job_state import get_job_state
    from backend.state.job_persistence import get_job_run

    clean_job_id = str(job_id)
    clean_session_id = (session_id or "").strip() or None
    payload = dict(final_metadata or {})
    
    # Check if job is already completed to prevent reprocessing after RabbitMQ connection loss
    try:
        job_state = get_job_state(clean_job_id)
        if job_state and job_state.status == "READY":
            print(f"[CELERY] Job already completed, skipping reprocessing | job_id={clean_job_id}")
            return {"ok": True, "job_id": job_id, "skipped": True, "reason": "already_completed"}
        
        # Also check persisted job state
        persisted_job = get_job_run(clean_job_id)
        if persisted_job and persisted_job.get("status") == "READY":
            print(f"[CELERY] Job already completed (persisted), skipping reprocessing | job_id={clean_job_id}")
            return {"ok": True, "job_id": job_id, "skipped": True, "reason": "already_completed_persisted"}
    except Exception as e:
        print(f"[CELERY] Error checking job completion status, proceeding with processing | job_id={clean_job_id} error={e}")
    
    print(
        "[CELERY] rag_commit_task started | "
        f"job_id={clean_job_id} session_id={clean_session_id or '-'}"
    )
    run_commit_payload_safe(
        job_id=clean_job_id,
        session_id=clean_session_id,
        final_metadata=payload,
    )
    print(f"[CELERY] rag_commit_task finished | job_id={clean_job_id}")
    return {"ok": True, "job_id": job_id}


@celery_app.task(name="chatui.minio.outbox.tick")
def minio_outbox_tick(limit: Optional[int] = None) -> Dict[str, Any]:
    from backend.storage.minio_outbox import process_due_uploads_once

    batch = max(
        1,
        int(limit or int(os.getenv("MINIO_OUTBOX_BATCH_SIZE", "2"))),
    )
    processed = process_due_uploads_once(limit=batch)
    return {"ok": True, "processed": int(processed)}

