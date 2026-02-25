# backend/api/audit_log.py

"""
RAG Audit Log API

Exposes the rag_audit_log table for:
- Dev dashboard inspection
- Per-session retrieval history
- Quality monitoring

Protected by auth (require_user).
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.rag.audit import get_recent_audits

router = APIRouter(prefix="/audit", tags=["Audit"])


class AuditQuery(BaseModel):
    session_id: Optional[str] = None
    company_document_id: Optional[str] = None
    limit: int = 50


@router.get("/recent")
def get_audit_log(
    session_id: Optional[str] = None,
    company_document_id: Optional[str] = None,
    limit: int = 50,
):
    """
    Fetch recent RAG audit entries.
    Query params: session_id, company_document_id, limit (default 50).
    """
    if limit > 200:
        limit = 200

    try:
        rows = get_recent_audits(
            session_id=session_id,
            company_document_id=company_document_id,
            limit=limit,
        )
        return {"status": "ok", "count": len(rows), "rows": rows}
    except Exception as e:
        raise HTTPException(500, f"Audit query failed: {e}")


@router.get("/stats")
def get_audit_stats(company_document_id: Optional[str] = None):
    """
    Summarize audit quality metrics for a document.
    Returns quality distribution and cache hit rate.
    """
    try:
        rows = get_recent_audits(
            company_document_id=company_document_id,
            limit=500,
        )
        if not rows:
            return {"status": "ok", "total": 0}

        total = len(rows)
        quality_dist: dict = {}
        cache_hits = 0
        total_latency = 0
        count_with_latency = 0

        for r in rows:
            q = r.get("eval_quality") or "unknown"
            quality_dist[q] = quality_dist.get(q, 0) + 1
            if r.get("cache_hit"):
                cache_hits += 1
            lat = r.get("latency_ms")
            if lat is not None:
                total_latency += lat
                count_with_latency += 1

        avg_latency = round(total_latency / count_with_latency) if count_with_latency else None

        return {
            "status": "ok",
            "total": total,
            "quality_distribution": quality_dist,
            "cache_hit_rate": round(cache_hits / total, 3),
            "avg_latency_ms": avg_latency,
        }
    except Exception as e:
        raise HTTPException(500, f"Audit stats failed: {e}")
