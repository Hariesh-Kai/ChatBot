# backend/llm/model_selector.py

import os

import torch

from backend.llm.loader import (
    GGUF_MODELS,
    HF_MODELS,
    HF_CACHE_DIR,
    resolve_gguf_model_path,
    sync_model_runtime_if_needed,
)
from backend.llm.hf_cache_utils import resolve_local_snapshot
from backend.llm.model_registry import MODEL_REGISTRY, ChatMode


_SMALL_CPU_GGUF_CANDIDATES = [
    "lite_qwen_3b_q4",
    "lite_qwen_1_5b_q4",
    "lite_qwen_q4",
    "lite_llama_8b",
]

_LOW_MEMORY_AVOID_MODEL_IDS = {
    "lite_qwen_q4",
    "lite_llama_8b",
    "base_qwen_7b",
    "base_qwen_7b_q4",
}


def _is_known_bad_gguf(path: str) -> bool:
    """
    Known incompatible quantization with current llama_cpp runtime.
    """
    return "q4_0_4_8" in os.path.basename(path or "").lower()


def _is_usable_gguf_model(model_id: str) -> bool:
    path = resolve_gguf_model_path(model_id)
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


def _prefer_small_cpu_models() -> bool:
    if torch.cuda.is_available():
        return False

    try:
        import psutil

        total_gib = psutil.virtual_memory().total / (1024 ** 3)
        return total_gib <= 20
    except Exception:
        return True


def _allow_model_for_current_machine(model_id: str, *, prefer_small_cpu: bool) -> bool:
    if not prefer_small_cpu:
        return True
    return model_id not in _LOW_MEMORY_AVOID_MODEL_IDS


def _iter_small_cpu_candidates(*extra_candidates: str) -> list[str]:
    ordered: list[str] = []
    seen = set()

    for model_id in [*_SMALL_CPU_GGUF_CANDIDATES, *extra_candidates]:
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        ordered.append(model_id)

    return ordered


def _resolve_first_available_model(candidates: list[str], *, prefer_small_cpu: bool) -> str | None:
    seen = set()

    for model_id in candidates:
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        if not _allow_model_for_current_machine(model_id, prefer_small_cpu=prefer_small_cpu):
            continue

        if model_id in GGUF_MODELS and _is_usable_gguf_model(model_id):
            return model_id
        if model_id in HF_MODELS and _is_usable_hf_model(model_id):
            return model_id

    return None


def _resolve_first_registered_model(candidates: list[str], *, prefer_small_cpu: bool) -> str | None:
    seen = set()

    for model_id in candidates:
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        if not _allow_model_for_current_machine(model_id, prefer_small_cpu=prefer_small_cpu):
            continue

        if model_id in GGUF_MODELS or model_id in HF_MODELS:
            return model_id

    return None


def resolve_model_id(mode: ChatMode) -> str:
    sync_model_runtime_if_needed()

    if mode not in MODEL_REGISTRY:
        raise ValueError(f"Unknown chat mode: {mode}")

    entry = MODEL_REGISTRY[mode]

    if mode == "base":
        default_id = str(entry.get("default") or "").strip()
        cpu_fallback_id = str(entry.get("cpu_fallback") or "").strip()
        prefer_small_cpu = _prefer_small_cpu_models()

        direct_candidates = (
            [default_id, cpu_fallback_id]
            if torch.cuda.is_available()
            else [cpu_fallback_id, default_id]
        )

        fallback_candidates = [
            model_id
            for model_id in list(HF_MODELS.keys())
            if model_id not in {default_id, cpu_fallback_id}
        ]
        if prefer_small_cpu:
            # Only consider Lite GGUF models after the configured Base options
            # and any other locally cached Base HF models have been checked.
            fallback_candidates.extend(_iter_small_cpu_candidates())

        candidates = [*direct_candidates, *fallback_candidates]

        resolved = _resolve_first_available_model(
            direct_candidates,
            prefer_small_cpu=prefer_small_cpu,
        )
        if resolved:
            return resolved

        resolved = _resolve_first_available_model(
            fallback_candidates,
            prefer_small_cpu=prefer_small_cpu,
        )
        if resolved:
            return resolved

        if prefer_small_cpu and "lite_qwen_1_5b_q4" in GGUF_MODELS:
            return "lite_qwen_1_5b_q4"

        # Last resort: keep legacy behavior and return any registered ID even
        # if local files are missing (caller will emit user-facing error).
        resolved = _resolve_first_registered_model(
            candidates,
            prefer_small_cpu=prefer_small_cpu,
        )
        if resolved:
            return resolved
        return default_id or cpu_fallback_id

    if mode == "lite":
        candidates = []
        prefer_small_cpu = _prefer_small_cpu_models()

        default_id = entry.get("default")
        if isinstance(default_id, str) and default_id:
            if prefer_small_cpu:
                candidates.extend(_iter_small_cpu_candidates(default_id))
            else:
                candidates.append(default_id)

        builtin_ids = {"lite_llama_8b", "lite_qwen_q4", "lite_qwen_1_5b_q4"}

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
        if prefer_small_cpu:
            candidates.extend(_iter_small_cpu_candidates())
        else:
            candidates.extend(["lite_llama_8b", "lite_qwen_q4"])

        # Any remaining registered GGUF IDs.
        candidates.extend(list(GGUF_MODELS.keys()))

        seen = set()
        for model_id in candidates:
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            if not _allow_model_for_current_machine(model_id, prefer_small_cpu=prefer_small_cpu):
                continue

            if _is_usable_gguf_model(model_id):
                return model_id

        # Last resort: prefer the smallest CPU-safe candidate when nothing is
        # installed yet so the user-facing download target matches reality.
        if prefer_small_cpu and "lite_qwen_1_5b_q4" in GGUF_MODELS:
            return "lite_qwen_1_5b_q4"

        # Legacy fallback: return configured default even if not ideal.
        return str(default_id or "lite_qwen_1_5b_q4")

    return entry["default"]
