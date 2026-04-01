from __future__ import annotations

from typing import Any, Dict, Literal

RagMode = Literal["fast", "balanced", "high_fidelity"]
RetrievalModeSetting = Literal["auto", "fast", "balanced", "high_fidelity"]
IntentLabel = Literal[
    "summary",
    "summarize",
    "compare",
    "comparison",
    "factual",
    "fact_lookup",
    "lookup",
    "definition",
    "reasoning",
    "greeting",
    "conversation",
    "confirmation",
    "follow_up",
]

DEFAULT_RAG_MODE: RagMode = "balanced"
DEFAULT_RETRIEVAL_MODE_SETTING: RetrievalModeSetting = "auto"
ALLOWED_RAG_MODES = {"fast", "balanced", "high_fidelity"}
ALLOWED_RETRIEVAL_MODE_SETTINGS = {"auto", "fast", "balanced", "high_fidelity"}


def normalize_rag_mode(value: Any) -> RagMode:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_RAG_MODES:
        return raw  # type: ignore[return-value]
    return DEFAULT_RAG_MODE


def normalize_retrieval_mode_setting(value: Any) -> RetrievalModeSetting:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_RETRIEVAL_MODE_SETTINGS:
        return raw  # type: ignore[return-value]
    return DEFAULT_RETRIEVAL_MODE_SETTING


def resolve_effective_retrieval_mode(
    retrieval_mode_setting: Any,
    intent: Any,
) -> RagMode:
    """
    Manual override + auto-routing:
    - explicit fast/balanced/high_fidelity => use directly
    - auto => route by query intent
    """
    mode = normalize_retrieval_mode_setting(retrieval_mode_setting)
    if mode != "auto":
        return normalize_rag_mode(mode)

    normalized_intent = str(intent or "").strip().lower()
    if normalized_intent in {"summary", "summarize", "compare", "comparison", "reasoning"}:
        return "high_fidelity"
    if normalized_intent in {
        "factual",
        "fact_lookup",
        "lookup",
        "definition",
        "greeting",
        "conversation",
        "confirmation",
    }:
        return "fast"
    if normalized_intent == "follow_up":
        return "balanced"
    return "balanced"


def get_preprocess_profile(
    rag_mode: RagMode,
    *,
    pipeline_mode: str,
) -> Dict[str, Any]:
    """
    Controls OCR/table/image extraction behavior per RAG mode.

    pipeline_mode:
    - metadata: page-1 preview path
    - commit: full ingest path
    """
    is_preview = str(pipeline_mode or "").lower() == "metadata"

    # Keep metadata preview lightweight even in high_fidelity mode.
    if is_preview:
        return {
            "strategy": "fast",
            "infer_table_structure": False,
            "extract_images_in_pdf": False,
            "extract_image_block_types": [],
            "prefer_quantized_hi_res": True,
        }

    if rag_mode == "fast":
        return {
            "strategy": "fast",
            "infer_table_structure": False,
            "extract_images_in_pdf": False,
            "extract_image_block_types": [],
            "prefer_quantized_hi_res": True,
        }

    if rag_mode == "high_fidelity":
        return {
            "strategy": "hi_res",
            "infer_table_structure": True,
            "extract_images_in_pdf": True,
            "extract_image_block_types": ["Image", "Table"],
            "prefer_quantized_hi_res": False,
        }

    # balanced (default)
    return {
        "strategy": "hi_res",
        "infer_table_structure": True,
        "extract_images_in_pdf": False,
        "extract_image_block_types": [],
        "prefer_quantized_hi_res": True,
    }


def get_retrieval_profile(
    rag_mode: RagMode,
    *,
    force_detailed: bool,
) -> Dict[str, Any]:
    """
    Controls retrieval depth vs latency per RAG mode.
    """
    profile: Dict[str, Any]

    if rag_mode == "fast":
        profile = {
            "candidate_k": 14,
            "final_k": 6,
            "keyword_limit": 0,
            "use_keyword": False,
            "use_rerank": False,
            "use_parent_resolution": False,
            "rrf_k": 80,
        }
    elif rag_mode == "high_fidelity":
        profile = {
            "candidate_k": 40,
            "final_k": 10,
            "keyword_limit": 18,
            "use_keyword": True,
            "use_rerank": True,
            "use_parent_resolution": True,
            "rrf_k": 50,
        }
    else:
        # balanced
        profile = {
            "candidate_k": 25,
            "final_k": 8,
            "keyword_limit": 12,
            "use_keyword": True,
            "use_rerank": True,
            "use_parent_resolution": True,
            "rrf_k": 60,
        }

    if force_detailed:
        profile["candidate_k"] = int(profile["candidate_k"]) + 10
        profile["final_k"] = int(profile["final_k"]) + 2
        profile["keyword_limit"] = int(profile["keyword_limit"]) + 6

    return profile
