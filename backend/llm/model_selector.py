# backend/llm/model_selector.py

import os

import torch

from backend.llm.loader import GGUF_MODELS, HF_MODELS
from backend.llm.model_registry import MODEL_REGISTRY, ChatMode


def _is_known_bad_gguf(path: str) -> bool:
    """
    Known incompatible quantization with current llama_cpp runtime.
    """
    return "q4_0_4_8" in os.path.basename(path or "").lower()


def _is_usable_gguf_model(model_id: str) -> bool:
    path = GGUF_MODELS.get(model_id)
    if not path:
        return False
    if _is_known_bad_gguf(path):
        return False
    return os.path.exists(path)


def resolve_model_id(mode: ChatMode) -> str:
    if mode not in MODEL_REGISTRY:
        raise ValueError(f"Unknown chat mode: {mode}")

    entry = MODEL_REGISTRY[mode]

    if mode == "base":
        preferred = entry["default"] if torch.cuda.is_available() else entry["cpu_fallback"]
        if preferred in HF_MODELS:
            return preferred

        fallback = entry.get("cpu_fallback")
        if isinstance(fallback, str) and fallback in HF_MODELS:
            return fallback

        if HF_MODELS:
            return next(iter(HF_MODELS.keys()))
        return preferred

    if mode == "lite":
        candidates = []

        default_id = entry.get("default")
        if isinstance(default_id, str) and default_id:
            candidates.append(default_id)

        builtin_ids = {"lite_llama_8b", "lite_qwen_q4"}

        # Prefer any registered custom GGUF (often the dashboard-installed model)
        # before falling back to built-in defaults.
        custom_candidates = [
            mid
            for mid in GGUF_MODELS.keys()
            if mid not in builtin_ids and mid != default_id
        ]
        candidates.extend(custom_candidates)

        fallback_id = entry.get("fallback")
        if isinstance(fallback_id, str) and fallback_id:
            candidates.append(fallback_id)

        # Built-in safe priorities.
        candidates.extend(["lite_llama_8b", "lite_qwen_q4"])

        # Any remaining registered GGUF IDs.
        candidates.extend(list(GGUF_MODELS.keys()))

        seen = set()
        for model_id in candidates:
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)

            if _is_usable_gguf_model(model_id):
                return model_id

        # Last resort: return configured default even if not ideal.
        return str(default_id or "lite_llama_8b")

    return entry["default"]
