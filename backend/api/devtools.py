# backend/api/devtools.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

# Import internal logic modules
from backend.llm.intent_classifier import classify_intent
from backend.llm.query_rewriter import rewrite_question
from backend.llm.text_normalizer import normalize_text
from backend.rag.keyword_search import extract_keywords

#  NEW: Import retrieval logic for testing
from backend.rag.retrieve import retrieve_rag_context
from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings
import os

from backend.memory.pg_memory import get_chat_messages
from backend.memory.redis_memory import get_active_topic, get_used_chunk_ids
from backend.state.job_state import _JOB_STORE

router = APIRouter(prefix="/devtools", tags=["Developer Tools"])

#-- Setup Vector Store for Retrieval Testing ---

DB_CONNECTION = os.getenv(
    "DB_CONNECTION",
    "postgresql+psycopg2://postgres:1@localhost:5432/rag_db",
)

COLLECTION_NAME = "rag_documents"

_embedding_model = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

vector_store = PGVector.from_existing_index(
    embedding=_embedding_model,
    collection_name=COLLECTION_NAME,
    connection=DB_CONNECTION,
)




# --- Models ---
class TextPayload(BaseModel):
    text: str
    history: List[str] = []

class IntentResult(BaseModel):
    normalized: str
    intent: str

class RetrievalDebugReq(BaseModel):
    question: str
    company_document_id: str
    revision_number: str = "1"

# --- Endpoints ---

@router.post("/intent", response_model=IntentResult)
def debug_intent(payload: TextPayload):
    """Test how the backend classifies a specific string."""
    norm = normalize_text(payload.text)
    intent = classify_intent(norm)
    return {"normalized": norm, "intent": intent}

@router.post("/rewrite")
def debug_rewrite(payload: TextPayload):
    """Test how a question is rewritten given a mock history."""
    rewritten = rewrite_question(payload.text, payload.history)
    return {"original": payload.text, "rewritten": rewritten}

@router.post("/keywords")
def debug_keywords(payload: TextPayload):
    """See what keywords are extracted for SQL search."""
    keywords = extract_keywords(payload.text)
    return {"keywords": keywords}

@router.get("/jobs")
def debug_jobs():
    """Inspect in-memory job states (SAFE SUMMARY)."""
    return {
        "active_jobs": len(_JOB_STORE),
        "statuses": {
            job_id: job.status
            for job_id, job in _JOB_STORE.items()
        }
    }


@router.post("/retrieve")
def debug_retrieval(req: RetrievalDebugReq):
    """Test the full RAG pipeline (Vector + Keyword + Rerank)"""

    if not req.company_document_id:
        raise HTTPException(400, "company_document_id required")

    if not req.revision_number:
        raise HTTPException(400, "revision_number required")

    chunks = retrieve_rag_context(
        question=req.question,
        vector_store=vector_store,
        company_document_id=req.company_document_id,
        revision_number=str(req.revision_number),
        force_detailed=True
    )

    return {
        "count": len(chunks),
        "chunk_ids": [c["id"] for c in chunks],
        "preview": chunks[:3],  # 🔥 DO NOT dump everything
    }

@router.get("/session-state/{session_id}")
def inspect_session(session_id: str):
    """View SAFE memory state for a session"""

    pg_history = get_chat_messages(session_id, limit=10)
    redis_topic = get_active_topic(session_id)
    redis_chunks = get_used_chunk_ids(session_id)

    return {
        "session_id": session_id,
        "postgres_message_count": len(pg_history),
        "recent_user_messages": [
            m["content"] for m in pg_history if m["role"] == "user"
        ][-3:],
        "active_topic": redis_topic,
        "used_chunk_ids_count": len(redis_chunks),
    }
