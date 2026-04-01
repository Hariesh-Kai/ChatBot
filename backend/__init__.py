"""Chat UI backend package."""

from __future__ import annotations

import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODELS_DIR = _PROJECT_ROOT / "models"
_HF_CACHE_DIR = _MODELS_DIR / "hf_cache"
_GGUF_DIR = _MODELS_DIR / "gguf"
_DOCLING_CACHE_DIR = _HF_CACHE_DIR / "docling"

for path in (_MODELS_DIR, _HF_CACHE_DIR, _GGUF_DIR, _DOCLING_CACHE_DIR):
    path.mkdir(parents=True, exist_ok=True)

# Keep all model and artifact downloads under the repo-local model folders.
os.environ.setdefault("HF_HOME", str(_HF_CACHE_DIR))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(_HF_CACHE_DIR))
os.environ.setdefault("TRANSFORMERS_CACHE", str(_HF_CACHE_DIR))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(_HF_CACHE_DIR))
os.environ.setdefault("DOCLING_CACHE_DIR", str(_DOCLING_CACHE_DIR))
