# backend/llm/model_selector.py

import os

import torch

from backend.llm.loader import GGUF_MODELS, HF_MODELS, HF_CACHE_DIR
from backend.llm.hf_cache_utils import resolve_local_snapshot
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


def _is_usable_hf_model(model_id: str) -> bool:
    repo_or_path = (HF_MODELS.get(model_id) or "").strip()
    if not repo_or_path:
        return False

    # Support direct local path registrations.
    if os.path.exists(repo_or_path):
        return True

    # Repo-id registrations must resolve to an existing local HF snapshot.
    return bool(resolve_local_snapshot(HF_CACHE_DIR, repo_or_path))


def resolve_model_id(mode: ChatMode) -> str:
    if mode not in MODEL_REGISTRY:
        raise ValueError(f"Unknown chat mode: {mode}")

    entry = MODEL_REGISTRY[mode]

    if mode == "base":
        default_id = str(entry.get("default") or "").strip()
        cpu_fallback_id = str(entry.get("cpu_fallback") or "").strip()

        # On CPU, prefer cpu_fallback first, but fall back to default if that
        # fallback is not actually present in local HF cache.
        candidates = (
            [default_id, cpu_fallback_id]
            if torch.cuda.is_available()
            else [cpu_fallback_id, default_id]
        )
        candidates.extend(list(HF_MODELS.keys()))

        seen = set()
        for model_id in candidates:
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            if model_id in HF_MODELS and _is_usable_hf_model(model_id):
                return model_id

        # Last resort: keep legacy behavior and return any registered ID even
        # if local files are missing (caller will emit user-facing error).
        for model_id in candidates:
            if model_id in HF_MODELS:
                return model_id
        return default_id or cpu_fallback_id

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
