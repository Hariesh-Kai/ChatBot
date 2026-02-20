# backend/api/devtools.py

from fastapi import APIRouter, Body, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
import re
from pathlib import Path
import shutil
from urllib.parse import urlparse, urlunparse

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
from backend.state.rag_overrides import (
    disable_rag_for_session,
    enable_rag_for_session,
    disable_rag_for_user,
    enable_rag_for_user,
    list_overrides,
)
from backend.auth.deps import require_admin
from backend.auth.user_store import (
    User,
    create_user,
    list_users,
    set_user_disabled,
    reset_user_password,
    set_user_role,
    delete_user,
    set_user_resources,
)
from backend.memory import pg_memory

# Model management (Dev Dashboard)
from huggingface_hub import snapshot_download, list_repo_files, hf_hub_download
import requests
import psycopg2
from psycopg2 import sql as pg_sql
from psycopg2.extras import RealDictCursor
import backend.llm.loader as llm_loader
from backend.llm.loader import (
    GGUF_MODELS,
    HF_MODELS,
    HF_CACHE_DIR,
    GGUF_DIR,
    get_llm,
    reload_model_config,
)
from backend.llm.model_registry import MODEL_REGISTRY, reload_model_registry
from backend.llm.hf_cache_utils import resolve_local_snapshot
from backend.llm.model_config_store import (
    load_model_config,
    upsert_hf_model,
    upsert_gguf_model,
    patch_model_registry_overrides,
    delete_model,
    ensure_model_paths,
)
from backend.llm.net_models import get_active_net_provider, resolve_active_net_model
from backend.secrets.net_keys import has_net_api_key
from backend.storage.minio_client import get_minio_client
from backend.memory.redis_memory import r as redis_client
from backend.runtime_status import get_runtime_status

router = APIRouter(prefix="/devtools", tags=["Developer Tools"])

_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}$")
_PG_DB_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,62}$")
_MINIO_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

logger = logging.getLogger("kavin.devtools")


def _raise_http(detail: str, status_code: int = 500, exc: Optional[Exception] = None) -> None:
    if exc:
        logger.exception(detail)
    else:
        logger.error(detail)
    raise HTTPException(status_code=status_code, detail=detail)


ensure_model_paths()


def _require_safe_model_id(model_id: str) -> str:
    model_id = (model_id or "").strip()
    if not _MODEL_ID_RE.match(model_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid model_id. Use 2-64 chars: letters/numbers and ._-",
        )
    return model_id


def _derive_model_id(repo_id: str) -> str:
    # Derive a safe model_id from repo_id (e.g., "org/name" -> "org_name")
    base = (repo_id or "").strip()
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "_", base)
    base = base.strip("._-")
    if len(base) < 2:
        base = "model"
    return base[:64]


def _hf_repo_cache_dir(repo_id: str) -> Path:
    safe = (repo_id or "").replace("/", "--")
    return Path(HF_CACHE_DIR) / f"models--{safe}"


def _model_status(model_id: str) -> Dict[str, Any]:
    model_id = (model_id or "").strip()
    status: Dict[str, Any] = {
        "model_id": model_id or None,
        "type": None,
        "ready": False,
        "loaded": False,
        "path": None,
        "repo_id": None,
        "error": None,
    }

    if not model_id:
        status["error"] = "No model_id configured"
        return status

    if model_id in GGUF_MODELS:
        path = GGUF_MODELS.get(model_id)
        status["type"] = "gguf"
        status["path"] = path
        status["ready"] = bool(path and Path(path).exists())
        status["loaded"] = model_id in getattr(llm_loader, "_llama_cache", {})
        if not status["ready"]:
            status["error"] = "GGUF file missing"
        return status

    if model_id in HF_MODELS:
        repo_id = HF_MODELS.get(model_id)
        status["type"] = "hf"
        status["repo_id"] = repo_id
        cache_dir = _hf_repo_cache_dir(repo_id or "")
        status["ready"] = cache_dir.exists()
        status["loaded"] = model_id in getattr(llm_loader, "_hf_model_cache", {})
        if not status["ready"]:
            status["error"] = "HF files not cached"
        return status

    status["error"] = "Unknown model_id"
    return status


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _safe_delete_path(path: Path, root: Path) -> Dict[str, Any]:
    result = {"path": str(path), "deleted": False, "error": None}
    if not path:
        result["error"] = "missing path"
        return result
    if not path.exists():
        result["error"] = "not found"
        return result
    if not _is_within(path, root):
        result["error"] = "path outside allowed directory"
        return result

    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        result["deleted"] = True
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def _resolve_gguf_path(path_str: str) -> Path:
    p = Path(path_str or "")
    if not p.is_absolute():
        p = Path(GGUF_DIR) / p
    return p

#-- Setup Vector Store for Retrieval Testing ---

DB_CONNECTION = os.getenv(
    "DB_CONNECTION",
    "postgresql+psycopg2://postgres:1@localhost:5432/rag_db",
)
CHAT_DB_URL = os.getenv(
    "CHAT_DB_URL",
    "postgresql://postgres:1@localhost:5432/chat_memory_db",
)

COLLECTION_NAME = "rag_documents"

_embedding_model = HuggingFaceEmbeddings(
    model_name=resolve_local_snapshot(HF_CACHE_DIR, "BAAI/bge-m3") or "BAAI/bge-m3",
    model_kwargs={"device": "cpu", "local_files_only": True},
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


class DownloadGGUFModelReq(BaseModel):
    model_id: str
    url: str
    filename: Optional[str] = None  # defaults to basename(url)
    max_mb: int = 25_000  # safety cap (25GB by default)


class DownloadHFModelReq(BaseModel):
    repo_id: str
    model_id: Optional[str] = None
    gguf_filename: Optional[str] = None


class ResetRequest(BaseModel):
    confirm: str
    # Safety knobs (only for admins with env gate enabled)
    wipe_redis_all: bool = False
    minio_bucket: Optional[str] = None


class CreateUserReq(BaseModel):
    email: str
    username: str
    password: str
    role: Optional[str] = "user"
    pg_database: Optional[str] = None
    minio_bucket: Optional[str] = None


class DisableUserReq(BaseModel):
    identifier: str
    disabled: bool = True


class ResetUserPasswordReq(BaseModel):
    identifier: str
    new_password: str


class SetUserRoleReq(BaseModel):
    identifier: str
    role: str


class DeleteUserReq(BaseModel):
    identifier: str


class RagOverrideReq(BaseModel):
    session_id: Optional[str] = None
    username: Optional[str] = None


_RESET_CONFIRM_PHRASE = "DELETE_EVERYTHING"


def _require_destructive_enabled() -> None:
    if os.getenv("KAVIN_ENABLE_DESTRUCTIVE_DEVTOOLS", "0").strip().lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=403,
            detail="Destructive devtools disabled. Set KAVIN_ENABLE_DESTRUCTIVE_DEVTOOLS=1 to enable.",
        )


def _require_reset_confirm(confirm: str) -> None:
    if (confirm or "").strip() != _RESET_CONFIRM_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation required. Set confirm='{_RESET_CONFIRM_PHRASE}'.",
        )


def _psycopg2_dsn(url: str) -> str:
    return (url or "").replace("postgresql+psycopg2://", "postgresql://")


def _truncate_tables(dsn: str, tables: List[str]) -> Dict[str, Any]:
    conn = None
    truncated: List[str] = []
    missing: List[str] = []
    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            for table in tables:
                try:
                    cur.execute(
                        pg_sql.SQL("TRUNCATE TABLE {} CASCADE;").format(
                            pg_sql.Identifier(table)
                        )
                    )
                    truncated.append(table)
                except psycopg2.errors.UndefinedTable:
                    conn.rollback()
                    missing.append(table)
                except Exception:
                    conn.rollback()
                    raise
        conn.commit()
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return {"truncated": truncated, "missing": missing}


# -------------------- Database Introspection --------------------
_DB_SOURCES = {
    "rag_db": {
        "label": "RAG (pgvector)",
        "type": "postgres",
        "dsn_env": "DB_CONNECTION",
        "fallback": DB_CONNECTION,
    },
    "chat_db": {
        "label": "Chat Memory",
        "type": "postgres",
        "dsn_env": "CHAT_DB_URL",
        "fallback": os.getenv("CHAT_DB_URL", "postgresql://postgres:1@localhost:5432/chat_memory_db"),
    },
    "redis": {
        "label": "Redis",
        "type": "redis",
    },
    "minio": {
        "label": "MinIO",
        "type": "minio",
    },
}


def _get_db_dsn(db_id: str) -> str:
    meta = _DB_SOURCES.get(db_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Unknown database id")
    dsn = os.getenv(meta.get("dsn_env", ""), "") or meta.get("fallback", "")
    if not dsn:
        raise HTTPException(status_code=500, detail="Database DSN not configured")
    return _psycopg2_dsn(dsn)


def _ensure_postgres_database(dsn: str) -> Dict[str, Any]:
    dsn = _psycopg2_dsn(dsn)
    parsed = urlparse(dsn)
    dbname = (parsed.path or "").lstrip("/")
    if not dbname:
        raise ValueError("Database name missing in DSN")

    try:
        conn = psycopg2.connect(dsn)
        conn.close()
        return {"database": dbname, "created": False}
    except psycopg2.OperationalError as e:
        if "does not exist" not in str(e):
            raise ValueError(f"Database connection failed: {e}")

    admin_dsn = urlunparse(parsed._replace(path="/postgres"))
    conn = None
    try:
        conn = psycopg2.connect(admin_dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(pg_sql.SQL("CREATE DATABASE {}").format(pg_sql.Identifier(dbname)))
        return {"database": dbname, "created": True}
    except Exception as e:
        raise ValueError(f"Failed to create database '{dbname}': {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _with_db_name(dsn: str, db_name: str) -> str:
    parsed = urlparse(_psycopg2_dsn(dsn))
    return urlunparse(parsed._replace(path=f"/{db_name}"))


def _normalize_pg_db_name(username: str, requested: Optional[str]) -> str:
    raw = (requested or "").strip()
    if raw:
        if not _PG_DB_RE.match(raw):
            raise ValueError("Postgres database name must be 3-63 chars and use letters, numbers, underscore")
        return raw

    base = re.sub(r"[^a-zA-Z0-9_]+", "_", (username or "user")).strip("_")
    if not base:
        base = "user"
    db_name = f"kavin_{base.lower()}"[:63]
    if len(db_name) < 3:
        db_name = "kavin_user"
    if not _PG_DB_RE.match(db_name):
        raise ValueError("Generated Postgres database name is invalid")
    return db_name


def _normalize_bucket_name(username: str, requested: Optional[str]) -> str:
    raw = (requested or "").strip().lower()
    if raw:
        if not _MINIO_BUCKET_RE.match(raw):
            raise ValueError("MinIO bucket name must be 3-63 chars, lowercase, digits, dots or hyphens")
        return raw

    base = re.sub(r"[^a-z0-9-]+", "-", (username or "user").lower()).strip("-")
    if not base:
        base = "user"
    bucket = f"kavin-{base}"[:63].strip("-")
    if len(bucket) < 3:
        bucket = "kavin-user"
    if not _MINIO_BUCKET_RE.match(bucket):
        raise ValueError("Generated MinIO bucket name is invalid")
    return bucket


def _ensure_minio_bucket(bucket_name: str) -> Dict[str, Any]:
    client = get_minio_client()
    if not client:
        raise ValueError("MinIO not configured or unavailable")

    try:
        exists = client.bucket_exists(bucket_name)
    except Exception as e:
        raise ValueError(f"MinIO bucket check failed: {e}")

    if not exists:
        try:
            client.make_bucket(bucket_name)
        except Exception as e:
            raise ValueError(f"Failed to create MinIO bucket '{bucket_name}': {e}")

    return {"bucket": bucket_name, "created": not exists}


def _provision_user_resources(username: str, pg_database: Optional[str], minio_bucket: Optional[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    pg_name = _normalize_pg_db_name(username, pg_database)
    bucket_name = _normalize_bucket_name(username, minio_bucket)

    base_dsn = os.getenv("CHAT_DB_URL", "postgresql://postgres:1@localhost:5432/postgres")
    result["postgres"] = _ensure_postgres_database(_with_db_name(base_dsn, pg_name))
    result["minio"] = _ensure_minio_bucket(bucket_name)
    result["redis"] = {"namespace": f"user:{username.lower()}:"}

    try:
        pg_memory._init_db()
    except Exception as e:
        raise ValueError(f"Chat DB init failed: {e}")

    return result


def _list_tables(dsn: str) -> List[str]:
    conn = None
    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
                """
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _list_columns(dsn: str, table: str) -> List[str]:
    conn = None
    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _fetch_rows(dsn: str, table: str, columns: List[str], limit: int, offset: int) -> Dict[str, Any]:
    conn = None
    rows: List[Dict[str, Any]] = []
    total = 0

    # Avoid huge vectors in response
    selected = [c for c in columns if c.lower() not in ("embedding",)]
    if not selected:
        selected = columns

    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                pg_sql.SQL("SELECT COUNT(*) as count FROM {}").format(pg_sql.Identifier(table))
            )
            total = int(cur.fetchone()["count"])

            cur.execute(
                pg_sql.SQL("SELECT {fields} FROM {table} LIMIT %s OFFSET %s").format(
                    fields=pg_sql.SQL(", ").join(pg_sql.Identifier(c) for c in selected),
                    table=pg_sql.Identifier(table),
                ),
                (limit, offset),
            )
            rows = list(cur.fetchall() or [])
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "columns": selected,
        "excluded_columns": [c for c in columns if c not in selected],
        "rows": rows,
        "total": total,
    }

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


# -------------------- User Management (Admin-only) --------------------

@router.get("/users")
def admin_list_users(_admin: User = Depends(require_admin)):
    return {"users": list_users()}


@router.post("/users")
def admin_create_user(req: CreateUserReq, _admin: User = Depends(require_admin)):
    try:
        user = create_user(
            email=req.email,
            username=req.username,
            password=req.password,
            role=req.role or "user",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        provisioned = _provision_user_resources(user.username, req.pg_database, req.minio_bucket)
        set_user_resources(user.username, provisioned)
    except Exception as e:
        try:
            delete_user(user.username)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "ok": True,
        "user": {"username": user.username, "email": user.email, "role": user.role, "disabled": user.disabled},
        "provisioned": provisioned,
    }


@router.patch("/users/disable")
def admin_disable_user(req: DisableUserReq, _admin: User = Depends(require_admin)):
    try:
        user = set_user_disabled(req.identifier, bool(req.disabled))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": {"username": user.username, "email": user.email, "role": user.role, "disabled": user.disabled}}


@router.patch("/users/password")
def admin_reset_password(req: ResetUserPasswordReq, _admin: User = Depends(require_admin)):
    try:
        user = reset_user_password(req.identifier, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": {"username": user.username, "email": user.email, "role": user.role, "disabled": user.disabled}}


@router.patch("/users/role")
def admin_set_user_role(req: SetUserRoleReq, _admin: User = Depends(require_admin)):
    try:
        user = set_user_role(req.identifier, req.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": {"username": user.username, "email": user.email, "role": user.role, "disabled": user.disabled}}


@router.delete("/users")
def admin_delete_user(req: DeleteUserReq = Body(...), _admin: User = Depends(require_admin)):
    try:
        user = delete_user(req.identifier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": {"username": user.username, "email": user.email, "role": user.role, "disabled": user.disabled}}


@router.post("/users/delete")
def admin_delete_user_post(req: DeleteUserReq, _admin: User = Depends(require_admin)):
    try:
        user = delete_user(req.identifier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": {"username": user.username, "email": user.email, "role": user.role, "disabled": user.disabled}}


# -------------------- RAG Overrides (Admin-only) --------------------

@router.get("/rag/overrides")
def rag_overrides(_admin: User = Depends(require_admin)):
    return list_overrides()


@router.post("/rag/disable")
def rag_disable(req: RagOverrideReq, _admin: User = Depends(require_admin)):
    if not req.session_id and not req.username:
        raise HTTPException(status_code=400, detail="session_id or username required")
    if req.session_id:
        disable_rag_for_session(req.session_id)
    if req.username:
        disable_rag_for_user(req.username)
    return list_overrides()


@router.post("/rag/enable")
def rag_enable(req: RagOverrideReq, _admin: User = Depends(require_admin)):
    if not req.session_id and not req.username:
        raise HTTPException(status_code=400, detail="session_id or username required")
    if req.session_id:
        enable_rag_for_session(req.session_id)
    if req.username:
        enable_rag_for_user(req.username)
    return list_overrides()


@router.get("/models")
def list_models(_admin: User = Depends(require_admin)):
    """List available models + current mode registry (for the Developer Dashboard)."""
    return {
        "model_registry": MODEL_REGISTRY,
        "hf_models": dict(HF_MODELS),
        "gguf_models": dict(GGUF_MODELS),
        "model_config": load_model_config(),
    }


@router.get("/models/active")
def active_models(_admin: User = Depends(require_admin)):
    """Report active model per mode + readiness."""
    modes: List[Dict[str, Any]] = []

    for mode in ("base", "lite"):
        model_id = MODEL_REGISTRY.get(mode, {}).get("default")
        status = _model_status(model_id)
        status["mode"] = mode
        modes.append(status)

    net_provider = None
    net_model = None
    net_ready = False
    net_error = None
    try:
        net_provider = get_active_net_provider()
        net_model = resolve_active_net_model()
        net_ready = has_net_api_key(net_provider)
        if not net_ready:
            net_error = "API key missing"
    except Exception as e:
        net_error = str(e)

    modes.append({
        "mode": "net",
        "provider": net_provider,
        "model": net_model,
        "ready": net_ready,
        "loaded": net_ready,
        "error": net_error,
    })

    runtime = get_runtime_status()
    return {
        "modes": modes,
        "system": runtime.get("gpu", {}),
        "rabbitmq": runtime.get("rabbitmq", {}),
        "workers": runtime.get("workers", {}),
        "software": runtime.get("software", {}),
    }


@router.get("/runtime")
def runtime_status(_admin: User = Depends(require_admin)):
    """Runtime visibility for GPU, RabbitMQ broker and worker queues."""
    return get_runtime_status()


@router.post("/models/hf/install")
def install_hf_model(req: InstallHFModelReq, _admin: User = Depends(require_admin)):
    """
    Download a HuggingFace model to local cache and register it for use.

    NOTE: Requires internet access from the machine running the backend.
    """
    ensure_model_paths()
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
        _raise_http(f"Download failed: {e}", 500, e)

    try:
        upsert_hf_model(model_id=model_id, repo_id=repo_id)
        reload_model_config()
        reload_model_registry()
    except Exception as e:
        _raise_http(f"Register failed: {e}", 500, e)

    return {
        "ok": True,
        "model_id": model_id,
        "repo_id": repo_id,
        "model_registry": MODEL_REGISTRY,
    }


@router.post("/models/download")
def download_model(req: DownloadHFModelReq, _admin: User = Depends(require_admin)):
    """
    Download a model from Hugging Face by repo_id.

    Auto-detects:
    - GGUF repos (downloads .gguf -> registers as Lite)
    - HF transformer repos (snapshot -> registers as Base)
    """
    ensure_model_paths()
    repo_id = (req.repo_id or "").strip()
    if not repo_id:
        raise HTTPException(status_code=400, detail="repo_id is required")

    model_id = req.model_id or _derive_model_id(repo_id)
    model_id = _require_safe_model_id(model_id)

    cfg = load_model_config()
    if model_id in cfg.get("hf_models", {}) or model_id in cfg.get("gguf_models", {}):
        raise HTTPException(status_code=409, detail="model_id already registered")

    for mid, rid in cfg.get("hf_models", {}).items():
        if rid == repo_id:
            return {
                "ok": True,
                "already_registered": True,
                "model_type": "hf",
                "mode": "base",
                "model_id": mid,
                "repo_id": repo_id,
            }

    try:
        files = list_repo_files(repo_id=repo_id)
    except Exception as e:
        _raise_http(f"Unable to access repo: {e}", 400, e)

    gguf_files = [f for f in files if f.lower().endswith(".gguf")]
    if gguf_files:
        filename = (req.gguf_filename or "").strip()
        if not filename:
            if len(gguf_files) == 1:
                filename = gguf_files[0]
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Multiple GGUF files found. Specify gguf_filename.",
                )
        if filename not in gguf_files:
            raise HTTPException(status_code=400, detail="gguf_filename not found in repo")

        try:
            path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=GGUF_DIR,
                local_dir_use_symlinks=False,
            )
        except Exception as e:
            _raise_http(f"GGUF download failed: {e}", 500, e)

        try:
            upsert_gguf_model(model_id=model_id, path=path)
            reload_model_config()
            reload_model_registry()
        except Exception as e:
            _raise_http(f"Register failed: {e}", 500, e)

        return {
            "ok": True,
            "model_type": "gguf",
            "mode": "lite",
            "model_id": model_id,
            "repo_id": repo_id,
            "filename": filename,
            "path": path,
            "model_registry": MODEL_REGISTRY,
        }

    # Default: HF model (non-GGUF)
    try:
        snapshot_download(
            repo_id=repo_id,
            cache_dir=HF_CACHE_DIR,
            local_dir_use_symlinks=False,
        )
    except Exception as e:
        _raise_http(f"Download failed: {e}", 500, e)

    try:
        upsert_hf_model(model_id=model_id, repo_id=repo_id)
        reload_model_config()
        reload_model_registry()
    except Exception as e:
        _raise_http(f"Register failed: {e}", 500, e)

    return {
        "ok": True,
        "model_type": "hf",
        "mode": "base",
        "model_id": model_id,
        "repo_id": repo_id,
        "model_registry": MODEL_REGISTRY,
    }


@router.post("/models/gguf/register")
def register_gguf_model(req: RegisterGGUFModelReq, _admin: User = Depends(require_admin)):
    """
    Register a GGUF model path (does not download).
    """
    ensure_model_paths()
    model_id = _require_safe_model_id(req.model_id)
    path = (req.path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")

    try:
        upsert_gguf_model(model_id=model_id, path=path)
        reload_model_config()
        reload_model_registry()
    except Exception as e:
        _raise_http(f"Register failed: {e}", 500, e)

    return {
        "ok": True,
        "model_id": model_id,
        "path": path,
        "model_registry": MODEL_REGISTRY,
    }


@router.post("/models/gguf/download")
def download_gguf_model(req: DownloadGGUFModelReq, _admin: User = Depends(require_admin)):
    """
    Download a GGUF file from a direct URL into `models/gguf/` and register it.

    NOTE:
    - Requires internet access from the machine running the backend.
    - This endpoint is intentionally strict for safety.
    """
    ensure_model_paths()
    model_id = _require_safe_model_id(req.model_id)
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    filename = (req.filename or "").strip()
    if not filename:
        try:
            from urllib.parse import urlparse
            import os as _os

            filename = _os.path.basename(urlparse(url).path)
        except Exception:
            filename = ""

    filename = (filename or "").strip()
    if not filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filename.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail="filename must end with .gguf")

    max_mb = max(1, int(req.max_mb or 1))
    max_bytes = max_mb * 1024 * 1024

    dest_dir = Path(GGUF_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    tmp_path = dest_dir / f"{filename}.part"

    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()

        cl = resp.headers.get("Content-Length")
        if cl and str(cl).isdigit():
            size = int(cl)
            if size > max_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"File too large ({size // (1024 * 1024)}MB) > max_mb={max_mb}",
                )

        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Download exceeded max_mb={max_mb}",
                    )
                f.write(chunk)
    except HTTPException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        _raise_http(f"Download failed: {e}", 500, e)

    try:
        if dest_path.exists():
            dest_path.unlink()
        tmp_path.replace(dest_path)
    except Exception as e:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Finalize download failed: {e}")

    try:
        upsert_gguf_model(model_id=model_id, path=str(dest_path))
        reload_model_config()
        reload_model_registry()
    except Exception as e:
        _raise_http(f"Register failed: {e}", 500, e)

    return {
        "ok": True,
        "model_id": model_id,
        "path": str(dest_path),
        "model_registry": MODEL_REGISTRY,
    }


@router.patch("/models/registry")
def patch_models_registry(patch: Dict[str, Any] = Body(...), _admin: User = Depends(require_admin)):
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
def test_model(req: TestModelReq, _admin: User = Depends(require_admin)):
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


@router.delete("/models/{model_id}")
def delete_model_endpoint(model_id: str, _admin: User = Depends(require_admin)):
    """
    Remove a registered model from the dev config.
    Deletes files from disk (GGUF or HF cache).
    """
    try:
        ensure_model_paths()
        cfg = load_model_config()
        delete_report: Dict[str, Any] = {"gguf": None, "hf_cache": None}

        gguf_path = cfg.get("gguf_models", {}).get(model_id) or GGUF_MODELS.get(model_id)
        if gguf_path:
            resolved = _resolve_gguf_path(gguf_path)
            delete_report["gguf"] = _safe_delete_path(resolved, Path(GGUF_DIR))

        repo_id = cfg.get("hf_models", {}).get(model_id) or HF_MODELS.get(model_id)
        if repo_id:
            cache_dir = _hf_repo_cache_dir(repo_id)
            delete_report["hf_cache"] = _safe_delete_path(cache_dir, Path(HF_CACHE_DIR))

        cfg, info = delete_model(model_id)
        reload_model_config()
        reload_model_registry()
    except ValueError as e:
        _raise_http(str(e), 400, e)
    except Exception as e:
        _raise_http(str(e), 500, e)

    return {
        "ok": True,
        "model_id": model_id,
        "info": info,
        "deleted": delete_report,
        "model_config": cfg,
        "model_registry": MODEL_REGISTRY,
    }


# -------------------- Database Visibility (Admin-only) --------------------

@router.get("/dbs")
def list_databases(_admin: User = Depends(require_admin)):
    return {
        "databases": [
            {"id": k, "label": v["label"], "type": v["type"]}
            for k, v in _DB_SOURCES.items()
        ]
    }


@router.get("/dbs/{db_id}/tables")
def list_db_tables(db_id: str, _admin: User = Depends(require_admin)):
    meta = _DB_SOURCES.get(db_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Unknown database id")

    if meta["type"] == "postgres":
        dsn = _get_db_dsn(db_id)
        tables = _list_tables(dsn)
        return {"db_id": db_id, "tables": tables}

    if meta["type"] == "redis":
        return {"db_id": db_id, "tables": ["keys"]}

    if meta["type"] == "minio":
        return {"db_id": db_id, "tables": ["objects"]}

    raise HTTPException(status_code=400, detail="Unsupported database type")


@router.get("/dbs/{db_id}/records")
def list_db_records(
    db_id: str,
    table: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
):
    meta = _DB_SOURCES.get(db_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Unknown database id")

    if meta["type"] == "postgres":
        dsn = _get_db_dsn(db_id)
        tables = _list_tables(dsn)
        if table not in tables:
            raise HTTPException(status_code=404, detail="Table not found")
        cols = _list_columns(dsn, table)
        data = _fetch_rows(dsn, table, cols, limit, offset)
        return {
            "db_id": db_id,
            "table": table,
            "limit": limit,
            "offset": offset,
            **data,
        }

    if meta["type"] == "redis":
        if table != "keys":
            raise HTTPException(status_code=404, detail="Table not found")
        if not redis_client:
            raise HTTPException(status_code=500, detail="Redis not configured or unavailable")

        keys = list(redis_client.scan_iter(match="*", count=1000))
        total = len(keys)
        slice_keys = keys[offset : offset + limit]
        rows: List[Dict[str, Any]] = []
        for k in slice_keys:
            try:
                ktype = redis_client.type(k)
                value = None
                if ktype == "string":
                    value = redis_client.get(k)
                    if isinstance(value, str) and len(value) > 500:
                        value = value[:500] + "..."
                elif ktype == "list":
                    value = f"list[{redis_client.llen(k)}]"
                elif ktype == "set":
                    value = f"set[{redis_client.scard(k)}]"
                elif ktype == "zset":
                    value = f"zset[{redis_client.zcard(k)}]"
                elif ktype == "hash":
                    value = f"hash[{redis_client.hlen(k)}]"
                rows.append({"key": k, "type": ktype, "value": value})
            except Exception:
                rows.append({"key": k, "type": "unknown", "value": None})

        return {
            "db_id": db_id,
            "table": table,
            "limit": limit,
            "offset": offset,
            "columns": ["key", "type", "value"],
            "excluded_columns": [],
            "rows": rows,
            "total": total,
        }

    if meta["type"] == "minio":
        if table != "objects":
            raise HTTPException(status_code=404, detail="Table not found")
        client = get_minio_client()
        if not client:
            raise HTTPException(status_code=500, detail="MinIO not configured or unavailable")

        bucket = os.getenv("MINIO_BUCKET", "kavin-documents")
        try:
            if not client.bucket_exists(bucket):
                return {
                    "db_id": db_id,
                    "table": table,
                    "limit": limit,
                    "offset": offset,
                    "columns": ["object", "size", "last_modified"],
                    "excluded_columns": [],
                    "rows": [],
                    "total": 0,
                }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"MinIO bucket check failed: {e}")

        objects = list(client.list_objects(bucket, recursive=True))
        total = len(objects)
        slice_objects = objects[offset : offset + limit]
        rows = [
            {
                "object": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
            }
            for obj in slice_objects
        ]

        return {
            "db_id": db_id,
            "table": table,
            "limit": limit,
            "offset": offset,
            "columns": ["object", "size", "last_modified"],
            "excluded_columns": [],
            "rows": rows,
            "total": total,
        }

    raise HTTPException(status_code=400, detail="Unsupported database type")


@router.post("/reset/rag")
def reset_rag_db(req: ResetRequest, _admin: User = Depends(require_admin)):
    _require_destructive_enabled()
    _require_reset_confirm(req.confirm)

    dsn = _psycopg2_dsn(os.getenv("DB_CONNECTION", DB_CONNECTION))
    result = _truncate_tables(dsn, ["langchain_pg_embedding", "langchain_pg_collection"])
    return {"ok": True, "target": "rag_db", **result}


@router.post("/reset/chat")
def reset_chat_db(req: ResetRequest, _admin: User = Depends(require_admin)):
    _require_destructive_enabled()
    _require_reset_confirm(req.confirm)

    dsn = os.getenv("CHAT_DB_URL", "postgresql://postgres:1@localhost:5432/chat_memory_db")
    tables = [
        "chat_messages",
        "chat_sessions",
        "session_topic_hints",
        "session_active_documents",
    ]
    result = _truncate_tables(dsn, tables)
    return {"ok": True, "target": "chat_db", **result}


@router.post("/reset/redis")
def reset_redis(req: ResetRequest, _admin: User = Depends(require_admin)):
    _require_destructive_enabled()
    _require_reset_confirm(req.confirm)

    if not redis_client:
        raise HTTPException(status_code=500, detail="Redis not configured or unavailable")

    if bool(req.wipe_redis_all):
        redis_client.flushdb()
        return {"ok": True, "target": "redis", "mode": "flushdb"}

    patterns = ["rag:*", "abort:*"]
    keys: List[str] = []
    for pat in patterns:
        try:
            keys.extend(list(redis_client.scan_iter(match=pat, count=1000)))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Redis scan failed: {e}")

    deleted = 0
    try:
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            if not batch:
                continue
            deleted += int(redis_client.delete(*batch))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Redis delete failed: {e}")

    return {
        "ok": True,
        "target": "redis",
        "mode": "patterns",
        "patterns": patterns,
        "deleted": deleted,
    }


@router.post("/reset/minio")
def reset_minio(req: ResetRequest, _admin: User = Depends(require_admin)):
    _require_destructive_enabled()
    _require_reset_confirm(req.confirm)

    client = get_minio_client()
    if not client:
        raise HTTPException(status_code=500, detail="MinIO not configured or unavailable")

    bucket = (req.minio_bucket or os.getenv("MINIO_BUCKET", "kavin-documents")).strip()
    if not bucket:
        raise HTTPException(status_code=400, detail="minio_bucket is required")

    try:
        if not client.bucket_exists(bucket):
            return {"ok": True, "target": "minio", "bucket": bucket, "deleted": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MinIO bucket check failed: {e}")

    deleted = 0
    try:
        for obj in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, obj.object_name)
            deleted += 1
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MinIO delete failed: {e}")

    return {"ok": True, "target": "minio", "bucket": bucket, "deleted": deleted}


@router.post("/reset/all")
def reset_all(req: ResetRequest, _admin: User = Depends(require_admin)):
    _require_destructive_enabled()
    _require_reset_confirm(req.confirm)

    rag = reset_rag_db(req)
    chat = reset_chat_db(req)
    redis = reset_redis(req)
    minio = reset_minio(req)

    return {
        "ok": True,
        "rag": rag,
        "chat": chat,
        "redis": redis,
        "minio": minio,
    }


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
