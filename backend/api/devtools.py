# backend/api/devtools.py

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import re

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
from backend.state.dev_settings import get_dev_settings, update_dev_settings

# Model management (Dev Dashboard)
from huggingface_hub import snapshot_download
from backend.llm.loader import (
    GGUF_MODELS,
    HF_MODELS,
    HF_CACHE_DIR,
    get_llm,
    reload_model_config,
)
from backend.llm.model_registry import MODEL_REGISTRY, reload_model_registry
from backend.llm.model_config_store import (
    load_model_config,
    upsert_hf_model,
    upsert_gguf_model,
    patch_model_registry_overrides,
)

router = APIRouter(prefix="/devtools", tags=["Developer Tools"])

_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}$")


def _require_safe_model_id(model_id: str) -> str:
    model_id = (model_id or "").strip()
    if not _MODEL_ID_RE.match(model_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid model_id. Use 2-64 chars: letters/numbers and ._-",
        )
    return model_id

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


class InstallHFModelReq(BaseModel):
    model_id: str
    repo_id: str


class RegisterGGUFModelReq(BaseModel):
    model_id: str
    path: str


class TestModelReq(BaseModel):
    model_id: str
    prompt: str = "Say hello in one short sentence."

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


@router.get("/settings")
def read_settings():
    """Read in-memory developer settings (feature flags)."""
    return get_dev_settings()


@router.patch("/settings")
def patch_settings(patch: Dict[str, Any] = Body(...)):
    """Update in-memory developer settings (feature flags)."""
    try:
        return update_dev_settings(patch)
    except (KeyError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/models")
def list_models():
    """List available models + current mode registry (for the Developer Dashboard)."""
    return {
        "model_registry": MODEL_REGISTRY,
        "hf_models": dict(HF_MODELS),
        "gguf_models": dict(GGUF_MODELS),
        "model_config": load_model_config(),
    }


@router.post("/models/hf/install")
def install_hf_model(req: InstallHFModelReq):
    """
    Download a HuggingFace model to local cache and register it for use.

    NOTE: Requires internet access from the machine running the backend.
    """
    model_id = _require_safe_model_id(req.model_id)
    repo_id = (req.repo_id or "").strip()
    if not repo_id:
        raise HTTPException(status_code=400, detail="repo_id is required")

    try:
        snapshot_download(
            repo_id=repo_id,
            cache_dir=HF_CACHE_DIR,
            local_dir_use_symlinks=False,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {e}")

    try:
        upsert_hf_model(model_id=model_id, repo_id=repo_id)
        reload_model_config()
        reload_model_registry()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Register failed: {e}")

    return {
        "ok": True,
        "model_id": model_id,
        "repo_id": repo_id,
        "model_registry": MODEL_REGISTRY,
    }


@router.post("/models/gguf/register")
def register_gguf_model(req: RegisterGGUFModelReq):
    """
    Register a GGUF model path (does not download).
    """
    model_id = _require_safe_model_id(req.model_id)
    path = (req.path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")

    try:
        upsert_gguf_model(model_id=model_id, path=path)
        reload_model_config()
        reload_model_registry()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Register failed: {e}")

    return {
        "ok": True,
        "model_id": model_id,
        "path": path,
        "model_registry": MODEL_REGISTRY,
    }


@router.patch("/models/registry")
def patch_models_registry(patch: Dict[str, Any] = Body(...)):
    """
    Patch the per-mode model IDs (lite/base/net) used by the backend.
    """
    try:
        patch_model_registry_overrides(patch)
        reload_model_registry()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "ok": True,
        "model_registry": MODEL_REGISTRY,
    }


@router.post("/models/test")
def test_model(req: TestModelReq):
    """
    Try loading a model and generating a tiny sample output to confirm it works.
    """
    model_id = (req.model_id or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")

    try:
        llm_info = get_llm(model_id)
        prompt = (req.prompt or "").strip() or "Say hello."

        if llm_info["type"] == "gguf":
            stream = llm_info["llm"](prompt, max_tokens=24, stop=["\n"])
            text = "".join(
                chunk.get("choices", [{}])[0].get("text", "")
                if isinstance(chunk, dict)
                else str(chunk)
                for chunk in stream
            )
            return {"ok": True, "type": "gguf", "output": (text or "").strip()}

        if llm_info["type"] == "hf":
            model = llm_info["model"]
            tokenizer = llm_info["tokenizer"]
            inputs = tokenizer(prompt, return_tensors="pt")
            try:
                inputs = {k: v.to(model.device) for k, v in inputs.items()}
            except Exception:
                pass
            tokens = model.generate(
                **inputs,
                max_new_tokens=24,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
            out = tokenizer.decode(tokens[0], skip_special_tokens=True)
            out = out.replace(prompt, "").strip()
            return {"ok": True, "type": "hf", "output": out}

        return {"ok": False, "error": f"Unsupported model type: {llm_info.get('type')}"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/retrieve")
def debug_retrieval(req: RetrievalDebugReq):
    """Test the full RAG pipeline (Vector + Keyword + Rerank)"""

    if not req.company_document_id:
        raise HTTPException(400, "company_document_id required")

    if not req.revision_number:
        raise HTTPException(400, "revision_number required")

    settings = get_dev_settings()

    chunks = retrieve_rag_context(
        question=req.question,
        vector_store=vector_store,
        company_document_id=req.company_document_id,
        revision_number=str(req.revision_number),
        force_detailed=bool(settings.get("force_detailed_retrieval"))
    )

    return {
        "count": len(chunks),
        "chunk_ids": [c["id"] for c in chunks],
        "chunks": chunks[:3],
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
