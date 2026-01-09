# backend/llm/net_models.py

"""
Registry for KavinBase Net (External API-based LLMs).

Rules:
- No API calls here
- No secrets hardcoded
- Net-only (no local fallback)
"""

import os
from typing import Literal, Dict, List

from backend.secrets.net_keys import (
    has_net_api_key,
    set_net_api_key,
    get_active_net_provider as _get_active_provider_from_keys,
)

# ============================================================
# TYPES
# ============================================================

NetProvider = Literal["groq", "xai"]

# ============================================================
# NET MODEL REGISTRY (RANKED)
# ============================================================

NET_MODELS: Dict[NetProvider, Dict[str, str]] = {
    "groq": {
        "rank_1": "llama-3.1-8b-instant",
        "rank_2": "llama-3.1-8b-instant",
    },
    "xai": {
        "rank_1": "grok-beta",
        "rank_2": "grok-beta",
    },
}

# ============================================================
# ENV VARS
# ============================================================

NET_PROVIDER_ENV = "KAVIN_NET_PROVIDER"

# ============================================================
# HARD LIMITS
# ============================================================

NET_MAX_TOKENS = 1024
NET_MAX_REQUESTS_PER_MIN = 30
NET_MAX_CONCURRENT_STREAMS = 2

# ============================================================
# PROVIDER RESOLUTION
# ============================================================

def is_valid_net_provider(provider: str) -> bool:
    return provider in NET_MODELS


def get_active_net_provider() -> NetProvider:
    """
    Resolve active Net provider.

    Order:
    1. ENV override
    2. Persisted verified keys
    3. Fail hard
    """

    provider = os.getenv(NET_PROVIDER_ENV)

    if provider:
        if provider not in NET_MODELS:
            raise RuntimeError(
                f"Invalid Net provider '{provider}'. "
                f"Available: {list(NET_MODELS.keys())}"
            )
        return provider  # type: ignore

    # fallback to persisted key state
    return _get_active_provider_from_keys()


# ============================================================
# MODEL RESOLUTION
# ============================================================

def get_net_model(
    provider: NetProvider,
    rank: Literal["rank_1", "rank_2"] = "rank_1",
) -> str:
    if provider not in NET_MODELS:
        raise ValueError(f"Unknown Net provider '{provider}'")

    models = NET_MODELS[provider]

    if rank not in models:
        raise ValueError(f"Invalid rank '{rank}' for provider '{provider}'")

    return models[rank]


def get_ranked_net_models(provider: NetProvider) -> List[str]:
    models = NET_MODELS[provider]
    return [models["rank_1"], models["rank_2"]]


# ============================================================
# RUNTIME ACTIVATION (API USE)
# ============================================================

def activate_net_provider(provider: NetProvider, api_key: str) -> None:
    if provider not in NET_MODELS:
        raise ValueError(f"Cannot activate unknown provider '{provider}'")

    os.environ[NET_PROVIDER_ENV] = provider
    set_net_api_key(provider, api_key)


def resolve_active_net_model() -> str:
    provider = get_active_net_provider()
    return get_net_model(provider, rank="rank_1")
