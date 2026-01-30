# backend/llm/model_registry.py

from typing import Literal

ChatMode = Literal["lite", "base", "net"]

MODEL_REGISTRY = {
    "lite": {
        "default": "lite_llama_8b",
        "fallback": "lite_qwen_q4",
        "type": "gguf",
    },
    "base": {
        "default": "base_qwen_7b",
        "cpu_fallback": "base_qwen_3b",
        "type": "hf",
    },
    "net": {
        "default": "groq",
        "type": "api",
    },
}
