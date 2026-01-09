"""
backend/api/net_key.py

Net API Key verification and activation for KavinBase Net.
Supports Groq and xAI (Grok).

Rules:
- Detect provider by key prefix
- Verify using REAL provider chat endpoint
- NEVER echo keys
- NEVER persist keys to disk
- Activate Net provider only after verification
"""

import requests
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.llm.net_models import (
    activate_net_provider,
)

# ============================================================
# ROUTER
# ============================================================

router = APIRouter(prefix="/net-key", tags=["KavinBase Net"])

# ============================================================
# PROVIDERS
# ============================================================

Provider = Literal["groq", "xai"]

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"

# ============================================================
# SCHEMAS
# ============================================================

class NetKeyVerifyRequest(BaseModel):
    api_key: str = Field(..., min_length=20)


class NetKeyVerifyResponse(BaseModel):
    valid: bool
    provider: Provider
    message: str

# ============================================================
# INTERNAL HELPERS
# ============================================================

def _detect_provider(api_key: str) -> Provider:
    if api_key.startswith("gsk_"):
        return "groq"
    if api_key.startswith("xai-"):
        return "xai"

    raise HTTPException(
        status_code=400,
        detail="Unknown API key format",
    )


def _verify_groq(api_key: str) -> None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }

    resp = requests.post(
        GROQ_CHAT_URL,
        headers=headers,
        json=payload,
        timeout=10,
    )

    if resp.status_code == 401:
        raise HTTPException(401, "Invalid Groq API key")

    if resp.status_code == 429:
        raise HTTPException(429, "Groq rate limit exceeded")

    if resp.status_code >= 400:
        raise HTTPException(
            502,
            f"Groq verification failed [{resp.status_code}]",
        )


def _verify_xai(api_key: str) -> None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "grok-beta",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }

    resp = requests.post(
        XAI_CHAT_URL,
        headers=headers,
        json=payload,
        timeout=10,
    )

    if resp.status_code == 401:
        raise HTTPException(401, "Invalid xAI API key")

    if resp.status_code == 429:
        raise HTTPException(429, "xAI rate limit exceeded")

    if resp.status_code >= 400:
        raise HTTPException(
            502,
            f"xAI verification failed [{resp.status_code}]",
        )

# ============================================================
# ENDPOINT
# ============================================================

@router.post("/verify", response_model=NetKeyVerifyResponse)
def verify_net_key(req: NetKeyVerifyRequest):
    api_key = req.api_key.strip()
    provider = _detect_provider(api_key)

    # --------------------------------------------
    # VERIFY AGAINST REAL PROVIDER
    # --------------------------------------------

    if provider == "groq":
        _verify_groq(api_key)
    else:
        _verify_xai(api_key)

    # --------------------------------------------
    # ACTIVATE NET (SINGLE SOURCE OF TRUTH)
    # --------------------------------------------

    activate_net_provider(
        provider=provider,
        api_key=api_key,
    )

    return NetKeyVerifyResponse(
        valid=True,
        provider=provider,
        message=f"{provider.upper()} API key verified and activated",
    )
