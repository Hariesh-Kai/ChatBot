from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.pml_chat.client import PMLClientError, stream_pml_completion
from backend.pml_chat.prompts import build_pml_messages
from backend.pml_chat.settings import get_pml_settings


router = APIRouter(prefix="/pml-chat", tags=["PML Chat"])


class PMLHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=6000)


class PMLChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    question: str = Field(..., min_length=1, max_length=8000)
    history: List[PMLHistoryMessage] = Field(default_factory=list)
    max_tokens: Optional[int] = Field(default=None, ge=64, le=4096)


@router.get("/status")
def pml_chat_status():
    settings = get_pml_settings()
    return {
        "ok": settings.configured,
        "configured": settings.configured,
        "base_url": settings.base_url,
        "model": settings.model,
    }


@router.post("/")
def pml_chat(req: PMLChatRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "question is required")

    settings = get_pml_settings()
    if not settings.configured:
        raise HTTPException(
            status_code=503,
            detail="PML LLM is not configured (PML_LLM_BASE_URL and PML_LLM_MODEL required).",
        )

    history = [item.model_dump() for item in req.history]
    messages = build_pml_messages(question=question, history=history)

    def _stream():
        yielded = False
        try:
            for token in stream_pml_completion(messages=messages, max_tokens=req.max_tokens):
                yielded = True
                yield token
        except PMLClientError as e:
            yield f"[PML error] {e}"
            return
        except Exception:
            yield "[PML error] Failed to generate response."
            return

        if not yielded:
            yield "No response generated."

    return StreamingResponse(_stream(), media_type="text/plain")

