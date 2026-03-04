from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class User:
    username: str
    email: str
    role: str = "admin"
    disabled: bool = False


_LOCK = Lock()
_USER_DB_PATH = Path(__file__).resolve().parent / "users.json"


def _normalize_identifier(value: str) -> str:
    return (value or "").strip().lower()


def get_configured_user() -> User:
    username = os.getenv("PML_ADMIN_USERNAME", "pml_admin").strip() or "pml_admin"
    email = os.getenv("PML_ADMIN_EMAIL", "pml_admin@example.com").strip() or "pml_admin@example.com"
    return User(username=username, email=email, role="admin", disabled=False)


def _get_configured_password() -> str:
    return os.getenv("PML_ADMIN_PASSWORD", "").strip()


def _new_salt() -> bytes:
    return os.urandom(16)


def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return dk.hex()


def _load_db() -> Dict[str, Any]:
    with _LOCK:
        if not _USER_DB_PATH.exists():
            return {"users": []}
        try:
            data = json.loads(_USER_DB_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"users": []}
        if not isinstance(data, dict):
            return {"users": []}
        users = data.get("users")
        if not isinstance(users, list):
            users = []
        return {"users": users}


def _save_db(db: Dict[str, Any]) -> None:
    with _LOCK:
        _USER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USER_DB_PATH.write_text(
            json.dumps(db, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _find_user_record(db: Dict[str, Any], identifier: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    ident = _normalize_identifier(identifier)
    users = db.get("users", [])
    for i, u in enumerate(users):
        if _normalize_identifier(u.get("username")) == ident or _normalize_identifier(u.get("email")) == ident:
            return i, u
    return None, None


def get_user_by_identifier(identifier: str) -> Optional[User]:
    identifier = _normalize_identifier(identifier)
    if not identifier:
        return None

    admin = get_configured_user()
    if identifier in (_normalize_identifier(admin.username), _normalize_identifier(admin.email)):
        return admin

    db = _load_db()
    _, rec = _find_user_record(db, identifier)
    if not rec:
        return None

    return User(
        username=rec.get("username", ""),
        email=rec.get("email", ""),
        role=rec.get("role", "admin"),
        disabled=bool(rec.get("disabled", False)),
    )


def get_user_for_token(username: Optional[str], email: Optional[str]) -> Optional[User]:
    for ident in (username or "", email or ""):
        if not ident:
            continue
        user = get_user_by_identifier(ident)
        if user and not user.disabled:
            return user
    return None


def verify_credentials(identifier: str, password: str) -> Optional[User]:
    identifier = (identifier or "").strip()
    password = password or ""
    if not identifier or not password:
        return None

    admin = get_configured_user()
    expected_password = _get_configured_password()
    if identifier in (admin.username, admin.email):
        if not expected_password:
            return None
        if hmac.compare_digest(password, expected_password):
            return admin
        return None

    db = _load_db()
    _, rec = _find_user_record(db, identifier)
    if not rec or rec.get("disabled"):
        return None

    try:
        salt = bytes.fromhex(rec.get("salt", ""))
    except Exception:
        return None

    computed = _hash_password(password, salt)
    stored = str(rec.get("password_hash") or "")
    if not stored:
        return None
    if not hmac.compare_digest(computed, stored):
        return None

    return User(
        username=rec.get("username", ""),
        email=rec.get("email", ""),
        role=rec.get("role", "admin"),
        disabled=False,
    )


def create_local_user(*, username: str, email: str, password: str, role: str = "admin") -> User:
    if role not in {"admin", "developer"}:
        role = "admin"

    username = (username or "").strip()
    email = (email or "").strip()
    password = password or ""
    if not username or not email or len(password) < 8:
        raise ValueError("username/email required and password must be at least 8 chars")

    db = _load_db()
    _, existing = _find_user_record(db, username)
    if existing:
        raise ValueError("User already exists")
    _, existing = _find_user_record(db, email)
    if existing:
        raise ValueError("User already exists")

    salt = _new_salt()
    record = {
        "username": username,
        "email": email,
        "role": role,
        "disabled": False,
        "password_hash": _hash_password(password, salt),
        "salt": salt.hex(),
    }
    db["users"].append(record)
    _save_db(db)
    return User(username=username, email=email, role=role, disabled=False)

