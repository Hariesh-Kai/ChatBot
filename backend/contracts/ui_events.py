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

def request_metadata_event(fields: List[str]) -> Dict[str, Any]:
    """
    Request missing metadata fields from the user.

    Example input:
        ["revision_code", "approved_by"]
    """

    safe_fields = fields or []

    return _base_event(
        "REQUEST_METADATA",
        {
            "fields": [
                {
                    "key": field,
                    "label": _humanize(field),
                    "placeholder": f"Enter {_humanize(field)}",
                    "reason": "Missing or low confidence",
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

def _humanize(key: str) -> str:
    """
    Convert snake_case keys into human-readable labels.

    Example:
        revision_code -> Revision Code
    """
    return key.replace("_", " ").strip().title()


# ==========================================================
# PUBLIC EXPORTS (CONTRACT GUARANTEE)
# ==========================================================

__all__ = [
    "system_message_event",
    "request_metadata_event",
    "metadata_confirmed_event",
    "progress_event",
    "answer_confidence_event",
    "error_event",
]
