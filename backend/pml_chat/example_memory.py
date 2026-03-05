from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STORE_PATH = _PROJECT_ROOT / "data" / "pml_examples.json"
_STORE_LOCK = threading.Lock()
_CODE_FENCE_RE = re.compile(r"```(?:pml)?\s*([\s\S]*?)```", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9_][a-z0-9_.-]{2,}", re.IGNORECASE)
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "you",
    "your",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "will",
    "shall",
    "should",
    "can",
    "could",
    "would",
    "about",
    "into",
    "onto",
    "code",
    "pml",
}


def _store_path() -> Path:
    raw = os.getenv("PML_EXAMPLE_STORE_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT_STORE_PATH


def _ensure_store_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _normalize_code(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _looks_like_pml(text: str) -> bool:
    sample = (text or "").lower()
    if not sample:
        return False
    markers = (
        "$p",
        "endif",
        "enddo",
        "if ",
        "do ",
        "handle",
        "form",
        "macro",
        "object",
        "!!",
    )
    hits = sum(1 for marker in markers if marker in sample)
    line_count = sample.count("\n") + 1
    return hits >= 2 or (hits >= 1 and line_count >= 3)


def _extract_code_blocks(text: str) -> List[str]:
    blocks = [_normalize_code(match) for match in _CODE_FENCE_RE.findall(text or "")]
    blocks = [block for block in blocks if block]
    if blocks:
        return blocks

    normalized = _normalize_code(text or "")
    if _looks_like_pml(normalized):
        return [normalized]
    return []


def _code_hash(code: str) -> str:
    digest = hashlib.sha1(code.encode("utf-8")).hexdigest()
    return digest[:16]


def _read_examples_unlocked(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    raw_examples = data.get("examples") if isinstance(data, dict) else []
    if not isinstance(raw_examples, list):
        return []

    examples: List[Dict[str, str]] = []
    for item in raw_examples:
        if not isinstance(item, dict):
            continue
        code = _normalize_code(str(item.get("code") or ""))
        if not code:
            continue
        examples.append(
            {
                "id": str(item.get("id") or _code_hash(code)),
                "code": code,
                "note": str(item.get("note") or "").strip(),
                "source": str(item.get("source") or "unknown").strip() or "unknown",
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    return examples


def _write_examples_unlocked(path: Path, examples: List[Dict[str, str]]) -> None:
    _ensure_store_dir(path)
    payload = {"examples": examples}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _upsert_example_unlocked(
    examples: List[Dict[str, str]],
    *,
    code: str,
    note: str,
    source: str,
) -> bool:
    normalized_code = _normalize_code(code)
    if not normalized_code:
        return False

    example_id = _code_hash(normalized_code)
    timestamp = _now_iso()
    for item in examples:
        if item.get("id") != example_id:
            continue
        item["updated_at"] = timestamp
        if note and not item.get("note"):
            item["note"] = note
        if source and item.get("source") == "unknown":
            item["source"] = source
        return False

    examples.append(
        {
            "id": example_id,
            "code": normalized_code,
            "note": note,
            "source": source or "unknown",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    return True


def add_pml_example(*, code: str, note: str = "", source: str = "manual") -> Dict[str, str]:
    path = _store_path()
    normalized_note = (note or "").strip()
    normalized_source = (source or "").strip() or "manual"

    with _STORE_LOCK:
        examples = _read_examples_unlocked(path)
        _upsert_example_unlocked(
            examples,
            code=code,
            note=normalized_note,
            source=normalized_source,
        )
        max_examples = max(10, int(os.getenv("PML_EXAMPLE_MAX_ITEMS", "500")))
        if len(examples) > max_examples:
            examples = sorted(
                examples,
                key=lambda item: item.get("updated_at", ""),
                reverse=True,
            )[:max_examples]
        _write_examples_unlocked(path, examples)

        code_hash = _code_hash(_normalize_code(code))
        for item in examples:
            if item.get("id") == code_hash:
                return item

    return {
        "id": _code_hash(_normalize_code(code)),
        "code": _normalize_code(code),
        "note": normalized_note,
        "source": normalized_source,
        "created_at": "",
        "updated_at": "",
    }


def learn_examples_from_text(text: str, *, source: str, note: str = "") -> int:
    blocks = _extract_code_blocks(text)
    if not blocks:
        return 0

    learned = 0
    for block in blocks:
        try:
            add_pml_example(code=block, note=note, source=source)
            learned += 1
        except Exception:
            continue
    return learned


def learn_examples_from_history(history: List[Dict[str, str]], *, source: str) -> int:
    learned = 0
    for item in history or []:
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "")
        learned += learn_examples_from_text(content, source=source)
    return learned


def list_pml_examples(*, limit: int = 100) -> List[Dict[str, str]]:
    path = _store_path()
    safe_limit = max(1, min(500, int(limit)))
    with _STORE_LOCK:
        examples = _read_examples_unlocked(path)

    ordered = sorted(
        examples,
        key=lambda item: (item.get("updated_at", ""), item.get("created_at", "")),
        reverse=True,
    )[:safe_limit]

    return [
        {
            "id": str(item.get("id") or ""),
            "note": str(item.get("note") or ""),
            "source": str(item.get("source") or ""),
            "code": str(item.get("code") or ""),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }
        for item in ordered
        if str(item.get("id") or "").strip()
    ]


def delete_pml_example(example_id: str) -> bool:
    clean_id = (example_id or "").strip()
    if not clean_id:
        return False

    path = _store_path()
    with _STORE_LOCK:
        examples = _read_examples_unlocked(path)
        next_examples = [item for item in examples if str(item.get("id") or "") != clean_id]
        if len(next_examples) == len(examples):
            return False
        _write_examples_unlocked(path, next_examples)
        return True


def _tokenize(text: str) -> List[str]:
    tokens = []
    seen = set()
    for token in _TOKEN_RE.findall((text or "").lower()):
        if token in seen or token in _STOPWORDS:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def get_relevant_examples(question: str, *, limit: int = 3) -> List[Dict[str, str]]:
    path = _store_path()
    with _STORE_LOCK:
        examples = _read_examples_unlocked(path)

    if not examples:
        return []

    safe_limit = max(1, min(6, int(limit)))
    query_terms = _tokenize(question)
    ranked = []

    for index, item in enumerate(examples):
        haystack = f"{item.get('note', '')}\n{item.get('code', '')[:4000]}".lower()
        overlap = sum(1 for token in query_terms if token in haystack)
        ranked.append((overlap, item.get("updated_at", ""), -index, item))

    ranked.sort(reverse=True)
    selected = [row[3] for row in ranked if row[0] > 0][:safe_limit]

    if not selected:
        selected = sorted(
            examples,
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )[: min(2, safe_limit)]

    trimmed: List[Dict[str, str]] = []
    max_chars = max(400, int(os.getenv("PML_EXAMPLE_MAX_CHARS", "1400")))
    for item in selected:
        trimmed.append(
            {
                "id": item.get("id", ""),
                "note": item.get("note", ""),
                "source": item.get("source", ""),
                "code": (item.get("code", "") or "")[:max_chars],
            }
        )
    return trimmed


def get_example_store_stats() -> Dict[str, object]:
    path = _store_path()
    with _STORE_LOCK:
        examples = _read_examples_unlocked(path)
    return {
        "store_path": str(path),
        "count": len(examples),
    }
