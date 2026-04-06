# backend/llm/model_config_store.py

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Tuple

"""
Persistent model configuration for developer-controlled model management.

Stored under `models/model_config.json` (ignored by git via `models/` ignore).

This file allows the Developer Dashboard to:
- register new HF / GGUF models
- override which model IDs power the `lite` / `base` / `net` modes

Notes:
- This is intended for local/dev usage.
- We keep validation strict to avoid breaking the backend at runtime.
"""

_LOCK = Lock()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MODELS_DIR = _PROJECT_ROOT / "models"
HF_CACHE_DIR = _MODELS_DIR / "hf_cache"
GGUF_DIR = _MODELS_DIR / "gguf"
MODEL_CONFIG_PATH = _MODELS_DIR / "model_config.json"


def _default_config() -> Dict[str, Any]:
    return {
        "hf_models": {},  # {model_id: repo_id}
        "gguf_models": {},  # {model_id: absolute_or_relative_path}
        "model_registry_overrides": {},  # {mode: {key: model_id}}
    }


def ensure_model_paths() -> None:
    """
    Ensure model directories and config file exist.
    """
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    GGUF_DIR.mkdir(parents=True, exist_ok=True)
    if not MODEL_CONFIG_PATH.exists():
        MODEL_CONFIG_PATH.write_text(
            json.dumps(_default_config(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def get_model_config_fingerprint() -> Tuple[int, int]:
    """
    Return a stable fingerprint for the persisted model config file.

    The backend uses this to auto-reload model registrations when an external
    script updates `models/model_config.json`.
    """
    ensure_model_paths()
    try:
        stat = MODEL_CONFIG_PATH.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except Exception:
        return 0, 0


def load_model_config() -> Dict[str, Any]:
    with _LOCK:
        ensure_model_paths()
        if not MODEL_CONFIG_PATH.exists():
            return _default_config()

        try:
            data = json.loads(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            # corrupted file -> start fresh rather than crash the server
            return _default_config()

        if not isinstance(data, dict):
            return _default_config()

        cfg = _default_config()
        for k in cfg.keys():
            if isinstance(data.get(k), dict):
                cfg[k] = dict(data[k])
        return cfg


def save_model_config(cfg: Dict[str, Any]) -> None:
    with _LOCK:
        ensure_model_paths()
        MODEL_CONFIG_PATH.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def upsert_hf_model(*, model_id: str, repo_id: str) -> Dict[str, Any]:
    model_id = (model_id or "").strip()
    repo_id = (repo_id or "").strip()
    if not model_id:
        raise ValueError("model_id is required")
    if not repo_id:
        raise ValueError("repo_id is required")

    cfg = load_model_config()
    cfg["hf_models"][model_id] = repo_id
    save_model_config(cfg)
    return cfg


def upsert_gguf_model(*, model_id: str, path: str) -> Dict[str, Any]:
    model_id = (model_id or "").strip()
    path = (path or "").strip()
    if not model_id:
        raise ValueError("model_id is required")
    if not path:
        raise ValueError("path is required")

    cfg = load_model_config()
    cfg["gguf_models"][model_id] = path
    save_model_config(cfg)
    return cfg


def patch_model_registry_overrides(patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Patch format:
      {
        "lite": {"default": "lite_llama_8b"},
        "base": {"default": "base_qwen_7b", "cpu_fallback": "base_qwen_3b"}
      }
    """
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch must be a non-empty object")

    cfg = load_model_config()
    overrides = cfg.get("model_registry_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}

    for mode, mode_patch in patch.items():
        if not isinstance(mode, str) or not mode:
            raise ValueError("Invalid mode key")
        if not isinstance(mode_patch, dict) or not mode_patch:
            raise ValueError(f"Invalid patch for mode '{mode}'")

        existing = overrides.get(mode, {})
        if not isinstance(existing, dict):
            existing = {}

        for k, v in mode_patch.items():
            if not isinstance(k, str) or not k:
                raise ValueError(f"Invalid registry key for mode '{mode}'")
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"Invalid model_id for mode '{mode}.{k}'")
            existing[k] = v.strip()

        overrides[mode] = existing

    cfg["model_registry_overrides"] = overrides
    save_model_config(cfg)
    return cfg


def delete_model(model_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Remove a model_id from HF/GGUF registries and any registry overrides.

    Returns:
      (updated_config, info)
    """
    model_id = (model_id or "").strip()
    if not model_id:
        raise ValueError("model_id is required")

    cfg = load_model_config()
    info = {
        "removed_hf": False,
        "removed_gguf": False,
        "removed_overrides": [],
    }

    if model_id in cfg.get("hf_models", {}):
        cfg["hf_models"].pop(model_id, None)
        info["removed_hf"] = True

    if model_id in cfg.get("gguf_models", {}):
        cfg["gguf_models"].pop(model_id, None)
        info["removed_gguf"] = True

    overrides = cfg.get("model_registry_overrides", {})
    if isinstance(overrides, dict):
        for mode, patch in list(overrides.items()):
            if not isinstance(patch, dict):
                continue
            removed_keys = [k for k, v in patch.items() if v == model_id]
            if removed_keys:
                for k in removed_keys:
                    patch.pop(k, None)
                info["removed_overrides"].append({"mode": mode, "keys": removed_keys})
            if not patch:
                overrides.pop(mode, None)

    cfg["model_registry_overrides"] = overrides
    save_model_config(cfg)
    return cfg, info
