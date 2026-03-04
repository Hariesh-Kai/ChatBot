# backend/api/chat.py

import os
import json
import uuid
import time
import re
from typing import List, Literal, Generator, Dict, Optional, Any

from fastapi import APIRouter, HTTPException, Depends
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
from backend.llm.hf_cache_utils import resolve_local_snapshot
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
from backend.llm.pii import detect_pii
from backend.rag.evaluator import evaluate_answer
from backend.rag.audit import log_rag_turn
from backend.rag.cache import get_cached_chunks, set_cached_chunks
from backend.llm.few_shot import get_few_shot_examples, format_few_shot_block
from backend.learning.adaptive_retrieval import get_adaptive_config
from backend.rag.retrieve import augment_query_with_context

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
    get_summarized_history,
)

# ================================
# JOB STATE
# ================================

from backend.state.job_state import (
    get_job_state,
    get_active_document,
    clear_job_for_session,
)

from backend.state.dev_settings import get_dev_settings
from backend.state.rag_overrides import is_rag_disabled
from backend.auth.deps import require_user
from backend.auth.user_store import User

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
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
HF_CACHE_DIR = os.path.join(PROJECT_ROOT, "models", "hf_cache")

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
    model_name=resolve_local_snapshot(HF_CACHE_DIR, "BAAI/bge-m3") or "BAAI/bge-m3",
    model_kwargs={"device": "cpu", "local_files_only": True},
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

_SMALLTALK_MAP = {
    "who are you": "I'm KavinBase, your AI document assistant.",
    "what are you": "I'm KavinBase, your AI document assistant.",
    "what do you do": "I help answer questions about your documents.",
    "what can you do": "I can answer questions about your documents and help summarize them.",
    "how are you": "I'm doing well. How can I help?",
    "how are you?": "I'm doing well. How can I help?",
    "how's it going": "All good here. How can I help?",
}

_ANSWER_SOURCE_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "there", "their", "then",
    "when", "where", "while", "what", "which", "into", "about", "after", "before",
    "over", "under", "between", "your", "you", "they", "them", "its", "our", "are",
    "was", "were", "has", "have", "had", "will", "shall", "can", "could", "would",
    "should", "may", "might", "must", "not", "only", "also", "than", "such", "each",
    "using", "used", "use", "per", "via", "all", "any", "out", "off", "new", "old",
    "one", "two", "three", "how", "why", "who",
}


# ============================================================
# QUERY ROUTING (Phase 3)
# Adjusts retrieval strategy based on detected intent
# ============================================================

def _route_query(intent: str, force_detailed: bool) -> dict:
    """
    Return retrieval config based on query intent.
    - factual   → tight BM25, small K
    - summary   → wide retrieval, large K
    - compare   → force detailed, large K
    - default   → standard settings
    """
    if force_detailed:
        return {"force_detailed": True, "limit": 16}

    if intent in ("summary", "summarize"):
        return {"force_detailed": True, "limit": 16}

    if intent in ("compare", "comparison"):
        return {"force_detailed": True, "limit": 14}

    if intent in ("factual", "lookup", "definition"):
        return {"force_detailed": False, "limit": 8}

    return {"force_detailed": False, "limit": 10}


def _static_smalltalk_reply(text: str, intent: Optional[str] = None) -> Optional[str]:
    q = (text or "").strip().lower()
    if not q:
        return None

    if q in _SMALLTALK_MAP:
        return _SMALLTALK_MAP[q]

    if q.startswith("who are you"):
        return _SMALLTALK_MAP["who are you"]
    if q.startswith("what are you"):
        return _SMALLTALK_MAP["what are you"]
    if q.startswith("how are you"):
        return _SMALLTALK_MAP["how are you"]

    if intent == "confirmation":
        if q in {"thanks", "thank you", "thx"}:
            return "You're welcome."
        return "Got it."

    if intent in ("greeting", "conversation"):
        return "Hi! How can I help?"

    return None


def _extract_answer_terms(answer: str) -> List[str]:
    text = (answer or "").strip().lower()
    if not text:
        return []

    raw_terms = re.findall(r"[a-z0-9][a-z0-9._\\-/]{2,}", text)
    terms: List[str] = []
    seen: set[str] = set()

    for token in raw_terms:
        clean = token.strip(".,:;()[]{}\"'`")
        if not clean or clean in seen:
            continue
        if clean in _ANSWER_SOURCE_STOPWORDS:
            continue
        seen.add(clean)
        terms.append(clean)

    numeric_terms = re.findall(r"\b\d[\d,\.]*\b", text)
    for token in numeric_terms:
        clean = token.replace(",", "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            terms.append(clean)

    return terms


def _select_answer_supporting_chunks(
    answer: str,
    rag_chunks: List[Dict[str, Any]],
    *,
    max_chunks: int = 8,
) -> List[Dict[str, Any]]:
    if not rag_chunks:
        return []

    terms = _extract_answer_terms(answer)
    if not terms:
        return rag_chunks[: min(3, len(rag_chunks))]

    scored: List[tuple[int, int, float, int, Dict[str, Any]]] = []
    for idx, chunk in enumerate(rag_chunks):
        content = str(chunk.get("content") or "").lower()
        if not content:
            continue

        hit_count = 0
        weighted_hits = 0
        for term in terms:
            if term in content:
                hit_count += 1
                weighted_hits += 2 if any(ch.isdigit() for ch in term) else 1

        if hit_count == 0:
            continue

        retrieval_score = float(chunk.get("score") or 0.0)
        scored.append((weighted_hits, hit_count, retrieval_score, -idx, chunk))

    if not scored:
        return rag_chunks[: min(3, len(rag_chunks))]

    scored.sort(reverse=True)

    selected: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for _, _, _, _, chunk in scored:
        chunk_id = str(chunk.get("id") or "").strip()
        if chunk_id and chunk_id in seen_ids:
            continue
        if chunk_id:
            seen_ids.add(chunk_id)
        selected.append(chunk)
        if len(selected) >= max_chunks:
            break

    return selected


def _build_sources_from_chunks(
    chunks: List[Dict[str, Any]],
    *,
    company_document_id: str,
    revision_number: int,
) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen_pages: set[tuple[str, int, str, int]] = set()

    for chunk in chunks:
        meta = chunk.get("metadata", {}) or {}
        file_name = str(meta.get("source_file") or "Unknown")
        try:
            page_number = int(meta.get("page_number") or 1)
        except Exception:
            page_number = 1

        page_key = (file_name, page_number, company_document_id, int(revision_number))
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)

        sources.append(
            {
                "id": chunk.get("id"),
                "fileName": file_name,
                "page": page_number,
                "bbox": meta.get("bbox", ""),
                "chunk_type": chunk.get("chunk_type"),
                "section": chunk.get("section"),
                "company_document_id": company_document_id,
                "revision_number": int(revision_number),
            }
        )

    return sources


def safe_stream_response(
    token_stream: Generator[str, None, None],
    session_id: str,
    original_question: str,
) -> Generator[str, None, str]:

    collected: List[str] = []
    saw_error_event = False

    try:
        for chunk in token_stream:
            if is_aborted(session_id):
                yield emit_event(error_event("Generation aborted"))
                return ""

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
                        if event.get("type") == "ERROR":
                            saw_error_event = True
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
        return ""

    final_answer = "".join(collected).strip()

    if not final_answer:
        if saw_error_event:
            return ""
        yield emit_event(system_message_event("Model produced no output"))
        return ""

    try:
        append_chat_message(session_id, "user", original_question)
        append_chat_message(session_id, "assistant", final_answer)
    except Exception:
        pass

    return final_answer




# ================================
# CHAT ENDPOINT
# ================================

@router.post("/")
def chat(req: ChatRequest, user: User = Depends(require_user)):

    if not req.session_id or not req.question:
        raise HTTPException(400, "session_id and question required")

    session_id = req.session_id.strip()
    reset_abort_signal(session_id)

    start_time = time.time()
    original_question = normalize_text(req.question)
    job_state = get_job_state(session_id)

    settings = get_dev_settings()
    emit_model_stages = bool(settings.get("emit_model_stage_events", True))
    emit_sources = bool(settings.get("emit_sources", True))
    emit_answer_confidence = bool(settings.get("emit_answer_confidence", True))
    force_detailed_retrieval = bool(settings.get("force_detailed_retrieval", False))
    disable_retrieval_policy = bool(settings.get("disable_retrieval_policy", False))
    disable_rag_globally = bool(settings.get("disable_rag_globally", False))

    # =====================================================
    # 🔥 METADATA GATE (OPTION A)
    # =====================================================

    if job_state and job_state.status == "WAIT_FOR_METADATA":
        metadata = job_state.metadata or {}

        REQUIRED_KEYS = ["document_type", "revision_code"]

        missing = [
            key for key in REQUIRED_KEYS
            if not metadata.get(key)
        ]

        # 🔥 ONLY REQUEST METADATA IF SOMETHING IS ACTUALLY MISSING
        if missing:
            fields = [{
                "key": key,
                "label": key.replace("_", " ").title(),
                "placeholder": f"Enter {key.replace('_', ' ')}",
                "reason": "Required to continue",
                "value": metadata.get(key),
            } for key in missing]

            def metadata_stream():
                yield emit_event(request_metadata_event(fields, job_state.job_id))

            return StreamingResponse(metadata_stream(), media_type="text/plain")
    processing_in_background = False
    if job_state and job_state.status == "PROCESSING":
        # Keep chat responsive while ingestion is still running.
        processing_in_background = True
        job_state = None


    # =====================================================
    # FAST MODE (NO RAG)
    # =====================================================

    rule_intent = detect_rule_intent(original_question)
    print(
        f"[INTENT][RULE] session={session_id} "
        f"text='{original_question}' -> intent='{rule_intent}'"
    )

    static_reply = _static_smalltalk_reply(original_question, rule_intent)
    if static_reply:
        def static_stream():
            yield static_reply

        try:
            append_chat_message(session_id, "user", original_question)
            append_chat_message(session_id, "assistant", static_reply)
        except Exception:
            pass

        return StreamingResponse(static_stream(), media_type="text/plain")

    if False and rule_intent in ("greeting", "confirmation", "conversation") and not job_state:
        model_id = resolve_model_id(req.mode)
        try:
            _ = get_llm(model_id)
        except Exception:
            pass
        
        
        def fast_stream():
            yield emit_event(system_message_event(" Responding…"))
            if emit_model_stages:
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
            if processing_in_background:
                yield emit_event(
                    system_message_event(
                        "Document indexing is running in background. "
                        "Answering without document context for now."
                    )
                )
            else:
                yield emit_event(system_message_event("Thinking..."))
            model_id = resolve_model_id(req.mode)

            if emit_model_stages:
                yield emit_event(model_stage_event(
                    stage="generation",
                    message="Generation (No RAG)",
                    model=model_id,
                ))

            if emit_model_stages:
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

    # =====================================================
    # PII CHECK (Phase 3) — log only, non-blocking
    # =====================================================
    try:
        pii_findings = detect_pii(original_question)
        if pii_findings:
            labels = [f["label"] for f in pii_findings]
            print(f"[PII] Detected in question: {labels}")
    except Exception:
        pass

    history = get_recent_user_messages(session_id)
    rewritten = rewrite_question(original_question, history)
    intent = classify_intent(rewritten)
    print(
        f"[INTENT][CLASSIFIER] session={session_id} "
        f"original='{original_question}' "
        f"rewritten='{rewritten}' "
        f"intent='{intent}'"
)
    if intent == "greeting":
        static_reply = _static_smalltalk_reply(original_question, "greeting")
        if static_reply:
            def static_stream():
                yield static_reply

            try:
                append_chat_message(session_id, "user", original_question)
                append_chat_message(session_id, "assistant", static_reply)
            except Exception:
                pass

            return StreamingResponse(static_stream(), media_type="text/plain")
    rag_disabled = disable_rag_globally or is_rag_disabled(session_id, user.username)

    previous_context_chunks = []
    if not rag_disabled and intent == "follow_up":
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

    # =====================================================
    # QUERY ROUTING (Phase 3)
    # =====================================================
    route_config = _route_query(intent, force_detailed_retrieval)

    # =====================================================
    # ADAPTIVE RETRIEVAL CONFIG (Phase 4)
    # Overrides route_config K if doc has history
    # =====================================================
    try:
        adaptive = get_adaptive_config(company_document_id, str(revision_number))
        # Adaptive can widen route_config but not override force_detailed
        if adaptive["k"] > route_config.get("limit", 8):
            route_config["limit"] = adaptive["k"]
        if adaptive["force_detailed"] and not route_config["force_detailed"]:
            route_config["force_detailed"] = True
    except Exception:
        pass

    # =====================================================
    # SEMANTIC CACHE CHECK (Phase 2)
    # =====================================================
    cache_hit = False
    new_rag_chunks = []

    # Conversation-aware query augmentation (Phase 4) - only for cache key + retrieval
    conv_history = get_recent_user_messages(session_id)
    augmented_query = augment_query_with_context(rewritten, conv_history)

    if not rag_disabled:
        cached = get_cached_chunks(company_document_id, str(revision_number), augmented_query)
        if cached is not None:
            new_rag_chunks = cached
            cache_hit = True
        else:
            new_rag_chunks = retrieve_rag_context(
                question=augmented_query,
                vector_store=vector_store,
                company_document_id=company_document_id,
                revision_number=str(revision_number),
                force_detailed=route_config["force_detailed"],
            )
            set_cached_chunks(
                company_document_id, str(revision_number), augmented_query, new_rag_chunks
            )



    unique = {}
    rag_chunks = []
    for c in previous_context_chunks + new_rag_chunks:
        if c["id"] not in unique:
            unique[c["id"]] = True
            rag_chunks.append(c)

    if not disable_retrieval_policy and not rag_disabled:
        policy_result = apply_retrieval_policy(
            question=rewritten,
            rag_chunks=rag_chunks,
            company_document_id=company_document_id,
            revision_number=str(revision_number),
        )

        rag_chunks = policy_result.chunks

    did_retrieve = bool(rag_chunks) and not rag_disabled

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
        if emit_model_stages and not did_retrieve:
            yield emit_event(model_stage_event(
                stage="generation",
                message="Generation (No RAG)",
                model=model_id,
            ))
        if emit_model_stages and did_retrieve:
            yield emit_event(model_stage_event(
                stage="intent",
                message="Understanding your question…",
                model=model_id,
            ))

        # intent already computed, do NOT re-run

        if emit_model_stages and did_retrieve:
            if rag_disabled:
                yield emit_event(model_stage_event(
                    stage="retrieval",
                    message="Retrieval disabled for this session",
                ))
            else:
                yield emit_event(model_stage_event(
                    stage="retrieval",
                    message="Searching relevant documents…",
                ))

        # retrieval already happened — OK for now
        # (next step: move retrieval here)

        if emit_model_stages and did_retrieve:
            yield emit_event(model_stage_event(
                stage="reranking",
                message="Ranking the best passages…",
            ))
            yield emit_event(model_stage_event(
                stage="chunks",
                message="Selected top chunks",
            ))

        if emit_model_stages and did_retrieve:
            yield emit_event(model_stage_event(
                stage="generation",
                message="Generating answer…",
                model=model_id,
            ))
        
        # =====================================================
        # FEW-SHOT EXAMPLES (Phase 4) — prepended as context turns
        # =====================================================
        few_shot_hist = []
        try:
            fs_examples = get_few_shot_examples(
                question=rewritten,
                company_document_id=company_document_id,
                revision_number=str(revision_number),
            )
            if fs_examples:
                fs_block = format_few_shot_block(fs_examples)
                if fs_block:
                    few_shot_hist = [{"role": "system", "content": fs_block}]
        except Exception:
            few_shot_hist = []

        # Summarized history (Phase 2) + few-shot (Phase 4)
        try:
            full_history = get_summarized_history(session_id, limit=50)
        except Exception:
            full_history = get_recent_user_messages(session_id)
        combined_history = few_shot_hist + list(full_history)

        final_answer = yield from safe_stream_response(
            generate_answer_stream(
                question=rewritten,
                model_id=model_id,
                context_chunks=rag_chunks,
                intent=intent,
                chat_history=combined_history,
                session_id=session_id,
            ),
            session_id,
            original_question,
        )

        answer_supported_chunks = _select_answer_supporting_chunks(
            final_answer,
            rag_chunks,
        )
        if not answer_supported_chunks:
            answer_supported_chunks = rag_chunks

        try:
            resolved_revision_number = int(revision_number)
        except Exception:
            resolved_revision_number = 1

        rag_sources = _build_sources_from_chunks(
            answer_supported_chunks,
            company_document_id=company_document_id,
            revision_number=resolved_revision_number,
        )

        try:
            add_used_chunk_ids(
                session_id,
                [c["id"] for c in answer_supported_chunks if c.get("id")],
            )
        except Exception:
            pass

        if emit_answer_confidence:
            yield emit_event(answer_confidence_event(
                confidence=confidence_payload["confidence"],
                level=confidence_payload["level"],
            ))

        if not is_aborted(session_id):
            if emit_sources:
                yield emit_event({
                    "type": "SOURCES",
                    "data": rag_sources,
                })
            clear_job_for_session(session_id)

        # =====================================================
        # RAGAS EVALUATION + AUDIT LOG (Phase 3, non-blocking)
        # =====================================================
        try:
            # Collect the answer from memory (already stored by safe_stream_response)
            from backend.memory.pg_memory import get_chat_messages as _get_msgs
            recent = _get_msgs(session_id, limit=2)
            last_answer = ""
            for m in reversed(recent):
                if m.get("role") == "assistant":
                    last_answer = m.get("content", "")
                    break

            eval_scores = evaluate_answer(
                question=rewritten,
                answer=last_answer,
                rag_chunks=answer_supported_chunks or rag_chunks,
            )

            # Emit eval quality as a UI event (optional — only if high/low)
            if eval_scores.get("quality") == "low":
                yield emit_event({
                    "type": "EVAL",
                    "quality": eval_scores["quality"],
                    "overall": eval_scores["overall"],
                })

            # Write to audit log
            log_rag_turn(
                session_id=session_id,
                company_document_id=company_document_id,
                revision_number=str(revision_number),
                question=original_question,
                rewritten_question=rewritten,
                intent=intent,
                chunk_ids=[c["id"] for c in (answer_supported_chunks or rag_chunks)],
                grounding_score=None,
                eval_scores=eval_scores,
                answer_snippet=last_answer[:500],
                latency_ms=int((time.time() - start_time) * 1000),
                cache_hit=cache_hit,
                multi_query_used=False,
            )
        except Exception as _e:
            print(f"[STREAM] eval/audit failed (non-fatal): {_e}")



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
