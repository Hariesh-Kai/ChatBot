# backend/api/main.py

# ============================================================
# 1. LOAD ENV VARS FIRST (CRITICAL FIX)
# ============================================================
from dotenv import load_dotenv
load_dotenv()  # <-- REQUIRED BEFORE ANY BACKEND IMPORTS

import psutil
from fastapi import FastAPI
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

# Render & DevTools
from backend.api.render import router as render_router
from backend.api.devtools import router as devtools_router

# ============================================================
#  NEW IMPORT (LEARNING – FEEDBACK API)
# ============================================================
from backend.api.feedback import router as feedback_router
# ↑ ADDED: registers /feedback endpoint


# ============================================================
# IMPORT HEALTH CHECK DEPENDENCIES
# ============================================================
from backend.memory.pg_memory import get_connection
from backend.memory.redis_memory import r as redis_client
from backend.storage.minio_client import get_minio_client


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="KAVIN Backend API",
    description=(
        "RAG + Multi-LLM Backend for KavinBase\n\n"
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




# ============================================================
# CORS CONFIGURATION
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROUTER REGISTRATION (ORDER MATTERS)
# ============================================================

# Core APIs
app.include_router(chat_router)                 # POST /chat
app.include_router(upload_router)               # POST /upload
app.include_router(metadata_correct_router)     # POST /metadata/correct
app.include_router(metadata_commit_router)      # POST /metadata/update
app.include_router(abort_router)                # POST /abort

# ============================================================
#  NEW ROUTER REGISTRATION (LEARNING FEEDBACK)
# ============================================================
app.include_router(feedback_router)              # POST /feedback
# ↑ ADDED: stores user feedback safely

# Debug & external services
app.include_router(debug_router)                # GET /debug/rag/{session_id}
app.include_router(net_router)                  # /net/*
app.include_router(net_key_router)              # /net-key/*
app.include_router(retrieve_router)             # /retrieve/*

# Viewer & Dev tools
app.include_router(render_router)               # GET /render/image
app.include_router(devtools_router)             # POST /devtools/*


# ============================================================
# BASIC INFO ENDPOINT
# ============================================================

@app.get("/", tags=["Health"])
def root_info():
    return {
        "status": "ok",
        "service": "KAVIN Backend",
        "features": [
            "RAG (Postgres + pgvector)",
            "KavinBase Lite (GGUF / CPU)",
            "KavinBase Base (HF Transformers)",
            "KavinBase Net (Groq / xAI)",
            "Agent-aware metadata workflow",
            "RAG Debug Observability",
            "Answer Confidence Scoring",
            "Source Highlighting & Rendering",
            "Developer Method Dashboard",
            "Resource Aware Dispatcher",
            #  NEW FEATURE FLAG
            "Learning Telemetry (Stats + Feedback)",
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
            "minio": "unknown"
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
    except Exception as e:
        status["services"]["minio"] = f"error: {e}"
        all_ok = False

    if not all_ok:
        status["status"] = "degraded"

    return status
