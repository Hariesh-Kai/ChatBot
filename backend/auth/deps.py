# backend/auth/deps.py

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request

from backend.auth.tokens import verify_token
from backend.auth.user_store import User, get_configured_user

AUTH_COOKIE_NAME = "kavin_auth"


def _get_auth_secret() -> str:
    # Local/dev only. In production, set a strong secret via your deployment environment.
    return os.getenv("KAVIN_AUTH_SECRET", "").strip()


def get_current_user(request: Request) -> Optional[User]:
    secret = _get_auth_secret()
    if not secret:
        # Fail closed: if you turn on auth routes, you should configure the secret.
        raise HTTPException(500, "Auth secret not configured (KAVIN_AUTH_SECRET)")

    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None

    payload = verify_token(token, secret=secret)
    if not payload:
        return None

    configured = get_configured_user()
    sub = payload.get("sub")
    email = payload.get("email")
    if sub != configured.username or email != configured.email:
        return None

    return configured


def require_user(request: Request) -> User:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

