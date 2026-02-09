# backend/api/auth.py

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from backend.auth.deps import AUTH_COOKIE_NAME, require_user
from backend.auth.tokens import create_token
from backend.auth.user_store import User, verify_credentials
import os


router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    identifier: str
    password: str


class UserResponse(BaseModel):
    username: str
    email: str
    role: str | None = None


def _get_cookie_options() -> dict:
    # For local/dev over http://localhost
    secure = os.getenv("KAVIN_AUTH_COOKIE_SECURE", "0").strip() in ("1", "true", "True")
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
    }


@router.post("/login", response_model=UserResponse)
def login(payload: LoginRequest, response: Response):
    if not os.getenv("KAVIN_ADMIN_PASSWORD", "").strip():
        raise HTTPException(
            status_code=500,
            detail="Auth not configured (KAVIN_ADMIN_PASSWORD is missing)",
        )

    user = verify_credentials(payload.identifier, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    secret = os.getenv("KAVIN_AUTH_SECRET", "").strip()
    if not secret:
        raise HTTPException(500, "Auth secret not configured (KAVIN_AUTH_SECRET)")

    ttl_seconds = int(os.getenv("KAVIN_AUTH_TTL_SECONDS", str(60 * 60 * 24 * 7)))
    token = create_token(
        {"sub": user.username, "email": user.email},
        secret=secret,
        ttl_seconds=ttl_seconds,
    )

    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=ttl_seconds,
        **_get_cookie_options(),
    )

    return {"username": user.username, "email": user.email, "role": user.role}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(require_user)):
    return {"username": user.username, "email": user.email, "role": user.role}
