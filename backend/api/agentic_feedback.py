# backend/api/agentic_feedback.py

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.memory.pg_memory import save_user_feedback, get_user_feedback_summary

router = APIRouter(prefix="/agentic_feedback", tags=["Agentic Feedback"])


# ================================
# REQUEST MODEL
# ================================

class AgenticFeedbackRequest(BaseModel):
    session_id: str = Field(..., description="Session ID")
    message_id: str = Field(..., description="Message ID")
    feedback_type: str = Field(..., description="Feedback type: thumbs_up or thumbs_down")
    feedback_value: int = Field(..., description="Feedback value: 1 for thumbs_up, 0 for thumbs_down")


# ================================
# FEEDBACK ENDPOINT
# ================================

@router.post("/")
def submit_agentic_feedback(req: AgenticFeedbackRequest):
    """
    Submit agentic feedback for learning.
    This is used to improve the system based on user feedback.
    """
    if not req.session_id or not req.message_id:
        raise HTTPException(400, "Session ID and Message ID required")

    if req.feedback_type not in ["thumbs_up", "thumbs_down"]:
        raise HTTPException(400, "Invalid feedback type")

    try:
        # Use session_id as user_id proxy for learning
        save_user_feedback(
            user_id=req.session_id,
            session_id=req.session_id,
            message_id=int(req.message_id) if req.message_id.isdigit() else 0,
            feedback_type=req.feedback_type,
            feedback_value=req.feedback_value,
        )
    except Exception as e:
        # 🔒 Feedback must never break the system
        print(f"[AGENTIC FEEDBACK] Error saving feedback: {e}")

    return {
        "status": "ok",
        "message": "Feedback recorded",
    }


@router.get("/summary/{user_id}")
def get_feedback_summary(user_id: str):
    """
    Get feedback summary for a user.
    This is used to understand user preferences and improve responses.
    """
    try:
        summary = get_user_feedback_summary(user_id)
        return {
            "status": "ok",
            "summary": summary,
        }
    except Exception as e:
        print(f"[AGENTIC FEEDBACK] Error getting feedback summary: {e}")
        return {
            "status": "error",
            "message": str(e),
            "summary": {},
        }
