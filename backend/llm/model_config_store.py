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
MODEL_CONFIG_PATH = _PROJECT_ROOT / "models" / "model_config.json"


def _default_config() -> Dict[str, Any]:
    return {
        "hf_models": {},  # {model_id: repo_id}
        "gguf_models": {},  # {model_id: absolute_or_relative_path}
        "model_registry_overrides": {},  # {mode: {key: model_id}}
    }


def load_model_config() -> Dict[str, Any]:
    with _LOCK:
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
        MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
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

