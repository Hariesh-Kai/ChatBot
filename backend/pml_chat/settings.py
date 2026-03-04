from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(minimum, value)


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class PMLLLMSettings:
    base_url: str
    model: str
    api_key: str
    timeout_sec: int
    temperature: float
    max_tokens: int
    verify_tls: bool

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


@lru_cache(maxsize=1)
def get_pml_settings() -> PMLLLMSettings:
    return PMLLLMSettings(
        base_url=os.getenv("PML_LLM_BASE_URL", "").strip(),
        model=os.getenv("PML_LLM_MODEL", "").strip(),
        api_key=os.getenv("PML_LLM_API_KEY", "").strip(),
        timeout_sec=_read_int("PML_LLM_TIMEOUT_SEC", default=120, minimum=5),
        temperature=_read_float("PML_LLM_TEMPERATURE", default=0.1, minimum=0.0, maximum=1.5),
        max_tokens=_read_int("PML_LLM_MAX_TOKENS", default=1024, minimum=64),
        verify_tls=_read_bool("PML_LLM_VERIFY_TLS", default=True),
    )


def reload_pml_settings() -> PMLLLMSettings:
    get_pml_settings.cache_clear()
    return get_pml_settings()

