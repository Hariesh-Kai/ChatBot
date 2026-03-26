# backend/auth/user_store.py

from __future__ import annotations

import hmac
import hashlib
import json
import os
import time
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional, Dict, Any, List, Tuple


@dataclass(frozen=True)
class User:
    username: str
    email: str
    role: str = "pipe_designer"
    disabled: bool = False


_LOCK = Lock()
_USER_DB_PATH = Path(__file__).resolve().parent / "users.json"
_USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{2,31}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ROLE_PIPE_DESIGNER = "pipe_designer"
ROLE_PIPE_STRESS_ENGINEER = "pipe_stress_engineer"
ROLE_PIPE_LEAD = "pipe_lead"
ROLE_PIPING_ADMIN = "piping_admin"

_ROLE_ALIASES = {
    "user": ROLE_PIPE_DESIGNER,
    "developer": ROLE_PIPE_STRESS_ENGINEER,
    "admin": ROLE_PIPING_ADMIN,
    "pipe designer": ROLE_PIPE_DESIGNER,
    "pipe_designer": ROLE_PIPE_DESIGNER,
    "pipe stress engineer": ROLE_PIPE_STRESS_ENGINEER,
    "pipe stress enginner": ROLE_PIPE_STRESS_ENGINEER,
    "pipe_stress_engineer": ROLE_PIPE_STRESS_ENGINEER,
    "pipe lead": ROLE_PIPE_LEAD,
    "pipe_lead": ROLE_PIPE_LEAD,
    "piping admin": ROLE_PIPING_ADMIN,
    "piping_admin": ROLE_PIPING_ADMIN,
}

_ROLE_HIERARCHY = {
    ROLE_PIPE_DESIGNER: 10,
    ROLE_PIPE_STRESS_ENGINEER: 20,
    ROLE_PIPE_LEAD: 30,
    ROLE_PIPING_ADMIN: 40,
}
SUPPORTED_ROLES = tuple(_ROLE_HIERARCHY.keys())


def normalize_role(role: Optional[str]) -> str:
    key = (role or ROLE_PIPE_DESIGNER).strip().lower()
    mapped = _ROLE_ALIASES.get(key, key)
    if mapped not in _ROLE_HIERARCHY:
        return ROLE_PIPE_DESIGNER
    return mapped


def role_level(role: Optional[str]) -> int:
    return _ROLE_HIERARCHY.get(normalize_role(role), _ROLE_HIERARCHY[ROLE_PIPE_DESIGNER])


def get_configured_user() -> User:
    username = os.getenv("CHAT_UI_ADMIN_USERNAME", "admin").strip() or "admin"
    email = os.getenv("CHAT_UI_ADMIN_EMAIL", "admin@example.com").strip() or "admin@example.com"
    return User(username=username, email=email, role=ROLE_PIPING_ADMIN, disabled=False)


def _get_configured_password() -> str:
    # For local/dev usage only (stored in .env). Prefer a secrets manager in production.
    return os.getenv("CHAT_UI_ADMIN_PASSWORD", "")


def is_admin(user: User) -> bool:
    admin = get_configured_user()
    return role_level(user.role) >= _ROLE_HIERARCHY[ROLE_PIPING_ADMIN] or (
        user.username == admin.username and user.email == admin.email
    )


def _normalize_identifier(value: str) -> str:
    return (value or "").strip().lower()


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


def list_users() -> List[Dict[str, Any]]:
    db = _load_db()
    out = []
    for u in db.get("users", []):
        out.append({
            "username": u.get("username"),
            "email": u.get("email"),
            "role": normalize_role(u.get("role")),
            "disabled": bool(u.get("disabled", False)),
            "created_at": u.get("created_at"),
            "updated_at": u.get("updated_at"),
            "resources": u.get("resources") or {},
        })
    return out


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
        role=normalize_role(rec.get("role")),
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


def create_user(*, email: str, username: str, password: str, role: str = ROLE_PIPE_DESIGNER, resources: Optional[Dict[str, Any]] = None) -> User:
    email = (email or "").strip()
    username = (username or "").strip()
    password = password or ""
    role = normalize_role(role)

    if not email or not username or not password:
        raise ValueError("email, username, and password are required")
    if not _EMAIL_RE.match(email):
        raise ValueError("Invalid email format")
    if not _USERNAME_RE.match(username):
        raise ValueError("username must be 3-32 chars, start with a letter, and use only letters, numbers, . _ -")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(password) > 128:
        raise ValueError("password must be 128 characters or less")

    admin = get_configured_user()
    if _normalize_identifier(email) in (_normalize_identifier(admin.email), _normalize_identifier(admin.username)):
        raise ValueError("Cannot use the admin email or username")
    if _normalize_identifier(username) in (_normalize_identifier(admin.email), _normalize_identifier(admin.username)):
        raise ValueError("Cannot use the admin email or username")

    db = _load_db()
    _, existing = _find_user_record(db, email)
    if existing:
        raise ValueError("User already exists")
    _, existing = _find_user_record(db, username)
    if existing:
        raise ValueError("User already exists")

    salt = _new_salt()
    pwd_hash = _hash_password(password, salt)
    ts = int(time.time())

    record = {
        "username": username,
        "email": email,
        "role": role,
        "disabled": False,
        "password_hash": pwd_hash,
        "salt": salt.hex(),
        "created_at": ts,
        "updated_at": ts,
        "resources": resources or {},
    }

    db["users"].append(record)
    _save_db(db)

    return User(username=username, email=email, role=record["role"], disabled=False)


def set_user_disabled(identifier: str, disabled: bool) -> User:
    ident = _normalize_identifier(identifier)
    if not ident:
        raise ValueError("identifier is required")

    admin = get_configured_user()
    if ident in (_normalize_identifier(admin.username), _normalize_identifier(admin.email)):
        raise ValueError("Cannot disable the admin user")

    db = _load_db()
    idx, rec = _find_user_record(db, ident)
    if idx is None or rec is None:
        raise ValueError("User not found")
    if normalize_role(rec.get("role")) == ROLE_PIPING_ADMIN:
        raise ValueError("Cannot disable piping admin user")

    rec["disabled"] = bool(disabled)
    rec["updated_at"] = int(time.time())
    db["users"][idx] = rec
    _save_db(db)

    return User(
        username=rec.get("username", ""),
        email=rec.get("email", ""),
        role=normalize_role(rec.get("role")),
        disabled=bool(rec.get("disabled", False)),
    )


def reset_user_password(identifier: str, new_password: str) -> User:
    ident = _normalize_identifier(identifier)
    if not ident:
        raise ValueError("identifier is required")
    if not new_password:
        raise ValueError("new_password is required")

    admin = get_configured_user()
    if ident in (_normalize_identifier(admin.username), _normalize_identifier(admin.email)):
        raise ValueError("Cannot reset the admin password here")

    db = _load_db()
    idx, rec = _find_user_record(db, ident)
    if idx is None or rec is None:
        raise ValueError("User not found")
    if normalize_role(rec.get("role")) == ROLE_PIPING_ADMIN:
        raise ValueError("Cannot reset piping admin password here")

    salt = _new_salt()
    rec["salt"] = salt.hex()
    rec["password_hash"] = _hash_password(new_password, salt)
    rec["updated_at"] = int(time.time())
    db["users"][idx] = rec
    _save_db(db)

    return User(
        username=rec.get("username", ""),
        email=rec.get("email", ""),
        role=normalize_role(rec.get("role")),
        disabled=bool(rec.get("disabled", False)),
    )


def set_user_role(identifier: str, role: str) -> User:
    ident = _normalize_identifier(identifier)
    if not ident:
        raise ValueError("identifier is required")

    role = normalize_role(role)

    admin = get_configured_user()
    if ident in (_normalize_identifier(admin.username), _normalize_identifier(admin.email)):
        raise ValueError("Cannot change the admin role")

    db = _load_db()
    idx, rec = _find_user_record(db, ident)
    if idx is None or rec is None:
        raise ValueError("User not found")

    current_role = normalize_role(rec.get("role"))
    if current_role == ROLE_PIPING_ADMIN:
        raise ValueError("Cannot change piping admin role")

    rec["role"] = role
    rec["updated_at"] = int(time.time())
    db["users"][idx] = rec
    _save_db(db)

    return User(
        username=rec.get("username", ""),
        email=rec.get("email", ""),
        role=normalize_role(rec.get("role")),
        disabled=bool(rec.get("disabled", False)),
    )


def delete_user(identifier: str) -> User:
    ident = _normalize_identifier(identifier)
    if not ident:
        raise ValueError("identifier is required")

    admin = get_configured_user()
    if ident in (_normalize_identifier(admin.username), _normalize_identifier(admin.email)):
        raise ValueError("Cannot delete the admin user")

    db = _load_db()
    idx, rec = _find_user_record(db, ident)
    if idx is None or rec is None:
        raise ValueError("User not found")

    if normalize_role(rec.get("role")) == ROLE_PIPING_ADMIN:
        raise ValueError("Cannot delete piping admin user")

    removed = db["users"].pop(idx)
    _save_db(db)

    return User(
        username=removed.get("username", ""),
        email=removed.get("email", ""),
        role=normalize_role(removed.get("role")),
        disabled=bool(removed.get("disabled", False)),
    )


def set_user_resources(identifier: str, resources: Dict[str, Any]) -> User:
    ident = _normalize_identifier(identifier)
    if not ident:
        raise ValueError("identifier is required")

    db = _load_db()
    idx, rec = _find_user_record(db, ident)
    if idx is None or rec is None:
        raise ValueError("User not found")

    rec["resources"] = resources or {}
    rec["updated_at"] = int(time.time())
    db["users"][idx] = rec
    _save_db(db)

    return User(
        username=rec.get("username", ""),
        email=rec.get("email", ""),
        role=normalize_role(rec.get("role")),
        disabled=bool(rec.get("disabled", False)),
    )


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

    if not salt:
        return None

    pwd_hash = _hash_password(password, salt)
    if not hmac.compare_digest(pwd_hash, rec.get("password_hash", "")):
        return None

    return User(
        username=rec.get("username", ""),
        email=rec.get("email", ""),
        role=normalize_role(rec.get("role")),
        disabled=bool(rec.get("disabled", False)),
    )
