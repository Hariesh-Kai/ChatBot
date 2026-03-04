from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request

from backend.auth.tokens import verify_token
from backend.pml_auth.user_store import User, get_user_for_token


AUTH_COOKIE_NAME = os.getenv("PML_AUTH_COOKIE_NAME", "pml_auth").strip() or "pml_auth"


def _get_auth_secret() -> str:
    return os.getenv("PML_AUTH_SECRET", "").strip()


def get_current_user(request: Request) -> Optional[User]:
    secret = _get_auth_secret()
    if not secret:
        raise HTTPException(500, "PML auth secret not configured (PML_AUTH_SECRET)")

    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None

    payload = verify_token(token, secret=secret)
    if not payload:
        return None

    sub = payload.get("sub")
    email = payload.get("email")
    user = get_user_for_token(sub, email)
    if not user:
        return None
    return user


def require_user(request: Request) -> User:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

