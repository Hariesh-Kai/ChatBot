# backend/api/chat.py

import os
import json
import uuid
import time
from typing import List, Literal, Generator, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.state.abort_signals import (
    is_aborted,
    signal_abort,
    reset_abort_signal,
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
from backend.llm.prompts import build_title_prompt
from backend.llm.loader import get_llm
# ================================
# RAG
# ================================

from backend.rag.retrieve import retrieve_rag_context
from backend.rag.confidence import compute_confidence

# ================================
# LEARNING
# ================================

from backend.learning.retrieval_stats import record_retrieval_stats
from backend.learning.retrieval_policy import apply_retrieval_policy
from backend.llm.model_selector import resolve_model_id

# ================================
# MEMORY
# ================================

from backend.memory.redis_memory import (
    add_used_chunk_ids,
    save_rag_debug,
    get_used_chunk_ids,
)

from backend.memory.pg_memory import (
    append_chat_message,
    get_recent_user_messages,
    get_chunks_by_ids,
)

# ================================
# JOB STATE
# ================================

from backend.state.job_state import (
    get_job_state,
    get_active_document,
    clear_job_for_session,
)

# ================================
# UI EVENTS
# ================================

from backend.contracts.ui_events import (
    answer_confidence_event,
    system_message_event,
    request_metadata_event,
    model_stage_event,
    error_event,
)


# ================================
# CONFIG
# ================================

DB_CONNECTION = os.getenv(
    "DB_CONNECTION",
    "postgresql+psycopg2://postgres:1@localhost:5432/rag_db",
)

COLLECTION_NAME = "rag_documents"
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


class TitleRequest(BaseModel):
    question: str


# ================================
# VECTOR STORE
# ================================

embedding_model = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
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
) -> Generator[str, None, None]:

    collected: List[str] = []

    try:
        for chunk in token_stream:
            if is_aborted(session_id):
                yield emit_event(error_event("Generation aborted"))
                return

            if not chunk:
                continue
            
            # 🔒 SAFETY: If backend ever emits invalid TEXT UI event, normalize it
            if chunk.startswith(UI_EVENT_PREFIX):
                try:
                    event = json.loads(chunk[len(UI_EVENT_PREFIX):])
                    if event.get("type") == "TEXT":
                        content = event.get("content", "")
                        yield content
                        collected.append(content)
                        continue
                    else:
                        # ✅ VALID UI EVENT → forward as-is
                        yield chunk
                        continue
                except Exception:
                    continue

            # 🔥 chunk is already a UI event
            yield chunk
            if not chunk.startswith(UI_EVENT_PREFIX):
                collected.append(chunk)

    except Exception:
        signal_abort(session_id)
        yield emit_event(error_event("Generation failed"))
        return

    final_answer = "".join(collected).strip()

    if not final_answer:
        yield emit_event(system_message_event("Model produced no output"))
        return

    try:
        append_chat_message(session_id, "user", original_question)
        append_chat_message(session_id, "assistant", final_answer)
    except Exception:
        pass




# ================================
# CHAT ENDPOINT
# ================================

@router.post("/")
def chat(req: ChatRequest):

    if not req.session_id or not req.question:
        raise HTTPException(400, "session_id and question required")

    session_id = req.session_id.strip()
    reset_abort_signal(session_id)

    start_time = time.time()
    original_question = normalize_text(req.question)
    job_state = get_job_state(session_id)

    # =====================================================
    # 🔥 METADATA GATE (OPTION A)
    # =====================================================

    if job_state and job_state.status == "WAIT_FOR_METADATA":
        metadata = job_state.metadata or {}

        REQUIRED_KEYS = ["document_type", "revision_code"]

        fields = []
        for key in REQUIRED_KEYS:
            value = metadata.get(key)
            if not value:
                fields.append({
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "placeholder": f"Enter {key.replace('_', ' ')}",
                    "reason": "Required to continue",
                    "value": value,
                })

        def metadata_stream():
            yield emit_event(request_metadata_event(fields))

        return StreamingResponse(metadata_stream(), media_type="text/plain")


    if job_state and job_state.status == "PROCESSING":
        def processing_stream():
            yield emit_event(system_message_event("Document is being processed…"))
        return StreamingResponse(processing_stream(), media_type="text/plain")


    # =====================================================
    # FAST MODE (NO RAG)
    # =====================================================

    rule_intent = detect_rule_intent(original_question)
    print(
        f"[INTENT][RULE] session={session_id} "
        f"text='{original_question}' -> intent='{rule_intent}'"
    )

    if rule_intent in ("greeting", "confirmation", "conversation") and not job_state:
        model_id = resolve_model_id(req.mode)
        try:
            _ = get_llm(model_id)
        except Exception:
            pass
        
        
        def fast_stream():
            yield emit_event(system_message_event(" Responding…"))
            yield emit_event(model_stage_event(
                stage="generation",
                message="Responding…",
                model=model_id, 
            ))

            yield from safe_stream_response(
                generate_answer_stream(
                    question=original_question,
                    model_id=model_id
, 
                    intent=rule_intent,
                    max_tokens=128,
                    session_id=session_id,
                ),
                session_id,
                original_question,
            )

        return StreamingResponse(fast_stream(), media_type="text/plain")

    # =====================================================
    # JOB STATE
    # =====================================================
    if not job_state:
        active_doc = get_active_document(session_id)
        if active_doc:
            job_state = type("RecoveredJob", (), {
                "status": "READY",
                "metadata": active_doc,
                "missing_fields": [],
            })()


    if job_state and job_state.status == "ERROR":
        def single_event_stream(msg):
            yield emit_event(system_message_event(msg))
        return StreamingResponse(
            single_event_stream("Document processing failed"),
            media_type="text/plain",
)


    # =====================================================
    # NORMAL CHAT (NO DOCUMENT)
    # =====================================================
    if not job_state:
        def normal_stream():
            yield emit_event(system_message_event("Thinking…")) 
            model_id = resolve_model_id(req.mode)

            yield emit_event(model_stage_event(
                stage="generation",
                message="Generating response…",
               model=model_id,
            ))

            
            yield from safe_stream_response(
                generate_answer_stream(
                    question=original_question,
                     model_id=model_id,
                    session_id=session_id,
                ),
                session_id,
                original_question,
            )
            
        return StreamingResponse(normal_stream(), media_type="text/plain")

    
    
    # =====================================================
    # RAG MODE
    # NOTE:
    # Retrieval + reranking currently execute BEFORE streaming.
    # model_stage_event is used as a UX indicator only.
    # =====================================================

    if job_state.status != "READY":
        raise HTTPException(400, "Document not ready for querying")
    company_document_id = job_state.metadata.get("company_document_id")
    revision_number = job_state.metadata.get("revision_number")

    if not company_document_id or revision_number is None:
        raise HTTPException(500, "Invalid document metadata")

    history = get_recent_user_messages(session_id)
    rewritten = rewrite_question(original_question, history)
    intent = classify_intent(rewritten)
    print(
        f"[INTENT][CLASSIFIER] session={session_id} "
        f"original='{original_question}' "
        f"rewritten='{rewritten}' "
        f"intent='{intent}'"
)
    previous_context_chunks = []
    if intent == "follow_up":
        prev_ids = get_used_chunk_ids(session_id)
        if prev_ids:
            restored = get_chunks_by_ids(list(prev_ids))
            for rc in restored:
                previous_context_chunks.append({
                    "id": rc["id"],
                    "content": rc["content"],
                    "section": rc["section"],
                    "chunk_type": rc["chunk_type"],
                    "score": 1.0,
                    "metadata": rc["metadata"],
                })

    new_rag_chunks = retrieve_rag_context(
        question=rewritten,
        vector_store=vector_store,
        company_document_id=company_document_id,
        revision_number=str(revision_number),
    )



    unique = {}
    rag_chunks = []
    for c in previous_context_chunks + new_rag_chunks:
        if c["id"] not in unique:
            unique[c["id"]] = True
            rag_chunks.append(c)

    policy_result = apply_retrieval_policy(
        question=rewritten,
        rag_chunks=rag_chunks,
        company_document_id=company_document_id,
        revision_number=str(revision_number),
    )

    rag_chunks = policy_result.chunks

    rag_sources = []
    for c in rag_chunks:
        meta = c.get("metadata", {})
        rag_sources.append({
            "id": c["id"],
            "fileName": meta.get("source_file", "Unknown"),
            "page": meta.get("page_number", 1),
            "company_document_id": company_document_id,
            "revision_number": int(revision_number),
        })

    add_used_chunk_ids(session_id, [c["id"] for c in rag_chunks])

    confidence_payload = compute_confidence(
        rag_chunks=rag_chunks,
        similarity_scores=[c.get("score", SQL_BASE_SCORE) for c in rag_chunks],
    )

    save_rag_debug(session_id, {
        "question": rewritten,
        "company_document_id": company_document_id,
        "revision_number": str(revision_number),
        "chunks": [c["id"] for c in rag_chunks],
        "confidence": confidence_payload,
    })

    latency_ms = int((time.time() - start_time) * 1000)

    record_retrieval_stats(
        session_id=session_id,
        job_id=None,
        company_document_id=company_document_id,
        revision_number=str(revision_number),
        question=rewritten,
        rag_chunks=rag_chunks,
        confidence=confidence_payload.get("confidence"),
        confidence_level=confidence_payload.get("level"),
        latency_ms=latency_ms,
    )

    def stream():
        model_id = resolve_model_id(req.mode)
        yield emit_event(model_stage_event(
            stage="intent",
            message="Understanding your question…",
           model=model_id,
        ))

        # intent already computed, do NOT re-run

        yield emit_event(model_stage_event(
            stage="retrieval",
            message="Searching relevant documents…",
        ))

        # retrieval already happened — OK for now
        # (next step: move retrieval here)

        yield emit_event(model_stage_event(
            stage="reranking",
            message="Ranking the best passages…",
        ))

        yield emit_event(model_stage_event(
            stage="generation",
            message="Generating answer…",
           model=model_id,
        ))
        
        yield from safe_stream_response(
            generate_answer_stream(
                question=rewritten,
                model_id=model_id,
                context_chunks=rag_chunks,
                intent=intent,
                session_id=session_id,
            ),
            session_id,
            original_question,
        )

        yield emit_event(answer_confidence_event(
            confidence=confidence_payload["confidence"],
            level=confidence_payload["level"],
        ))

        if not is_aborted(session_id):
            yield emit_event({
                "type": "SOURCES",
                "data": rag_sources,
            })
            clear_job_for_session(session_id)



    return StreamingResponse(stream(), media_type="text/plain")


# ================================
# AUTO-TITLE ENDPOINT
# ================================

@router.post("/title", response_model=Dict[str, str])
def generate_title(req: TitleRequest):

    prompt = build_title_prompt(req.question)

    try:
       llm_info = get_llm(resolve_model_id("lite"))
    except Exception:
        return {"title": "New Chat"}

    output = ""
    try:
        if llm_info["type"] == "gguf":
            stream = llm_info["llm"](prompt, max_tokens=15, stop=["\n"])
            output = "".join(
                chunk.get("choices", [{}])[0].get("text", "")
                if isinstance(chunk, dict) else str(chunk)
                for chunk in stream
            )
        else:
            model = llm_info["model"]
            tokenizer = llm_info["tokenizer"]
            inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
            tokens = model.generate(
                **inputs,
                max_new_tokens=15,
                pad_token_id=tokenizer.eos_token_id,
                do_sample=False,
            )
            output = tokenizer.decode(tokens[0], skip_special_tokens=True)
            output = output.replace(prompt, "")
    except Exception:
        return {"title": "New Chat"}

    clean = output.strip().replace('"', "").replace("Title:", "")
    return {"title": clean[:50] or "New Chat"}
