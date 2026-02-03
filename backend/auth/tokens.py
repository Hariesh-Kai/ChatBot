# backend/auth/tokens.py

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(payload_b64: str, secret: str) -> str:
    sig = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(sig)


def create_token(
    payload: Dict[str, Any],
    *,
    secret: str,
    ttl_seconds: int = 60 * 60 * 24 * 7,  # 7 days
) -> str:
    now = int(time.time())
    data = dict(payload)
    data["iat"] = now
    data["exp"] = now + int(ttl_seconds)

    payload_json = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    payload_b64 = _b64url_encode(payload_json)
    sig_b64 = _sign(payload_b64, secret)
    return f"{payload_b64}.{sig_b64}"


def verify_token(token: str, *, secret: str) -> Optional[Dict[str, Any]]:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        return None

    expected_sig = _sign(payload_b64, secret)
    if not hmac.compare_digest(expected_sig, sig_b64):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    exp = payload.get("exp")
    if not isinstance(exp, int):
        return None
    if exp < int(time.time()):
        return None

    return payload

