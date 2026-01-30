# backend/api/feedback.py

from typing import Optional, List, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.learning.retrieval_feedback import save_retrieval_feedback

router = APIRouter(prefix="/feedback", tags=["Feedback"])


# ================================
# REQUEST MODEL
# ================================

class FeedbackRequest(BaseModel):
    session_id: Optional[str] = None
    job_id: Optional[str] = None

    company_document_id: str
    revision_number: str

    question: str
    answer: str

    feedback_label: Literal[
        "correct",
        "partial",
        "incorrect",
        "hallucination",
        "missing_context"
    ]

    feedback_score: Optional[int] = Field(
        default=None,
        ge=1,
        le=5,
        description="Optional numeric score (1–5)",
    )

    comment: Optional[str] = None
    chunk_ids: Optional[List[str]] = None


# ================================
# FEEDBACK ENDPOINT
# ================================

@router.post("/")
def submit_feedback(req: FeedbackRequest):

    if not req.company_document_id or not req.revision_number:
        raise HTTPException(400, "Invalid document metadata")

    if not req.question or not req.answer:
        raise HTTPException(400, "Question and answer required")

    try:
        save_retrieval_feedback(
            session_id=req.session_id,
            job_id=req.job_id,
            company_document_id=req.company_document_id,
            revision_number=str(req.revision_number),
            question=req.question,
            answer=req.answer,
            feedback_label=req.feedback_label,
            feedback_score=req.feedback_score,
            comment=req.comment,
            chunk_ids=req.chunk_ids,
        )
    except Exception:
        # 🔒 Feedback must never break the system
        pass

    return {
        "status": "ok",
        "message": "Feedback recorded",
    }
