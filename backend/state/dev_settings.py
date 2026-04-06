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
    # Minimal RAG debug controls (advanced features OFF by default)
    "enable_query_rewrite": False,
    "enable_agent_pipeline": False,
    "enable_hybrid_retrieval": False,
    "enable_learning": False,
    "enable_eval_gate": False,
    "enable_cache": False,
    # Retrieval behavior
    "force_detailed_retrieval": False,
    "disable_retrieval_policy": False,
    "disable_rag_globally": False,
    # Document processing profile
    # Off (default) => prioritize extraction quality over upload speed.
    "enable_fast_document_processing": False,
    # RAG operating profiles
    "rag_ingest_mode": DEFAULT_RAG_MODE,  # fast | balanced | high_fidelity
    "rag_retrieval_mode": DEFAULT_RETRIEVAL_MODE_SETTING,  # auto | fast | balanced | high_fidelity
    "rag_preprocessor": DEFAULT_RAG_PREPROCESSOR,  # unstructured | pypdf_text | pymupdf4llm | docling
    "rag_collection_name": DEFAULT_RAG_COLLECTION_NAME,
    # Legacy key retained for backward compatibility with older UI builds.
    "rag_mode": DEFAULT_RAG_MODE,  # deprecated
}


def _materialize_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    resolved = dict(settings or {})
    fast_enabled = bool(resolved.get("enable_fast_document_processing", False))

    if fast_enabled:
        effective_rag_mode = normalize_rag_mode(DEFAULT_RAG_MODE)
        effective_preprocessor = normalize_rag_preprocessor(DEFAULT_RAG_PREPROCESSOR)
        preview_scope = "auto"
        profile_label = "fast"
    else:
        effective_rag_mode = "high_fidelity"
        effective_preprocessor = "unstructured"
        preview_scope = "full"
        profile_label = "high_accuracy"

    resolved["rag_ingest_mode"] = effective_rag_mode
    resolved["rag_preprocessor"] = effective_preprocessor
    resolved["rag_mode"] = effective_rag_mode
    resolved["document_processing_profile"] = {
        "label": profile_label,
        "fast_enabled": fast_enabled,
        "rag_ingest_mode": effective_rag_mode,
        "rag_preprocessor": effective_preprocessor,
        "preview_scope": preview_scope,
        "table_extraction": True,
        "header_footer_cleanup": "strict" if not fast_enabled else "standard",
        "image_classification": not fast_enabled,
    }
    return resolved


def get_dev_settings() -> Dict[str, Any]:
    with _LOCK:
        return _materialize_settings(_SETTINGS)


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

        return _materialize_settings(_SETTINGS)
