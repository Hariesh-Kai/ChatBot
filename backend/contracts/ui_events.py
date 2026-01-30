"""
UI EVENT CONTRACTS (BACKEND)

This module defines ALL UI events that the backend
is allowed to emit to the frontend.

Frontend must react ONLY to these events.
"""

from typing import List, Dict, Any, Optional


# ==========================================================
# INTERNAL BASE EVENT
# ==========================================================

def _base_event(
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Internal helper to create a UI event.

    Guarantees:
    - Always has a 'type'
    - Payload is always a dict (if present)
    """
    event: Dict[str, Any] = {"type": event_type}
    if payload is not None:
        event.update(payload)
    return event


# ==========================================================
# SYSTEM MESSAGE
# ==========================================================

def system_message_event(text: str) -> Dict[str, Any]:
    """
    Informational message shown inside chat.
    Rendered as a system message bubble.
    """
    return _base_event(
        "SYSTEM_MESSAGE",
        {
            "text": str(text),
        },
    )


# ==========================================================
# METADATA REQUEST
# ==========================================================

def request_metadata_event(fields) -> Dict[str, Any]:
    """
    Accepts:
    - List[str]
    - List[Dict] (frontend structured metadata fields)
    """

    safe_fields = fields or []

    return _base_event(
        "REQUEST_METADATA",
        {
            "fields": [
                {
                    "key": field["key"] if isinstance(field, dict) else field,
                    "label": (
                                field.get("label")
                                if isinstance(field, dict) and field.get("label")
                                else _humanize(field)
                            ),
                    "placeholder": (
                        field.get("placeholder")
                        if isinstance(field, dict)
                        else f"Enter {_humanize(field)}"
                    ),
                    "reason": (
                        field.get("reason")
                        if isinstance(field, dict)
                        else "Missing or low confidence"
                    ),
                }
                for field in safe_fields
            ],
        },
    )


# ==========================================================
# METADATA CONFIRMED
# ==========================================================

def metadata_confirmed_event(
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sent after metadata is successfully updated.
    """
    return _base_event(
        "METADATA_CONFIRMED",
        {
            "message": message or "Metadata updated successfully.",
        },
    )


# ==========================================================
# PROGRESS EVENT (LONG JOBS ONLY)
# ==========================================================

def progress_event(
    value: int,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Determinate progress update (0–100).

    Used ONLY for:
    - PDF upload
    - Chunking
    - Embedding
    - Long-running jobs
    """

    try:
        safe_value = int(value)
    except Exception:
        safe_value = 0

    safe_value = max(0, min(100, safe_value))

    return _base_event(
        "PROGRESS",
        {
            "value": safe_value,
            "label": label,
        },
    )


# ==========================================================
# 🟩 ANSWER CONFIDENCE (STEP 6.2)
# ==========================================================

def answer_confidence_event(
    confidence: float,
    level: str,
) -> Dict[str, Any]:
    """
    Confidence indicator for the generated answer.

    - confidence: float between 0.0 and 1.0
    - level: "high" | "medium" | "low"

    Emitted AFTER answer generation.
    Rendered as a badge / indicator in UI.
    """

    try:
        safe_confidence = float(confidence)
    except Exception:
        safe_confidence = 0.0

    safe_confidence = max(0.0, min(1.0, safe_confidence))

    safe_level = level if level in ("high", "medium", "low") else "low"

    return _base_event(
        "ANSWER_CONFIDENCE",
        {
            "confidence": round(safe_confidence, 2),
            "level": safe_level,
        },
    )


# ==========================================================
# 🟦 MODEL STAGE EVENT (LIVE PIPELINE STATE)
# ==========================================================

def model_stage_event(
    *,
    stage: str,
    message: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Live pipeline stage update for frontend animation.

    Examples:
    - stage="intent"
    - stage="retrieval"
    - stage="reranking"
    - stage="generation"

    Emitted DURING answer generation.
    """

    safe_stage = str(stage).lower().strip()

    return _base_event(
        "MODEL_STAGE",
        {
            "stage": safe_stage,
            "message": message,
            "model": model,
        },
    )



# ==========================================================
# ERROR EVENT
# ==========================================================

def error_event(message: str) -> Dict[str, Any]:
    """
    Pipeline / metadata / job error.

    Frontend will:
    - stop typing indicator
    - render error state
    """
    return _base_event(
        "ERROR",
        {
            "message": str(message),
        },
    )


# ==========================================================
# HELPERS
# ==========================================================

def _humanize(field) -> str:
    """
    Accepts:
    - string keys: "revision_number"
    - dict fields: { key, label, value, ... }
    """
    if isinstance(field, dict):
        key = field.get("label") or field.get("key") or ""
    else:
        key = str(field)

    return key.replace("_", " ").strip().title()

# ==========================================================
# 🟥 NET RATE LIMITED EVENT
# ==========================================================

def net_rate_limited_event(
    *,
    retry_after_sec: int,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Emitted when Net / Cloud model is rate-limited.

    Frontend will:
    - block Net model temporarily
    - show retry timer
    - open NetKeyModal if needed
    """
    try:
        retry_after_sec = int(retry_after_sec)
    except Exception:
        retry_after_sec = 30

    retry_after_sec = max(1, retry_after_sec)

    return _base_event(
        "NET_RATE_LIMITED",
        {
            "retryAfterSec": retry_after_sec,
            "provider": provider,
        },
    )

def text_event(text: str) -> Dict[str, Any]:
    return {
        "type": "TEXT",
        "content": text,
    }

# ==========================================================
# PUBLIC EXPORTS (CONTRACT GUARANTEE)
# ==========================================================

__all__ = [
    "system_message_event",
    "request_metadata_event",
    "metadata_confirmed_event",
    "progress_event",
    "model_stage_event",
    "net_rate_limited_event",   #  ADD
    "answer_confidence_event",
    "error_event",
    "text_event",
]
