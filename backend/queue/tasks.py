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

    run_commit_payload_safe(
        job_id=str(job_id),
        session_id=(session_id or "").strip() or None,
        final_metadata=dict(final_metadata or {}),
    )
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

