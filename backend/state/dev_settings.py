# backend/state/dev_settings.py

from __future__ import annotations

from threading import Lock
from typing import Any, Dict

from backend.rag.mode_profiles import (
    DEFAULT_RAG_MODE,
    DEFAULT_RETRIEVAL_MODE_SETTING,
    normalize_rag_mode,
    normalize_retrieval_mode_setting,
)
from backend.rag.preprocessor_registry import (
    DEFAULT_RAG_PREPROCESSOR,
    normalize_rag_preprocessor,
)
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)

"""
In-memory developer settings (feature flags).

These settings are meant for local/dev usage and can be toggled from the
frontend Developer Dashboard.

Notes:
- Process-local: resets on backend restart.
- Intentionally small surface area: only allow known keys.
"""

_LOCK = Lock()

_SETTINGS: Dict[str, Any] = {
    # Chat stream UI events
    "emit_model_stage_events": True,
    "emit_sources": True,
    "emit_answer_confidence": True,
    # Retrieval behavior
    "force_detailed_retrieval": False,
    "disable_retrieval_policy": False,
    "disable_rag_globally": False,
    # RAG operating profiles
    "rag_ingest_mode": DEFAULT_RAG_MODE,  # fast | balanced | high_fidelity
    "rag_retrieval_mode": DEFAULT_RETRIEVAL_MODE_SETTING,  # auto | fast | balanced | high_fidelity
    "rag_preprocessor": DEFAULT_RAG_PREPROCESSOR,  # unstructured | pypdf_text | pymupdf4llm | docling
    "rag_collection_name": DEFAULT_RAG_COLLECTION_NAME,
    # Legacy key retained for backward compatibility with older UI builds.
    "rag_mode": DEFAULT_RAG_MODE,  # deprecated
}


def get_dev_settings() -> Dict[str, Any]:
    with _LOCK:
        return dict(_SETTINGS)


def update_dev_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        for key, value in (patch or {}).items():
            if key not in _SETTINGS:
                raise KeyError(f"Unknown setting: {key}")

            current = _SETTINGS[key]

            # Supported setting types: boolean and string enums.
            if isinstance(current, bool):
                if not isinstance(value, bool):
                    raise TypeError(f"Setting '{key}' must be boolean")
                _SETTINGS[key] = value
            elif isinstance(current, str):
                if key == "rag_ingest_mode":
                    _SETTINGS[key] = normalize_rag_mode(value)
                elif key == "rag_retrieval_mode":
                    _SETTINGS[key] = normalize_retrieval_mode_setting(value)
                elif key == "rag_preprocessor":
                    _SETTINGS[key] = normalize_rag_preprocessor(value)
                elif key == "rag_collection_name":
                    _SETTINGS[key] = normalize_collection_name(value)
                elif key == "rag_mode":
                    # Legacy compatibility path:
                    # update both new keys when old key is patched.
                    normalized = normalize_rag_mode(value)
                    _SETTINGS[key] = normalized
                    _SETTINGS["rag_ingest_mode"] = normalized
                    _SETTINGS["rag_retrieval_mode"] = normalized
                else:
                    if not isinstance(value, str):
                        raise TypeError(f"Setting '{key}' must be string")
                    _SETTINGS[key] = value
            else:
                _SETTINGS[key] = value

        return dict(_SETTINGS)
