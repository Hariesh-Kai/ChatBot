# backend/llm/model_selector.py

import torch
from backend.llm.model_registry import MODEL_REGISTRY, ChatMode

def resolve_model_id(mode: ChatMode) -> str:
    if mode not in MODEL_REGISTRY:
        raise ValueError(f"Unknown chat mode: {mode}")

    entry = MODEL_REGISTRY[mode]

    if mode == "base":
        if torch.cuda.is_available():
            return entry["default"]
        return entry["cpu_fallback"]

    return entry["default"]
