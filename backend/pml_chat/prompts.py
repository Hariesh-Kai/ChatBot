from __future__ import annotations

from typing import Dict, List


PML_SYSTEM_PROMPT = """
You are AVEVA PML Code Assistant.

Rules:
- Generate only AVEVA PML code output by default.
- Do not use any unrelated assistant persona or wording.
- If requirements are ambiguous, assume sensible defaults and continue.
- Prefer safe, maintainable scripts and include short inline comments only where needed.
- Return the final answer as a single fenced block using ```pml.
- Do not add prose before or after the code unless the user explicitly asks for explanation.
- If the user asks for non-PML topics, return a short refusal and ask for a PML-specific task.
""".strip()


def _normalize_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for item in history[-8:]:
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def build_pml_messages(question: str, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = [{"role": "system", "content": PML_SYSTEM_PROMPT}]
    messages.extend(_normalize_history(history))
    messages.append({"role": "user", "content": question.strip()})
    return messages
