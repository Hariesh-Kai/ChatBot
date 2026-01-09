"""
net_loader.py

External LLM loader for KavinBase Net.

GUARANTEES:
- Streaming-safe (FastAPI compatible)
- Yields ONLY strings
- Explicit provider errors
- Correct rate + concurrency accounting
"""

import time
import threading
import json
from typing import Generator, Optional

import requests

from backend.llm.net_models import (
    get_active_net_provider,
    get_net_model,
    NET_MAX_TOKENS,
    NET_MAX_REQUESTS_PER_MIN,
    NET_MAX_CONCURRENT_STREAMS,
)

from backend.secrets.net_keys import get_net_api_key


# ============================================================
# GLOBAL STATE (RATE / CONCURRENCY)
# ============================================================

_request_timestamps: list[float] = []
_active_streams: int = 0
_lock = threading.Lock()


# ============================================================
# ERRORS
# ============================================================

class NetUsageError(Exception):
    pass


class NetAuthError(Exception):
    pass


class NetRateLimitError(Exception):
    pass


class NetProviderError(Exception):
    pass


# ============================================================
# LIMIT ENFORCEMENT
# ============================================================

def _acquire_stream_slot() -> None:
    global _active_streams
    now = time.time()

    with _lock:
        one_min_ago = now - 60
        while _request_timestamps and _request_timestamps[0] < one_min_ago:
            _request_timestamps.pop(0)

        if len(_request_timestamps) >= NET_MAX_REQUESTS_PER_MIN:
            raise NetRateLimitError("KavinBase Net RPM limit exceeded")

        if _active_streams >= NET_MAX_CONCURRENT_STREAMS:
            raise NetRateLimitError("Too many concurrent Net streams")

        _request_timestamps.append(now)
        _active_streams += 1


def _release_stream_slot() -> None:
    global _active_streams
    with _lock:
        _active_streams = max(0, _active_streams - 1)


# ============================================================
# GROQ STREAM
# ============================================================

def _groq_stream(
    prompt: str,
    model: str,
    max_tokens: int,
) -> Generator[str, None, None]:

    api_key = get_net_api_key("groq")
    if not api_key:
        raise NetAuthError("Groq API key missing")

    url = "https://api.groq.com/openai/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        stream=True,
        timeout=60,
    )

    if response.status_code == 401:
        response.close()
        raise NetAuthError("Invalid Groq API key")

    if response.status_code == 429:
        response.close()
        raise NetRateLimitError("Groq quota exceeded")

    if response.status_code >= 400:
        text = response.text
        response.close()
        raise NetProviderError(f"Groq API error [{response.status_code}]: {text}")

    try:
        for raw in response.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue

            data = raw[5:].strip()
            if data == "[DONE]":
                break

            try:
                chunk = json.loads(data)
            except Exception:
                continue

            delta = (
                chunk.get("choices", [{}])[0]
                .get("delta", {})
                .get("content")
            )

            if isinstance(delta, str) and delta:
                yield delta

    finally:
        response.close()


# ============================================================
# XAI STREAM
# ============================================================

def _xai_stream(
    prompt: str,
    model: str,
    max_tokens: int,
) -> Generator[str, None, None]:

    api_key = get_net_api_key("xai")
    if not api_key:
        raise NetAuthError("xAI API key missing")

    url = "https://api.x.ai/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        stream=True,
        timeout=60,
    )

    if response.status_code == 401:
        response.close()
        raise NetAuthError("Invalid xAI API key")

    if response.status_code == 429:
        response.close()
        raise NetRateLimitError("xAI quota exceeded")

    if response.status_code >= 400:
        text = response.text
        response.close()
        raise NetProviderError(f"xAI API error [{response.status_code}]: {text}")

    try:
        for raw in response.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue

            data = raw[5:].strip()
            if data == "[DONE]":
                break

            try:
                chunk = json.loads(data)
            except Exception:
                continue

            delta = (
                chunk.get("choices", [{}])[0]
                .get("delta", {})
                .get("content")
            )

            if isinstance(delta, str) and delta:
                yield delta

    finally:
        response.close()


# ============================================================
# PUBLIC ENTRY POINT (WITH VISIBILITY)
# ============================================================

def generate_net_answer_stream(
    prompt: str,
    provider: Optional[str] = None,
    variant: str = "default",
    max_tokens: int = NET_MAX_TOKENS,
) -> Generator[str, None, None]:

    if not prompt or not prompt.strip():
        raise NetUsageError("Prompt cannot be empty")

    provider = provider or get_active_net_provider()
    model_id = get_net_model(provider, variant)
    max_tokens = min(max_tokens, NET_MAX_TOKENS)

    start = time.time()
    print(
        f"🌐 [NET START] provider={provider} | "
        f"model={model_id} | variant={variant}"
    )

    _acquire_stream_slot()

    try:
        if provider == "groq":
            yield from _groq_stream(prompt, model_id, max_tokens)

        elif provider == "xai":
            yield from _xai_stream(prompt, model_id, max_tokens)

        else:
            raise NetProviderError(
                f"Unsupported Net provider '{provider}'"
            )

    finally:
        _release_stream_slot()
        elapsed = round(time.time() - start, 2)
        print(
            f"🌐 [NET END] provider={provider} | "
            f"model={model_id} | {elapsed}s"
        )
