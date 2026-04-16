# backend/llm/model_registry.py

from __future__ import annotations

from typing import Any, Dict, Literal

from backend.llm.model_config_store import load_model_config

ChatMode = Literal["lite", "base", "net"]

_BUILTIN_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "lite": {
        "default": "lite_llama_8b",
        "fallback": "lite_qwen_q4",
        "type": "gguf",
    },
    "base": {
        "default": "base_qwen_7b_q4",
        "cpu_fallback": "base_qwen_3b_q4",
        "type": "gguf",
    },
    "net": {
        "default": "groq",
        "type": "api",
    },
}


MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "lite": dict(_BUILTIN_MODEL_REGISTRY["lite"]),
    "base": dict(_BUILTIN_MODEL_REGISTRY["base"]),
    "net": dict(_BUILTIN_MODEL_REGISTRY["net"]),
}


def reload_model_registry() -> Dict[str, Dict[str, Any]]:
    """
    Apply `models/model_config.json` overrides to the in-process registry.

    This enables runtime switching of which underlying model IDs power
    the fixed chat modes: lite/base/net.
    """
    cfg = load_model_config()
    overrides = cfg.get("model_registry_overrides", {})

    merged: Dict[str, Dict[str, Any]] = {
        "lite": dict(_BUILTIN_MODEL_REGISTRY["lite"]),
        "base": dict(_BUILTIN_MODEL_REGISTRY["base"]),
        "net": dict(_BUILTIN_MODEL_REGISTRY["net"]),
    }

    allowed_keys = {
        "lite": {"default", "fallback"},
        "base": {"default", "cpu_fallback"},
        "net": {"default"},
    }

    if isinstance(overrides, dict):
        for mode, patch in overrides.items():
            if mode not in merged or not isinstance(patch, dict):
                continue
            for k, v in patch.items():
                if k not in allowed_keys.get(mode, set()):
                    continue
                if isinstance(v, str) and v.strip():
                    merged[mode][k] = v.strip()

    # Mutate in place so importers keep seeing updated mapping.
    MODEL_REGISTRY.clear()
    MODEL_REGISTRY.update(merged)
    return MODEL_REGISTRY


try:
    reload_model_registry()
except Exception as _e:
    print(f"[MODEL_REGISTRY] Failed to load model_config.json: {_e}")
