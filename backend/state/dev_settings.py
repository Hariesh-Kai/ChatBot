# backend/state/dev_settings.py

from __future__ import annotations

from threading import Lock
from typing import Any, Dict

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

            # Currently we only support boolean flags.
            if isinstance(current, bool):
                if not isinstance(value, bool):
                    raise TypeError(f"Setting '{key}' must be boolean")
                _SETTINGS[key] = value
            else:
                _SETTINGS[key] = value

        return dict(_SETTINGS)
