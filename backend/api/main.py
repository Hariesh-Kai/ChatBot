# backend/api/main.py

# ============================================================
# 1. LOAD ENV VARS FIRST (CRITICAL FIX)
# ============================================================
from pathlib import Path
from dotenv import load_dotenv
# Always load the repo-root `.env` (works even if uvicorn is started from a different cwd).
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

import psutil
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

#  EXISTING: CPU limiter
from backend.rag.resource_planner import limit_cpu_usage
from backend.llm.model_selector import resolve_model_id

# ============================================================
# IMPORT API ROUTERS
# ============================================================

from backend.api.chat import router as chat_router
from backend.api.abort import router as abort_router
from backend.api.upload import router as upload_router

# Metadata routers
from backend.api.update import router as metadata_commit_router
from backend.api.metadata import router as metadata_correct_router

# Net & Debug routers
from backend.api.net import router as net_router
from backend.api.net_key import router as net_key_router
from backend.api.debug_rag import router as debug_router
from backend.api.retrieve import router as retrieve_router
from backend.api.pml_chat import router as pml_chat_router
from backend.api.session import router as session_router

# Render & DevTools
from backend.api.render import router as render_router
from backend.api.devtools import router as devtools_router

# Auth
from backend.api.auth import router as auth_router
from backend.auth.deps import require_user
from backend.api.team import router as team_router

# ============================================================
#  NEW IMPORT (LEARNING – FEEDBACK API)
# ============================================================
from backend.api.feedback import router as feedback_router
from backend.api.audit_log import router as audit_log_router
# ↑ ADDED: registers /feedback endpoint
# ↑ ADDED: registers /audit endpoints (Phase 3 RAG audit log)


# ============================================================
# IMPORT HEALTH CHECK DEPENDENCIES
# ============================================================
from backend.memory.pg_memory import get_connection
from backend.memory.redis_memory import r as redis_client
from backend.storage.minio_client import get_minio_client
from backend.storage.minio_outbox import (
    get_outbox_summary,
    start_outbox_worker,
    stop_outbox_worker,
)
from backend.queue.celery_app import is_celery_enabled, use_celery_for_outbox
from backend.runtime_status import get_rabbitmq_status


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Kavin Backend API",
    description=(
        "RAG + Multi-LLM Backend for Kavin\n\n"
        "Modes:\n"
        "- Lite  (GGUF / CPU)\n"
        "- Base  (HF / GPU-aware)\n"
        "- Net   (Groq / xAI)\n"
    ),
    version="1.0.0",
)


# ============================================================
# 🚦 STARTUP EVENT (CPU SAFETY)
# ============================================================

@app.on_event("startup")
async def startup_event():
    # CPU safety
    try:
        total_cores = psutil.cpu_count(logical=True) or 2
        safe_cores = max(1, int(total_cores * 0.75))
        print(f"🚦 [STARTUP] CPU Affinity {safe_cores}/{total_cores}")
        limit_cpu_usage(safe_cores)
    except Exception as e:
        print(f"[STARTUP] CPU affinity failed: {e}")

    # Model warmup
    try:
        from backend.llm.loader import get_llm
        get_llm(resolve_model_id("lite"))
        print("[STARTUP] Lite model warmed")
    except Exception as e:
        print(f"[STARTUP] Model warmup skipped: {e}")

    # Start local MinIO outbox worker only when outbox is not handled by Celery beat.
    if use_celery_for_outbox():
        print("[STARTUP] MinIO outbox retries delegated to Celery beat.")
    else:
        try:
            start_outbox_worker()
        except Exception as e:
            print(f"[STARTUP] MinIO outbox worker failed to start: {e}")

    if is_celery_enabled():
        print("[STARTUP] Celery mode enabled. Commit jobs will run on RabbitMQ workers.")


@app.on_event("shutdown")
async def shutdown_event():
    if not use_celery_for_outbox():
        try:
            stop_outbox_worker()
        except Exception as e:
            print(f"[SHUTDOWN] MinIO outbox worker stop failed: {e}")




# ============================================================
# CORS CONFIGURATION
# ============================================================

app.add_middleware(
    CORSMiddleware,
    # Cookie auth requires explicit origins (not "*") when allow_credentials=True.
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROUTER REGISTRATION (ORDER MATTERS)
# ============================================================

# Auth (public)
app.include_router(auth_router)                 # /auth/*
app.include_router(team_router)                 # /team/* (HTTP auth inside routes + /team/ws)

# Core APIs (protected)
_auth = [Depends(require_user)]
app.include_router(chat_router, dependencies=_auth)                 # POST /chat
app.include_router(upload_router, dependencies=_auth)               # POST /upload
app.include_router(metadata_correct_router, dependencies=_auth)     # POST /metadata/correct
app.include_router(metadata_commit_router, dependencies=_auth)      # POST /metadata/update
app.include_router(abort_router, dependencies=_auth)                # POST /abort
app.include_router(session_router, dependencies=_auth)              # /session/*

# ============================================================
#  NEW ROUTER REGISTRATION (LEARNING FEEDBACK)
# ============================================================
app.include_router(feedback_router, dependencies=_auth)              # POST /feedback
app.include_router(audit_log_router, dependencies=_auth)            # GET /audit/recent, /audit/stats
# ↑ ADDED: stores user feedback safely

# Debug & external services
app.include_router(debug_router, dependencies=_auth)                # GET /debug/rag/{session_id}
app.include_router(net_router, dependencies=_auth)                  # /net/*
app.include_router(net_key_router, dependencies=_auth)              # /net-key/*
app.include_router(retrieve_router, dependencies=_auth)             # /retrieve/*
app.include_router(pml_chat_router, dependencies=_auth)             # /pml-chat/*

# Viewer & Dev tools
app.include_router(render_router, dependencies=_auth)               # GET /render/image
app.include_router(devtools_router, dependencies=_auth)             # POST /devtools/*


# ============================================================
# BASIC INFO ENDPOINT
# ============================================================

@app.get("/", tags=["Health"])
def root_info():
    return {
        "status": "ok",
        "service": "Kavin Backend",
        "features": [
            "RAG (Postgres + pgvector)",
            "Kavin Lite (GGUF / CPU)",
            "Kavin Base (HF Transformers)",
            "Kavin Net (Groq / xAI)",
            "Agent-aware metadata workflow",
            "RAG Debug Observability",
            "Answer Confidence Scoring",
            "Source Highlighting & Rendering",
            "Developer Method Dashboard",
            "Resource Aware Dispatcher",
            "Learning Telemetry (Stats + Feedback)",
            # Phase 1-4 features
            "BM25 Keyword Search + RRF Fusion (Phase 1)",
            "Grounding / Hallucination Check (Phase 1)",
            "Semantic Cache + Multi-Query (Phase 2)",
            "Feedback-Driven Chunk Boosting (Phase 2)",
            "RAGAS-Style Evaluation (Phase 3)",
            "PII Detection (Phase 3)",
            "RAG Audit Log (Phase 3)",
            "Adaptive Retrieval K-Tuning (Phase 4)",
            "Few-Shot In-Context Learning (Phase 4)",
            "RLHF Training Data Export (Phase 4)",
        ],
    }


# ============================================================
# HEALTH CHECK ENDPOINT
# ============================================================

@app.get("/health", tags=["Health"])
def health_check():

    status = {
        "status": "ok",
        "services": {
            "postgres": "unknown",
            "redis": "unknown",
            "minio": "unknown",
            "minio_outbox": "unknown",
            "rabbitmq": "unknown",
        }
    }

    all_ok = True

    # Postgres
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        status["services"]["postgres"] = "ok"
    except Exception as e:
        status["services"]["postgres"] = f"error: {e}"
        all_ok = False

    # Redis
    try:
        if redis_client and redis_client.ping():
            status["services"]["redis"] = "ok"
        else:
            status["services"]["redis"] = "disabled/error"
    except Exception as e:
        status["services"]["redis"] = f"error: {e}"

    # MinIO
    try:
        client = get_minio_client()
        if client:
            client.list_buckets()
            status["services"]["minio"] = "ok"
        else:
            status["services"]["minio"] = "disabled/unavailable"
    except Exception as e:
        status["services"]["minio"] = f"error: {e}"

    # MinIO outbox
    try:
        summary = get_outbox_summary()
        counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
        pending = int(counts.get("pending", 0))
        failed = int(counts.get("failed", 0))
        uploading = int(counts.get("uploading", 0))
        status["services"]["minio_outbox"] = (
            f"pending={pending}, uploading={uploading}, failed={failed}"
        )
    except Exception as e:
        status["services"]["minio_outbox"] = f"error: {e}"

    # RabbitMQ (optional)
    try:
        rabbit = get_rabbitmq_status()
        rabbit_status = str(rabbit.get("status") or "unknown")
        if rabbit_status == "ok":
            status["services"]["rabbitmq"] = "ok"
        elif rabbit_status == "disabled":
            status["services"]["rabbitmq"] = "disabled/unconfigured"
        elif rabbit_status == "not_rabbitmq":
            status["services"]["rabbitmq"] = "configured/non-rabbitmq-broker"
        else:
            detail = rabbit.get("error") or "unreachable"
            status["services"]["rabbitmq"] = f"error: {detail}"
    except Exception as e:
        status["services"]["rabbitmq"] = f"error: {e}"

    if not all_ok:
        status["status"] = "degraded"

    return status
