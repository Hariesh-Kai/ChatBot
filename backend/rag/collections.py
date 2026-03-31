from __future__ import annotations

import os
import re
from typing import Any


DEFAULT_RAG_COLLECTION_NAME = os.getenv("RAG_COLLECTION_NAME", "rag_documents")
_COLLECTION_RE = re.compile(r"[^a-zA-Z0-9_]+")


def normalize_collection_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = DEFAULT_RAG_COLLECTION_NAME

    normalized = _COLLECTION_RE.sub("_", raw).strip("_").lower()
    if not normalized:
        normalized = DEFAULT_RAG_COLLECTION_NAME
    return normalized


def collection_name_for_preprocessor(
    preprocessor: str,
    *,
    base_collection: str | None = None,
) -> str:
    from backend.rag.preprocessor_registry import normalize_rag_preprocessor

    base = normalize_collection_name(base_collection or DEFAULT_RAG_COLLECTION_NAME)
    resolved_preprocessor = normalize_rag_preprocessor(preprocessor)
    return normalize_collection_name(f"{base}__{resolved_preprocessor}")
