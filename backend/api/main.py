# backend/api/main.py

# ============================================================
# 1. LOAD ENV VARS FIRST (CRITICAL FIX)
# ============================================================
from dotenv import load_dotenv
load_dotenv()  # <-- This must happen before other backend imports

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from backend.api.net import router as net_router           # /net/*
from backend.api.net_key import router as net_key_router   # /net-key/*
from backend.api.debug_rag import router as debug_router   # 🐞 Debug RAG
from backend.api.retrieve import router as retrieve_router # Optional retrieval APIs

# ✅ NEW: Render Router for Source Viewer
from backend.api.render import router as render_router 

# ============================================================
# IMPORT HEALTH CHECK DEPENDENCIES
# ============================================================
from backend.memory.pg_memory import get_connection
from backend.memory.redis_memory import r as redis_client
from backend.storage.minio_client import get_minio_client


# ============================================================
# FASTAPI APPLICATION (SINGLE ENTRY POINT)
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
# CORS CONFIGURATION
# ============================================================
# ⚠️ OPEN FOR DEVELOPMENT ONLY
# Lock origins in production

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROUTER REGISTRATION (ORDER MATTERS)
# ============================================================

# Core chat & RAG APIs
app.include_router(chat_router)        # POST /chat
app.include_router(upload_router)      # POST /upload
app.include_router(metadata_correct_router)   # POST /metadata/correct
app.include_router(metadata_commit_router)    # POST /metadata/update
app.include_router(abort_router)       # POST /abort

# 🐞 Debug APIs (READ-ONLY, SAFE)
app.include_router(debug_router)       # GET /debug/rag/{session_id}

# 🔑 Net APIs (External LLMs)
app.include_router(net_router)         # /net/*
app.include_router(net_key_router)     # /net-key/*
app.include_router(retrieve_router)    # /retrieve/* (optional)

# ✅ Render API (Source Viewer)
app.include_router(render_router)      # GET /render/image


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
            "Source Highlighting & Rendering"
        ],
    }


# ============================================================
# DEEP HEALTH CHECK (DB + REDIS + MINIO)
# ============================================================

@app.get("/health", tags=["Health"])
def health_check():
    """
    Checks connectivity to all critical infrastructure.
    """
    status = {
        "status": "ok",
        "services": {
            "postgres": "unknown",
            "redis": "unknown",
            "minio": "unknown"
        }
    }
    
    all_ok = True

    # 1. Check Postgres
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        status["services"]["postgres"] = "ok"
    except Exception as e:
        status["services"]["postgres"] = f"error: {str(e)}"
        all_ok = False

    # 2. Check Redis
    try:
        if redis_client and redis_client.ping():
            status["services"]["redis"] = "ok"
        else:
            status["services"]["redis"] = "disabled/error"
            # Redis is optional for some local configs, usually not critical fail
    except Exception as e:
        status["services"]["redis"] = f"error: {str(e)}"
        # all_ok = False (Optional: uncomment if Redis is strictly required)

    # 3. Check MinIO
    try:
        # Initializing client is cheap; listing buckets proves auth works
        client = get_minio_client()
        if client: 
             client.list_buckets()
             status["services"]["minio"] = "ok"
    except Exception as e:
        status["services"]["minio"] = f"error: {str(e)}"
        all_ok = False

    if not all_ok:
        status["status"] = "degraded"

    return status