# backend/api/chat.py

import os
import json
import uuid  # ✅ REQUIRED: Added for safety net
from typing import List, Literal, Generator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.state.abort_signals import (
    is_aborted,
    reset_abort_signal,
    signal_abort,
)

# ================================
# VECTOR / EMBEDDINGS
# ================================

from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings

# ================================
# LLM
# ================================

from backend.llm.generate import generate_answer_stream
from backend.llm.intent_rules import detect_rule_intent
from backend.llm.intent_classifier import classify_intent
from backend.llm.text_normalizer import normalize_text
from backend.llm.query_rewriter import rewrite_question

# ================================
# RAG
# ================================

from backend.rag.keyword_search import keyword_search
from backend.rag.confidence import compute_confidence

# ================================
# MEMORY
# ================================

from backend.memory.redis_memory import (
    add_used_chunk_ids,
    save_rag_debug,
)

from backend.memory.pg_memory import (
    append_chat_message,
    get_recent_user_messages,
    save_topic_hint,
    get_last_topic_hint,
)

from backend.memory.topic_hints import extract_topic_hint

# ================================
# JOB STATE
# ================================

from backend.state.job_state import (
    get_job_state,
    get_active_document,
)

# ================================
# UI EVENTS
# ================================

from backend.contracts.ui_events import (
    system_message_event,
    request_metadata_event,
    answer_confidence_event,
)

# ================================
# CONFIG
# ================================

DB_CONNECTION = os.getenv(
    "DB_CONNECTION",
    "postgresql+psycopg2://postgres:1@localhost:5432/rag_db",
)

COLLECTION_NAME = "rag_documents"

RAG_MAX_K = 8
SQL_BASE_SCORE = 0.35
UI_EVENT_PREFIX = "__UI_EVENT__"

router = APIRouter(prefix="/chat", tags=["Chat"])


# ================================
# REQUEST MODELS
# ================================

class ChatRequest(BaseModel):
    session_id: str
    question: str
    mode: Literal["lite", "base", "net"] = "lite"


# ================================
# SHARED VECTOR STORE
# ================================

embedding_model = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

vector_store = PGVector.from_existing_index(
    embedding=embedding_model,
    collection_name=COLLECTION_NAME,
    connection=DB_CONNECTION,
)


# ================================
# HELPERS
# ================================

def emit_event(event: dict) -> str:
    return UI_EVENT_PREFIX + json.dumps(event) + "\n"


def safe_stream_response(
    token_stream: Generator[str, None, None],
    session_id: str,
    original_question: str,
    confidence_payload: Optional[dict],
) -> Generator[str, None, None]:

    collected: List[str] = []

    try:
        for token in token_stream:
            if is_aborted(session_id):
                break
            if not token:
                continue
            collected.append(token)
            yield token

    except Exception:
        signal_abort(session_id)
        partial = "".join(collected).strip()
        final = f"{partial}\n\n*[Stopped]*" if partial else "⚠️ *Stopped*"
        try:
            append_chat_message(session_id, "user", original_question)
            append_chat_message(session_id, "assistant", final)
        except Exception:
            pass
        return

    final_answer = "".join(collected).strip()
    try:
        append_chat_message(session_id, "user", original_question)
        append_chat_message(session_id, "assistant", final_answer)
    except Exception:
        pass

    # 🔴 DISABLED: Commented out to prevent raw JSON from appearing in chat
    # if confidence_payload:
    #     yield emit_event(
    #         answer_confidence_event(
    #             confidence_payload["confidence"],
    #             confidence_payload["level"],
    #         )
    #     )


# ================================
# CHAT ENDPOINT
# ================================

@router.post("/")
def chat(req: ChatRequest):

    if not req.session_id or not req.question:
        raise HTTPException(400, "session_id and question required")

    session_id = req.session_id.strip()
    reset_abort_signal(session_id)

    original_question = normalize_text(req.question)

    # =====================================================
    # FAST MODE (NO RAG)
    # =====================================================

    rule_intent = detect_rule_intent(original_question)
    if rule_intent in ("greeting", "confirmation", "conversation"):
        return StreamingResponse(
            safe_stream_response(
                generate_answer_stream(
                    question=original_question,
                    model="lite",
                    intent=rule_intent,
                    max_tokens=32,
                    session_id=session_id,
                ),
                session_id,
                original_question,
                None,
            ),
            media_type="text/plain",
        )

    # =====================================================
    # JOB STATE (AUTO-RECOVERY)
    # =====================================================

    job_state = get_job_state(session_id)

    if not job_state:
        active_doc = get_active_document(session_id)
        if active_doc:
            job_state = type("RecoveredJob", (), {
                "status": "READY",
                "metadata": active_doc
            })()

    if job_state and job_state.status == "WAIT_FOR_METADATA":
        return StreamingResponse(
            [emit_event(request_metadata_event(job_state.missing_fields))],
            media_type="text/plain",
        )

    if job_state and job_state.status == "ERROR":
        return StreamingResponse(
            [emit_event(system_message_event("❌ Document processing failed"))],
            media_type="text/plain",
        )

    if job_state and job_state.status != "READY":
        return StreamingResponse(
            [emit_event(system_message_event("⏳ Processing documents…"))],
            media_type="text/plain",
        )

    # =====================================================
    # NORMAL CHAT (NO DOCUMENT)
    # =====================================================

    if not job_state:
        # No document context -> Just chat
        return StreamingResponse(
            safe_stream_response(
                generate_answer_stream(
                    question=original_question,
                    model=req.mode,
                    session_id=session_id,
                ),
                session_id,
                original_question,
                None,
            ),
            media_type="text/plain",
        )

    # =====================================================
    # RAG MODE (DOCUMENT-AWARE)
    # =====================================================

    company_document_id = job_state.metadata.get("company_document_id")
    revision_number = job_state.metadata.get("revision_number")

    # --- DEBUG LOGS ---
    print(f"\n🔍 [CHAT DEBUG] Search Request:")
    print(f"   - Question: {original_question}")
    print(f"   - Target Doc ID: {company_document_id}")
    print(f"   - Target Rev: {revision_number} (Type: {type(revision_number)})")

    if not company_document_id or revision_number is None:
        raise HTTPException(
            500,
            "Missing company_document_id or revision_number in job metadata",
        )

    history = get_recent_user_messages(session_id)
    rewritten = rewrite_question(original_question, history)
    intent = classify_intent(rewritten)

    # ✅ FILTER
    metadata_filter = {
        "company_document_id": company_document_id,
        "revision_number": str(revision_number), 
    }
    
    # 1. Vector Search
    vector_docs = vector_store.similarity_search(
        rewritten,
        k=RAG_MAX_K,
        filter=metadata_filter,
    )
    print(f"   - Vector Docs Found: {len(vector_docs)}")

    # 2. Keyword Search
    keyword_docs = keyword_search(
        question=rewritten,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        limit=RAG_MAX_K,
    )
    print(f"   - Keyword Docs Found: {len(keyword_docs)}")

    merged = {}
    for d in vector_docs + keyword_docs:
        # ✅ FIX: Flattened metadata access
        chunk_id = d.metadata.get("chunk_id")
        
        # ✅ FAIL-SAFE: If chunk_id is missing, assume it's valid and gen a temp ID
        # This prevents the system from throwing away valid data
        if not chunk_id:
            chunk_id = str(uuid.uuid4())
            
        merged.setdefault(chunk_id, d)

    rag_chunks = [
        {
            "id": cid,
            "content": d.page_content,
            "section": d.metadata.get("section"),
            "chunk_type": d.metadata.get("chunk_type"),
        }
        for cid, d in merged.items()
    ]

    add_used_chunk_ids(session_id, [c["id"] for c in rag_chunks])

    confidence_payload = compute_confidence(
        rag_chunks=rag_chunks,
        similarity_scores=[SQL_BASE_SCORE] * len(rag_chunks),
    )

    save_rag_debug(
        session_id,
        {
            "question": rewritten,
            "company_document_id": company_document_id,
            "revision_number": str(revision_number),
            "chunks": [c["id"] for c in rag_chunks],
            "confidence": confidence_payload,
        },
    )

    return StreamingResponse(
        safe_stream_response(
            generate_answer_stream(
                question=rewritten,
                model=req.mode,
                context_chunks=rag_chunks,
                intent=intent,
                session_id=session_id,
            ),
            session_id,
            original_question,
            confidence_payload,
        ),
        media_type="text/plain",
    )