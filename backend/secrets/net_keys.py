# backend/secrets/net_keys.py

"""
Net API key management (persistent, backend-only).

Security model:
- Keys are NEVER sent to frontend
- Keys are stored locally in a restricted file (chmod 600)
- No encryption is claimed (OS file permissions only)
- Keys are loaded once at startup
- Net provider selection is explicit
"""
import os
from typing import Dict, Literal
import json
from threading import Lock
from pathlib import Path

# ============================================================
# TYPES
# ============================================================

NetProvider = Literal["groq", "xai"]

# ============================================================
# ENV VAR (USED BY net_models.py)
# ============================================================

NET_PROVIDER_ENV = "KAVIN_NET_PROVIDER"

# ============================================================
# STORAGE (LOCAL, BACKEND-ONLY)
# ============================================================

SECRET_DIR = Path.home() / ".kavinbase"
SECRET_FILE = SECRET_DIR / "net_keys.json"

_SECRET_DIR_CREATED = False

def _ensure_secret_dir():
    global _SECRET_DIR_CREATED
    if not _SECRET_DIR_CREATED:
        SECRET_DIR.mkdir(parents=True, exist_ok=True)
        _SECRET_DIR_CREATED = True

# ============================================================
# IN-MEMORY STATE
# ============================================================

_NET_KEYS: Dict[NetProvider, str] = {}
_LOCK = Lock()

# ============================================================
# INTERNAL LOAD / SAVE
# ============================================================

def _load_from_disk():
    if not SECRET_FILE.exists():
        return

    try:
        data = json.loads(SECRET_FILE.read_text())
        if not isinstance(data, dict):
            raise ValueError("Key file is not a dict")

        for k, v in data.items():
            if k in ("groq", "xai") and isinstance(v, str):
                _NET_KEYS[k] = v

    except Exception as e:
        print(f"⚠️ Corrupted net_keys.json ignored: {e}")
        try:
            SECRET_FILE.unlink()
        except Exception:
            pass

def _save_to_disk():
    _ensure_secret_dir()

    tmp_file = SECRET_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(_NET_KEYS, indent=2))

    tmp_file.replace(SECRET_FILE)

    # 🔐 Restrict permissions: owner read/write only
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception as e:
        print(f"⚠️ Failed to chmod net_keys.json: {e}")

# Load once at import
_load_from_disk()

# ============================================================
# PUBLIC API
# ============================================================

def set_net_api_key(provider: NetProvider, api_key: str) -> None:
    if not api_key or not api_key.strip():
        raise ValueError("API key cannot be empty")

    with _LOCK:
        _NET_KEYS[provider] = api_key.strip()
        _save_to_disk()

        # Provider selection is explicit
        os.environ[NET_PROVIDER_ENV] = provider

def has_net_api_key(provider: NetProvider) -> bool:
    with _LOCK:
        return provider in _NET_KEYS

def get_net_api_key(provider: NetProvider) -> str:
    with _LOCK:
        key = _NET_KEYS.get(provider)

    if not key:
        raise RuntimeError(f"No API key for provider '{provider}'")

    return key

def clear_net_api_keys() -> None:
    with _LOCK:
        _NET_KEYS.clear()

        if SECRET_FILE.exists():
            try:
                print("⚠️ Corrupted net_keys.json preserved for inspection")
            except Exception:
                pass

        os.environ.pop(NET_PROVIDER_ENV, None)

def get_active_net_provider() -> NetProvider:
    provider = os.getenv(NET_PROVIDER_ENV)

    if provider in ("groq", "xai"):
        return provider  # type: ignore

    raise RuntimeError("Net provider not configured")
