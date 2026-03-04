from __future__ import annotations

import json
from typing import Dict, Generator, Iterable, List, Optional

import requests

from backend.pml_chat.settings import get_pml_settings


class PMLClientError(RuntimeError):
    pass


def _response_text_payload(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                maybe = item.get("text")
                if isinstance(maybe, str) and maybe:
                    parts.append(maybe)
        return "".join(parts)
    return ""


def _extract_choice_text(payload: Dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    if delta:
        return _response_text_payload(delta.get("content"))

    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    if message:
        return _response_text_payload(message.get("content"))

    return _response_text_payload(choice.get("text"))


def _iter_event_lines(response: requests.Response) -> Iterable[str]:
    for raw in response.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        yield line


def stream_pml_completion(
    *,
    messages: List[Dict[str, str]],
    max_tokens: Optional[int] = None,
) -> Generator[str, None, None]:
    settings = get_pml_settings()
    if not settings.configured:
        raise PMLClientError("PML LLM is not configured. Set PML_LLM_BASE_URL and PML_LLM_MODEL.")

    url = f"{settings.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature,
        "max_tokens": int(max_tokens or settings.max_tokens),
        "stream": True,
    }
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    response: Optional[requests.Response] = None
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=settings.timeout_sec,
            verify=settings.verify_tls,
        )

        if response.status_code >= 400:
            body = (response.text or "").strip()
            detail = body[:300] if body else f"HTTP {response.status_code}"
            raise PMLClientError(f"PML model request failed: {detail}")

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type and "text/event-stream" not in content_type:
            data = response.json()
            text = _extract_choice_text(data) if isinstance(data, dict) else ""
            if text:
                yield text
            return

        for line in _iter_event_lines(response):
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            text = _extract_choice_text(data)
            if text:
                yield text
    except requests.RequestException as e:
        raise PMLClientError(f"PML model connection failed: {e}") from e
    finally:
        if response is not None:
            response.close()

