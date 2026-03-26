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

NET_PROVIDER_ENV = "CHAT_UI_NET_PROVIDER"

# ============================================================
# STORAGE (LOCAL, BACKEND-ONLY)
# ============================================================

SECRET_DIR = Path.home() / ".chat_ui_base"
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
_ACTIVE_PROVIDER: NetProvider | None = None
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

        global _ACTIVE_PROVIDER

        active = data.get("active_provider") or data.get("_active_provider")
        if active in ("groq", "xai"):
            _ACTIVE_PROVIDER = active  # type: ignore

        for k, v in data.items():
            if k in ("groq", "xai") and isinstance(v, str):
                _NET_KEYS[k] = v

        # Auto-select provider if only one key exists and none was set
        if _ACTIVE_PROVIDER is None and len(_NET_KEYS) == 1:
            _ACTIVE_PROVIDER = next(iter(_NET_KEYS.keys()))

        if not os.getenv(NET_PROVIDER_ENV) and _ACTIVE_PROVIDER in _NET_KEYS:
            os.environ[NET_PROVIDER_ENV] = _ACTIVE_PROVIDER  # type: ignore

    except Exception as e:
        print(f"Corrupted net_keys.json ignored: {e}")
        try:
            SECRET_FILE.unlink()
        except Exception:
            pass

def _save_to_disk():
    _ensure_secret_dir()

    tmp_file = SECRET_FILE.with_suffix(".tmp")
    payload = dict(_NET_KEYS)
    if _ACTIVE_PROVIDER in ("groq", "xai"):
        payload["active_provider"] = _ACTIVE_PROVIDER
    tmp_file.write_text(json.dumps(payload, indent=2))

    tmp_file.replace(SECRET_FILE)

    # 🔐 Restrict permissions: owner read/write only
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception as e:
        print(f"Failed to chmod net_keys.json: {e}")

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
        global _ACTIVE_PROVIDER
        _ACTIVE_PROVIDER = provider
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
        global _ACTIVE_PROVIDER
        _ACTIVE_PROVIDER = None

        if SECRET_FILE.exists():
            try:
                print("Corrupted net_keys.json preserved for inspection")
            except Exception:
                pass

        os.environ.pop(NET_PROVIDER_ENV, None)

def get_active_net_provider() -> NetProvider:
    provider = os.getenv(NET_PROVIDER_ENV)

    if provider in ("groq", "xai"):
        return provider  # type: ignore

    with _LOCK:
        if _ACTIVE_PROVIDER in ("groq", "xai") and _ACTIVE_PROVIDER in _NET_KEYS:
            return _ACTIVE_PROVIDER
        if len(_NET_KEYS) == 1:
            return next(iter(_NET_KEYS.keys()))

    raise RuntimeError("Net provider not configured")
