from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.pml_chat.client import PMLClientError, stream_pml_completion
from backend.pml_chat.example_memory import (
    add_pml_example,
    delete_pml_example,
    get_example_store_stats,
    list_pml_examples,
    get_relevant_examples,
    learn_examples_from_history,
    learn_examples_from_text,
)
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


class PMLLearnRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=50000)
    note: Optional[str] = Field(default=None, max_length=500)


@router.get("/status")
def pml_chat_status():
    settings = get_pml_settings()
    return {
        "ok": settings.configured,
        "configured": settings.configured,
        "base_url": settings.base_url,
        "model": settings.model,
    }


@router.get("/learn/status")
def pml_learning_status():
    return {
        "ok": True,
        **get_example_store_stats(),
    }


@router.post("/learn")
def pml_learn(req: PMLLearnRequest):
    code = req.code.strip()
    if not code:
        raise HTTPException(400, "code is required")

    example = add_pml_example(
        code=code,
        note=(req.note or "").strip(),
        source="manual",
    )
    return {
        "ok": True,
        "example": example,
        **get_example_store_stats(),
    }


@router.get("/learn/templates")
def pml_list_templates(limit: int = 100):
    return {
        "ok": True,
        "templates": list_pml_examples(limit=limit),
        **get_example_store_stats(),
    }


@router.delete("/learn/templates/{template_id}")
def pml_delete_template(template_id: str):
    deleted = delete_pml_example(template_id)
    if not deleted:
        raise HTTPException(404, "Template not found")
    return {
        "ok": True,
        "deleted_id": template_id,
        **get_example_store_stats(),
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

    # Learn from user-provided code snippets automatically.
    try:
        session_source = f"session:{req.session_id}"
        learn_examples_from_history(history, source=session_source)
        learn_examples_from_text(question, source=session_source)
    except Exception:
        pass

    try:
        examples = get_relevant_examples(question, limit=3)
    except Exception:
        examples = []

    messages = build_pml_messages(question=question, history=history, examples=examples)

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
