# backend/api/chat.py

import os
import json
import uuid  # ✅ REQUIRED: Added for safety net
from typing import List, Literal, Generator, Optional, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from langchain_core.documents import Document

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
from backend.llm.prompts import build_title_prompt 
from backend.llm.loader import get_llm

# ================================
# RAG
# ================================

from backend.rag.keyword_search import keyword_search
from backend.rag.confidence import compute_confidence
from backend.rag.rerank import rerank_documents  # ✅ NEW: Reranker

# ================================
# MEMORY
# ================================

from backend.memory.redis_memory import (
    add_used_chunk_ids,
    save_rag_debug,
    get_used_chunk_ids, # ✅ NEW: Retrieve IDs for follow-ups
)

from backend.memory.pg_memory import (
    append_chat_message,
    get_recent_user_messages,
    save_topic_hint,
    get_last_topic_hint,
    get_chunks_by_ids, # ✅ NEW: Hydrate text from IDs
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
# Fetch more candidates for reranking
RAG_CANDIDATE_K = 25 
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
# SHARED VECTOR STORE
# ================================

# ✅ UPGRADE: Match the ingest model (BGE-M3)
embedding_model = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True}
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


def safe_stream_with_sources(generator, sources):
    """
    Wrapper to stream the answer first, then append the SOURCES event.
    """
    yield from generator
    
    if sources:
        # Emit custom event for Source Viewer
        payload = {
            "type": "SOURCES",
            "data": sources
        }
        # Format consistent with your stream parser (likely expects prefix or newlines)
        # Using emit_event helper for consistency
        yield emit_event(payload)


# ================================
# PARENT-CHILD LOOKUP
# ================================

def resolve_parent_chunks(
    child_docs: List[Document], 
    vector_store: PGVector, 
    collection_name: str
) -> List[Document]:
    """
    For every Child chunk (row), find its Parent chunk (full table).
    If a chunk is already a Parent or Text, keep it.
    Deduplicates parents.
    """
    
    # 1. Identify IDs to fetch
    parent_ids_to_fetch = set()
    final_docs_map = {} # Map ID -> Document

    for doc in child_docs:
        # Is this a child?
        if doc.metadata.get("type") == "child" and doc.metadata.get("parent_id"):
            parent_ids_to_fetch.add(doc.metadata["parent_id"])
        else:
            # It's already a parent or standard text, keep it
            doc_id = doc.metadata.get("chunk_id") or str(uuid.uuid4())
            final_docs_map[doc_id] = doc

    if not parent_ids_to_fetch:
        return list(final_docs_map.values())

    # 2. Fetch Parents from DB
    try:
        for pid in parent_ids_to_fetch:
            # We search for the specific parent document
            results = vector_store.similarity_search(
                "ignored", # query ignored with exact filter usually
                k=1,
                filter={"doc_id": pid, "type": "parent"} 
            )
            if results:
                parent = results[0]
                final_docs_map[pid] = parent
                
    except Exception as e:
        print(f"⚠️ Parent lookup failed: {e}")
        # Fallback: Just use the children if parents fail
        return child_docs

    return list(final_docs_map.values())


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

    if not company_document_id or revision_number is None:
        raise HTTPException(
            500,
            "Missing company_document_id or revision_number in job metadata",
        )

    history = get_recent_user_messages(session_id)
    rewritten = rewrite_question(original_question, history)
    intent = classify_intent(rewritten)

    # ✅ LOGIC UPDATE: Force "Detailed" verbosity if user asks for details
    force_detail = any(x in original_question.lower() for x in ["detail", "explain more", "elaborate"])

    # ✅ FILTER
    metadata_filter = {
        "company_document_id": company_document_id,
        "revision_number": str(revision_number), 
    }
    
    # ---------------------------------------------------------
    # 🚀 STEP 1: RETRIEVE CANDIDATES (CHILDREN)
    # ---------------------------------------------------------
    
    # 1. Vector Search (High Recall)
    # ✅ IMPROVEMENT: Fetch MORE candidates if requesting details
    search_k = RAG_CANDIDATE_K + 10 if force_detail else RAG_CANDIDATE_K

    vector_docs = vector_store.similarity_search(
        rewritten,
        k=search_k, 
        filter=metadata_filter,
    )
    print(f"   - Vector Candidates: {len(vector_docs)}")

    # 2. Keyword Search (Precision)
    keyword_docs = keyword_search(
        question=rewritten,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        limit=10, 
    )
    print(f"   - Keyword Candidates: {len(keyword_docs)}")

    # 3. Deduplicate
    unique_map = {}
    for d in vector_docs + keyword_docs:
        unique_map[d.page_content] = d
    candidates = list(unique_map.values())

    # ---------------------------------------------------------
    # 🚀 STEP 1.5: MERGE PREVIOUS CONTEXT (FOR FOLLOW-UPS)
    # ---------------------------------------------------------
    
    previous_context_docs = []
    
    if intent == "follow_up" or force_detail:
        print(f"🔄 [RAG] Follow-up detected. Restoring previous context...")
        prev_ids = get_used_chunk_ids(session_id)
        if prev_ids:
            # Fetch full content from Postgres
            restored_chunks = get_chunks_by_ids(list(prev_ids))
            
            # Convert back to Document objects
            for rc in restored_chunks:
                doc = Document(
                    page_content=rc["content"],
                    metadata={
                        "chunk_id": rc["id"],
                        "section": rc["section"],
                        "type": rc["chunk_type"],
                        **rc["metadata"] 
                    }
                )
                previous_context_docs.append(doc)
            
            print(f"   - Restored {len(previous_context_docs)} previous chunks.")

    # Combine: Previous Context + New Candidates
    combined_candidates = previous_context_docs + candidates
    
    # Deduplicate again (using chunk_id if available, else content hash)
    final_unique_map = {}
    for d in combined_candidates:
        cid = d.metadata.get("chunk_id") or d.page_content
        final_unique_map[cid] = d
    
    final_candidates = list(final_unique_map.values())

    # ---------------------------------------------------------
    # 🚀 STEP 2: RERANKING
    # ---------------------------------------------------------
    
    if final_candidates:
        # Increase top_k if user wants details (give LLM more to work with)
        final_k = RAG_MAX_K + 2 if force_detail else RAG_MAX_K
        print(f"   - Reranking {len(final_candidates)} candidates...")
        reranked_docs = rerank_documents(rewritten, final_candidates, top_k=final_k)
    else:
        reranked_docs = []

    # ---------------------------------------------------------
    # 🚀 STEP 3: PARENT RESOLUTION (CONTEXT EXPANSION)
    # ---------------------------------------------------------
    
    final_docs = resolve_parent_chunks(reranked_docs, vector_store, COLLECTION_NAME)
    
    # ---------------------------------------------------------
    # 🚀 STEP 4: FORMAT FOR LLM & FRONTEND (SOURCES)
    # ---------------------------------------------------------

    rag_chunks = []
    rag_sources = [] # ✅ List for Source Viewer

    for d in final_docs:
        cid = d.metadata.get("chunk_id") or d.metadata.get("doc_id") or str(uuid.uuid4())
        
        # Prepare context for LLM
        rag_chunks.append({
            "id": cid,
            "content": d.page_content,
            "section": d.metadata.get("section"),
            "chunk_type": d.metadata.get("type"),
            "score": d.metadata.get("rerank_score", 0.0)
        })

        # ✅ FIX: Robust Metadata Extraction for Source Viewer
        meta = d.metadata
        filename = meta.get("source_file")
        if not filename:
             # Fallback to nested cmetadata if flattened extraction failed
             filename = meta.get("cmetadata", {}).get("source_file", "Unknown")

        page_num = meta.get("page_number", 1)
        bbox = meta.get("bbox", "")

        # ✅ Extract Source Metadata for Frontend
        rag_sources.append({
            "id": str(uuid.uuid4()),
            "fileName": filename, # Matches SourceViewerModal
            "page": page_num,     # ✅ CRITICAL FIX: "page" (Frontend expects this), NOT "page_number"
            "bbox": bbox,
            "company_doc_id": company_document_id,
            "revision": int(revision_number) 
        })

    add_used_chunk_ids(session_id, [c["id"] for c in rag_chunks])

    confidence_payload = compute_confidence(
        rag_chunks=rag_chunks,
        similarity_scores=[c.get("score") or SQL_BASE_SCORE for c in rag_chunks],
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

    # ✅ STREAM WITH SOURCES
    # Wrap the generator to append the SOURCES event at the end
    return StreamingResponse(
        safe_stream_with_sources(
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
            rag_sources # Pass the sources list here
        ),
        media_type="text/plain",
    )


# ================================
# AUTO-TITLE ENDPOINT
# ================================

@router.post("/title", response_model=Dict[str, str])
def generate_title(req: TitleRequest):
    """
    Generates a short title for a chat session based on the first message.
    """
    # 1. Build Prompt
    prompt = build_title_prompt(req.question)

    # 2. Get Lite LLM (Fastest)
    try:
        llm_info = get_llm("lite_llama_8b") # or your lite model ID
    except Exception:
        return {"title": "New Chat"}

    # 3. Generate (Non-streaming)
    output = ""
    try:
        if llm_info["type"] == "gguf":
            # 🔥 CRITICAL FIX: CONSUME THE GENERATOR STREAM
            stream = llm_info["llm"](prompt, max_tokens=15, stop=["\n"])
            chunks = []
            
            # Iterate through the stream to get the actual text
            for chunk in stream:
                if isinstance(chunk, dict):
                    text = chunk.get("choices", [{}])[0].get("text", "")
                    chunks.append(text)
                elif isinstance(chunk, str):
                    chunks.append(chunk)
            
            output = "".join(chunks)

        else:
            # HuggingFace logic
            model = llm_info["model"]
            tokenizer = llm_info["tokenizer"]
            inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
            tokens = model.generate(
                **inputs, 
                max_new_tokens=15, 
                pad_token_id=tokenizer.eos_token_id,
                do_sample=False
            )
            output = tokenizer.decode(tokens[0], skip_special_tokens=True)
            if prompt in output:
                output = output.replace(prompt, "")
            
    except Exception as e:
        print(f"Title generation failed: {e}")
        return {"title": "New Chat"}

    # 4. Clean up
    clean_title = output.strip().replace('"', '').replace("Title:", "")
    if len(clean_title) > 50:
        clean_title = clean_title[:47] + "..."
    
    # Fallback if empty
    if not clean_title:
        clean_title = "New Chat"

    return {"title": clean_title}