"""
Dedicated backend app for AVEVA PML chat.

Isolation goals:
- No Chat UI chat/RAG routers are mounted here.
- Separate process/port from the main backend app.
- Auth is reused for user/session security.
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.pml_auth import router as pml_auth_router
from backend.api.pml_chat import router as pml_chat_router
from backend.pml_auth.deps import require_user
from backend.pml_chat.settings import get_pml_settings

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(
    title="PML Chat Backend API",
    description=(
        "Dedicated AVEVA PML assistant backend.\n\n"
        "This service is isolated from the main Chat UI chat/RAG service."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public auth endpoints for login/logout/me
app.include_router(pml_auth_router)

# Protected PML-only chat endpoints
_auth = [Depends(require_user)]
app.include_router(pml_chat_router, dependencies=_auth)


@app.get("/", tags=["Health"])
def root_info():
    settings = get_pml_settings()
    return {
        "status": "ok",
        "service": "PML Chat Backend",
        "configured": settings.configured,
        "model": settings.model,
    }


@app.get("/health", tags=["Health"])
def health():
    settings = get_pml_settings()
    return {
        "status": "ok",
        "services": {
            "pml_model_configured": settings.configured,
        },
    }
