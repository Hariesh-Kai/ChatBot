# backend/auth/user_store.py

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class User:
    username: str
    email: str


def get_configured_user() -> User:
    username = os.getenv("KAVIN_ADMIN_USERNAME", "admin").strip() or "admin"
    email = os.getenv("KAVIN_ADMIN_EMAIL", "admin@example.com").strip() or "admin@example.com"
    return User(username=username, email=email)


def _get_configured_password() -> str:
    # For local/dev usage only (stored in .env). Prefer a secrets manager in production.
    return os.getenv("KAVIN_ADMIN_PASSWORD", "")


def verify_credentials(identifier: str, password: str) -> Optional[User]:
    identifier = (identifier or "").strip()
    password = password or ""

    user = get_configured_user()
    expected_password = _get_configured_password()

    if not expected_password:
        # Misconfigured backend (no password set) -> never authenticate.
        return None

    if identifier not in (user.username, user.email):
        return None

    if not hmac.compare_digest(password, expected_password):
        return None

    return user

