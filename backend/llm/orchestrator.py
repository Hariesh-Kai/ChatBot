# backend/llm/orchestrator.py

"""
orchestrator.py (SAFE, DISABLED-BY-DEFAULT)

Hierarchical deliberation engine.

- Disabled unless ADVANCED_REASONING=true
- Abort-aware
- Single-reasoner by default
- Verifier/editor optional
"""

import ast
import json
import re
from typing import Any, Dict, List, Optional
import time
import os

from backend.llm.answer_policy import infer_answer_policy
from backend.llm.hf_cache_utils import resolve_local_snapshot
from backend.llm.loader import (
    GGUF_MODELS,
    HF_CACHE_DIR,
    HF_MODELS,
    get_llm,
    hf_stream_generate,
    resolve_gguf_model_path,
)
from backend.llm.prompts import clean_model_output
from backend.llm.response_policy import apply_response_policy
from backend.rag.evaluator_runtime import evaluate_answer
from backend.rag.grounding import check_grounding
from backend.state.abort_signals import is_aborted

# ============================================================
# GLOBAL FEATURE FLAG
# ============================================================

ADVANCED_REASONING_ENABLED = os.getenv(
    "ADVANCED_REASONING", "false"
).lower() == "true"
AGENTIC_REVIEW_ENABLED = os.getenv("FULL_AGENTIC_REVIEW", "true").lower() not in {
    "0",
    "false",
    "no",
}

AGENT_0_5B_MODEL_ID = "agent_qwen_0_5b_q4"
PRIMARY_REVIEW_MODEL_ID = "lite_qwen_1_5b_q4"
SENIOR_ESCALATION_MODEL_ID = "base_qwen_3b"
MAX_AGENT_CHUNKS = 6
MAX_CHUNK_CHARS = 900
MAX_HISTORY_CHARS = 900
TRACE_PREVIEW_LIMIT = 700

AGENT_SHARED_HEADER = """
You are a hidden internal agent in Kavin's answer pipeline.
The user never sees your output directly.
Return exactly one valid JSON object and nothing else.
Do not use markdown fences.
Use only the provided inputs.
If document evidence is available, treat it as the source of truth.
If document evidence is not available, you may rely only on the question and chat history.
Never use external knowledge.
Never guess.
Copy IDs, codes, tags, revision values, dates, and numbers exactly as written.
If evidence is missing or conflicting, report that in JSON.
""".strip()

FIELD_LABELS = {
    "company_document_id": "Company Document ID",
    "document_number": "Document Number",
    "revision_number": "Revision Number",
    "document_title": "Document Title",
    "project_name": "Project Name",
    "tag_number": "Tag Number",
}

REVISION_VALUE_PATTERN = r"(?:[A-Z]{1,3}|\d+(?:\.\d+)?[A-Z]?)"
INVALID_FIELD_VALUES = {
    "number",
    "id",
    "no",
    "list",
    "revision",
    "status",
    "document",
    "sheet",
}

FIELD_PATTERNS: Dict[str, List[re.Pattern[str]]] = {
    "company_document_id": [
        re.compile(r"company\s+document\s+id\s*[:#-]?\s*([A-Z0-9./_-]+)", re.IGNORECASE),
        re.compile(r"document\s+id\s*[:#-]?\s*([A-Z0-9./_-]+)", re.IGNORECASE),
    ],
    "document_number": [
        re.compile(r"document\s+number\s*[:#-]?\s*([A-Z0-9./_-]+)", re.IGNORECASE),
    ],
    "revision_number": [
        re.compile(rf"\b[A-Z]{{2,}}-[A-Z]{{1,}}\b\s+({REVISION_VALUE_PATTERN})\b", re.IGNORECASE),
        re.compile(rf"validity\s+status\s+revision\s+number\s+[A-Z-]+\s+({REVISION_VALUE_PATTERN})\b", re.IGNORECASE),
        re.compile(rf"revision\s+number\s*[:#-]?\s*({REVISION_VALUE_PATTERN})\b", re.IGNORECASE),
        re.compile(rf"\brev(?:ision)?\.?\s*(?:no\.?|number)?\s*[:#-]?\s*({REVISION_VALUE_PATTERN})\b", re.IGNORECASE),
        re.compile(rf"file\s+name\s*:\s*[A-Z0-9]+_[A-Z]+({REVISION_VALUE_PATTERN})_[A-Z0-9.]+", re.IGNORECASE),
    ],
    "tag_number": [
        re.compile(r"tag\s+number\s*[:#-]?\s*([A-Z0-9./_-]+)", re.IGNORECASE),
    ],
}

# ============================================================
# INTERNAL EXECUTION HELPER
# ============================================================

def _run_model_once(
    *,
    model_id: str,
    prompt: str,
    session_id: Optional[str],
    max_tokens: int = 512,
    role: str = "unknown",
) -> str:
    """
    Execute ONE model synchronously.
    Abort-aware. Returns cleaned text or empty string.
    """

    start = time.time()
    print(f"[ORCH:{role}] START {model_id}")

    try:
        info = get_llm(model_id)
    except Exception as e:
        print(f"[ORCH:{role}] LOAD ERROR {model_id}: {repr(e)}")
        return ""
    tokens: List[str] = []

    try:
        if info["type"] == "gguf":
            for chunk in info["llm"](
                prompt,
                max_tokens=max_tokens,
                stream=True,
            ):
                if session_id and is_aborted(session_id):
                    print(f"[ORCH:{role}] aborted")
                    return ""

                text = ""
                if isinstance(chunk, dict) and "choices" in chunk:
                    text = chunk["choices"][0].get("text", "")
                elif isinstance(chunk, str):
                    text = chunk

                if text:
                    tokens.append(text)

        else:
            for t in hf_stream_generate(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=max_tokens,
                session_id=session_id,
            ):
                if session_id and is_aborted(session_id):
                    print(f"[ORCH:{role}] aborted")
                    return ""

                if t:
                    tokens.append(t)

    except Exception as e:
        print(f"[ORCH:{role}] ERROR {model_id}: {repr(e)}")
        return ""

    output = clean_model_output("".join(tokens))
    elapsed = round(time.time() - start, 2)
    preview = output.encode("ascii", "replace").decode("ascii")[:TRACE_PREVIEW_LIMIT]
    print(f"[ORCH:{role}] END {model_id} | {elapsed}s | {preview}")

    return output


def _is_local_model_available(model_id: str) -> bool:
    if not model_id:
        return False
    if model_id in GGUF_MODELS:
        path = resolve_gguf_model_path(model_id)
        return bool(path and os.path.exists(path))
    if model_id in HF_MODELS:
        repo_or_path = (HF_MODELS.get(model_id) or "").strip()
        if not repo_or_path:
            return False
        if os.path.exists(repo_or_path):
            return True
        return bool(resolve_local_snapshot(HF_CACHE_DIR, repo_or_path))
    return False


def _pick_first_available(candidates: List[str]) -> str:
    for model_id in candidates:
        if _is_local_model_available(model_id):
            return model_id
    return ""


def _select_agent_models(requested_model_id: str) -> Dict[str, str]:
    requested_local = requested_model_id if _is_local_model_available(requested_model_id) else ""
    router_model = _pick_first_available(
        [AGENT_0_5B_MODEL_ID, PRIMARY_REVIEW_MODEL_ID, requested_local, SENIOR_ESCALATION_MODEL_ID]
    )
    draft_model = _pick_first_available(
        [requested_local, PRIMARY_REVIEW_MODEL_ID, router_model, SENIOR_ESCALATION_MODEL_ID]
    )
    reviewer_model = _pick_first_available(
        [PRIMARY_REVIEW_MODEL_ID, requested_local, router_model, SENIOR_ESCALATION_MODEL_ID]
    )
    repair_model = _pick_first_available(
        [PRIMARY_REVIEW_MODEL_ID, draft_model, reviewer_model, router_model, SENIOR_ESCALATION_MODEL_ID]
    )
    senior_model = _pick_first_available(
        [SENIOR_ESCALATION_MODEL_ID, requested_local, PRIMARY_REVIEW_MODEL_ID, router_model]
    )
    return {
        "router": router_model,
        "planner": router_model,
        "extractor": router_model,
        "draft": draft_model,
        "reviewer": reviewer_model,
        "repair": repair_model,
        "gate": router_model,
        "senior": senior_model,
    }


def _compact_text(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned if len(cleaned) <= max_chars else cleaned[: max_chars - 1].rstrip() + "…"


def _compact_chunks(chunks: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for chunk in (chunks or [])[:MAX_AGENT_CHUNKS]:
        meta = chunk.get("metadata", {}) or {}
        compact.append(
            {
                "chunk_id": str(chunk.get("id") or ""),
                "page": int(meta.get("page_number") or 1),
                "section": str(meta.get("section") or chunk.get("section") or ""),
                "source_file": str(meta.get("source_file") or ""),
                "content": _compact_text(chunk.get("content", ""), MAX_CHUNK_CHARS),
            }
        )
    return compact


def _compact_history(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    compact: List[Dict[str, str]] = []
    for msg in (history or [])[-4:]:
        content = _compact_text(msg.get("content", ""), MAX_HISTORY_CHARS)
        if content:
            compact.append({"role": str(msg.get("role") or "user").lower(), "content": content})
    return compact


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = clean_model_output(text or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(raw[idx:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        snippet = raw[first : last + 1]
        for parser in (json.loads, ast.literal_eval):
            try:
                obj = parser(snippet)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    return None


def _merge_defaults(defaults: Dict[str, Any], parsed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(defaults)
    if not isinstance(parsed, dict):
        return merged
    for key, value in parsed.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _safe_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _log_stage_payload(stage: str, payload: Dict[str, Any]) -> None:
    preview = _safe_json(payload).encode("ascii", "replace").decode("ascii")
    print(f"[ORCH:{stage}] JSON {preview[:TRACE_PREVIEW_LIMIT]}")


def _run_json_agent(
    *,
    role: str,
    model_id: str,
    instruction: str,
    payload: Dict[str, Any],
    defaults: Dict[str, Any],
    session_id: Optional[str],
    max_tokens: int,
) -> Dict[str, Any]:
    if not model_id:
        _log_stage_payload(role, defaults)
        return dict(defaults)

    prompt = (
        f"{AGENT_SHARED_HEADER}\n\n{instruction.strip()}\n\nINPUT JSON:\n{_safe_json(payload)}\n"
    )
    parsed = _extract_json_object(
        _run_model_once(
            model_id=model_id,
            prompt=prompt,
            session_id=session_id,
            max_tokens=max_tokens,
            role=role,
        )
    )
    merged = _merge_defaults(defaults, parsed)
    _log_stage_payload(role, merged)
    return merged


def _infer_focus_fields(question: str) -> List[str]:
    q = (question or "").lower()
    fields: List[str] = []
    if "company document id" in q or "document id" in q:
        fields.append("company_document_id")
    if "document number" in q:
        fields.append("document_number")
    if "revision number" in q or "current revision" in q or re.search(r"\brev(?:ision)?\b", q):
        fields.append("revision_number")
    if "document title" in q or ("title" in q and "document" in q):
        fields.append("document_title")
    if "project name" in q:
        fields.append("project_name")
    if "tag number" in q or "tag no" in q:
        fields.append("tag_number")
    return list(dict.fromkeys(fields))


def _default_router_output(question: str) -> Dict[str, Any]:
    policy = infer_answer_policy(question)
    q = (question or "").lower()
    if policy.strict_factual:
        question_type = "fact_lookup"
    elif any(token in q for token in ("compare", "difference", "versus", "vs")):
        question_type = "comparison"
    elif any(token in q for token in ("summary", "summarize", "overview")):
        question_type = "summary"
    elif any(token in q for token in ("how", "procedure", "steps")):
        question_type = "procedure"
    else:
        question_type = "ambiguous"
    answer_shape = "one_line" if policy.strict_factual else "short" if policy.verbosity in {"one_line", "short"} else "normal"
    max_sentences = 1 if answer_shape == "one_line" else 3 if answer_shape == "short" else 6
    return {
        "question_type": question_type,
        "answer_shape": answer_shape,
        "strict_copy": bool(policy.strict_factual),
        "focus_fields": _infer_focus_fields(question),
        "needs_multi_chunk": bool(policy.needs_context or policy.use_deliberation),
        "reviewer_mode": "exact" if policy.strict_factual else "analytical" if policy.use_deliberation else "standard",
        "max_sentences": max_sentences,
        "escalation_hint": bool(question_type in {"comparison", "ambiguous"} and len((question or "").split()) > 10),
    }


def _default_planner_output(router_output: Dict[str, Any], chunk_count: int) -> Dict[str, Any]:
    focus_fields = list(router_output.get("focus_fields") or [])
    strict_copy = bool(router_output.get("strict_copy"))
    revision_like = any(field in {"company_document_id", "document_number", "revision_number"} for field in focus_fields)
    top_k = min(chunk_count or MAX_AGENT_CHUNKS, 6 if strict_copy else 8) or 4
    return {
        "top_k": top_k,
        "prefer_tables": False,
        "prefer_first_page": revision_like,
        "prefer_title_block": revision_like,
        "prefer_revision_block": "revision_number" in focus_fields,
        "keyword_boost_terms": [field.replace("_", " ") for field in focus_fields],
        "required_fields": focus_fields,
        "stop_if_exact_match": strict_copy,
    }


def _chunk_priority(
    chunk: Dict[str, Any],
    router_output: Dict[str, Any],
    planner_output: Dict[str, Any],
) -> float:
    content = str(chunk.get("content") or "")
    lowered = content.lower()
    section = str(chunk.get("section") or "").lower()
    score = float(chunk.get("page") == 1) * 2.0

    if planner_output.get("prefer_first_page") and int(chunk.get("page") or 1) == 1:
        score += 8.0
    if planner_output.get("prefer_title_block") and any(token in section for token in ("title", "cover", "basis of design")):
        score += 6.0
    if planner_output.get("prefer_revision_block") and "revision" in lowered:
        score += 4.0

    for field in list(router_output.get("focus_fields") or []):
        for pattern in FIELD_PATTERNS.get(field, []):
            if pattern.search(content):
                score += 5.0

    for term in list(planner_output.get("keyword_boost_terms") or []):
        token = str(term or "").strip().lower()
        if token and token in lowered:
            score += 2.0

    if "revision list" in lowered:
        score -= 1.5

    return score


def _prioritize_chunks(
    chunks: List[Dict[str, Any]],
    router_output: Dict[str, Any],
    planner_output: Dict[str, Any],
) -> List[Dict[str, Any]]:
    indexed = list(enumerate(chunks or []))
    ranked = sorted(
        indexed,
        key=lambda item: (
            _chunk_priority(item[1], router_output, planner_output),
            float(item[1].get("page") == 1),
            -item[0],
        ),
        reverse=True,
    )
    return [chunk for _, chunk in ranked]


def _line_with_match(content: str, match: re.Match[str]) -> str:
    target = match.group(0)
    for line in [line.strip() for line in str(content or "").splitlines() if line.strip()]:
        if target in line:
            return line
    return target


def _normalize_search_content(content: str) -> str:
    text = str(content or "")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = text.replace("|", " ")
    text = text.replace("**", " ")
    text = text.replace("###", " ")
    text = re.sub(r"[`_]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


_METADATA_PRIORITY_FIELDS = {"company_document_id", "document_number", "revision_number"}


def _fact_candidate_priority(field: str, candidate: Dict[str, Any]) -> tuple:
    page = int(candidate.get("page") or 999)
    quote = str(candidate.get("support_quote") or "").lower()

    page_priority = -page
    if field in _METADATA_PRIORITY_FIELDS:
        if page == 1:
            page_priority = 500
        elif page == 2:
            page_priority = 400
        else:
            page_priority = max(100 - page, -page)

    label_priority = 0
    if field == "company_document_id":
        if "company document id" in quote:
            label_priority += 50
        if "file name" in quote:
            label_priority += 10
    elif field == "document_number" and "document number" in quote:
        label_priority += 50
    elif field == "revision_number":
        if "revision number" in quote:
            label_priority += 50
        if "validity status revision number" in quote:
            label_priority += 20

    quote_priority = min(len(quote), 220)
    return (page_priority, label_priority, quote_priority)


def _should_replace_fact_candidate(
    field: str,
    existing: Optional[Dict[str, Any]],
    candidate: Dict[str, Any],
) -> bool:
    if not existing:
        return True
    return _fact_candidate_priority(field, candidate) > _fact_candidate_priority(field, existing)


def _heuristic_extract_facts(question: str, retrieval_chunks: List[Dict[str, Any]], focus_fields: List[str]) -> Dict[str, Any]:
    requested = focus_fields or _infer_focus_fields(question)
    question_lower = str(question or "").lower()
    matches: Dict[str, Dict[str, Dict[str, Any]]] = {field: {} for field in requested}
    for field in requested:
        for chunk in retrieval_chunks:
            content = str(chunk.get("content") or "")
            search_content = _normalize_search_content(content)
            for pattern in FIELD_PATTERNS.get(field, []):
                for match in pattern.finditer(search_content):
                    value = (match.group(1) or "").strip().strip(".,;:")
                    if value and value.lower() not in INVALID_FIELD_VALUES:
                        candidate = {
                            "field": field,
                            "value": value,
                            "chunk_id": str(chunk.get("chunk_id") or ""),
                            "page": int(chunk.get("page") or 1),
                            "support_quote": _compact_text(_line_with_match(search_content, match), 220),
                        }
                        existing = matches[field].get(value)
                        if _should_replace_fact_candidate(field, existing, candidate):
                            matches[field][value] = candidate
    facts: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    missing_fields: List[str] = []
    for field in requested:
        values = sorted(
            list(matches.get(field, {}).values()),
            key=lambda item: _fact_candidate_priority(field, item),
            reverse=True,
        )
        if field in {"company_document_id", "revision_number"}:
            preferred = [item for item in values if int(item.get("page") or 0) <= 2]
            if preferred:
                values = preferred
        if field == "revision_number" and "current" in question_lower and values:
            numeric_values = []
            for item in values:
                value = str(item.get("value") or "").strip()
                if re.fullmatch(r"\d+(?:\.\d+)?", value):
                    try:
                        numeric_values.append((float(value), item))
                    except Exception:
                        pass
            if numeric_values:
                best_value = max(score for score, _ in numeric_values)
                values = [item for score, item in numeric_values if score == best_value]
        if not values:
            missing_fields.append(field)
        elif len(values) == 1:
            facts.append(values[0])
        else:
            facts.append(values[0])
            conflicts.append(
                {
                    "field": field,
                    "candidates": [{"value": item["value"], "chunk_id": item["chunk_id"], "page": item["page"]} for item in values],
                }
            )
    confidence = "low" if conflicts else "medium" if missing_fields else "high"
    return {"facts": facts, "conflicts": conflicts, "missing_fields": missing_fields, "overall_confidence": confidence}


def _normalize_extractor_output(fallback: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
    facts: List[Dict[str, Any]] = []
    for fact in list(parsed.get("facts") or []):
        if isinstance(fact, dict) and fact.get("field") and fact.get("value"):
            facts.append(
                {
                    "field": str(fact.get("field")),
                    "value": str(fact.get("value")),
                    "chunk_id": str(fact.get("chunk_id") or ""),
                    "page": int(fact.get("page") or 1),
                    "support_quote": _compact_text(fact.get("support_quote", ""), 220),
                }
            )
    if not facts:
        facts = list(fallback.get("facts") or [])
    covered_fields = {
        str(item.get("field") or "").strip()
        for item in facts
        if isinstance(item, dict) and item.get("field")
    }
    missing_fields = [
        field
        for field in list(parsed.get("missing_fields") or fallback.get("missing_fields") or [])
        if str(field or "").strip() not in covered_fields
    ]
    return {
        "facts": facts,
        "conflicts": list(parsed.get("conflicts") or fallback.get("conflicts") or []),
        "missing_fields": missing_fields,
        "overall_confidence": str(parsed.get("overall_confidence") or fallback.get("overall_confidence") or "medium"),
    }


def _fact_phrase(question: str, field: str, value: str) -> str:
    q = (question or "").lower()
    label = FIELD_LABELS.get(field, field.replace("_", " ").title())
    if field == "company_document_id":
        subject = "The specific Company Document ID" if "specific" in q else "The Company Document ID"
    elif field == "revision_number":
        subject = "The current Revision Number" if "current" in q else "The Revision Number"
    else:
        subject = f"The {label}"
    return f"{subject} is {value}"


def _compose_fact_answer(question: str, router_output: Dict[str, Any], extractor_output: Dict[str, Any]) -> str:
    facts_by_field = {
        str(item.get("field") or "").strip(): str(item.get("value") or "").strip()
        for item in list(extractor_output.get("facts") or [])
        if isinstance(item, dict) and item.get("field") and item.get("value")
    }
    ordered = list(router_output.get("focus_fields") or facts_by_field.keys())
    phrases = [_fact_phrase(question, field, facts_by_field[field]) for field in ordered if field in facts_by_field]
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0] + "."
    second = phrases[1]
    if second.startswith("The "):
        second = "the " + second[4:]
    if len(phrases) == 2:
        return f"{phrases[0]} and {second}."
    rest = ", ".join(phrases[2:])
    return f"{phrases[0]} and {second}, with {rest}."


def _sentence_count(text: str) -> int:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if part.strip()]
    return len(sentences) if sentences else (1 if str(text or "").strip() else 0)


def _has_page_citation(text: str) -> bool:
    return bool(re.search(r"\[Pages?\s+\d", str(text or ""), flags=re.IGNORECASE))


def _release_gate_review_issues(
    question: str,
    answer: str,
    retrieval_chunks: List[Dict[str, Any]],
) -> List[str]:
    if not retrieval_chunks:
        return []

    evaluation = evaluate_answer(question, answer, retrieval_chunks)
    blockers = [
        str(item).strip()
        for item in list(evaluation.get("blockers") or [])
        if str(item).strip()
    ]

    # Draft answers often get citations appended later in the pipeline.
    # We still want support and relevance gating here without rejecting
    # an otherwise correct draft solely because the page tag is added later.
    if not _has_page_citation(answer):
        blockers = [item for item in blockers if item != "citation_missing_or_invalid"]

    if evaluation.get("decision") == "review" and not blockers:
        warnings = {
            str(item).strip()
            for item in list(evaluation.get("warnings") or [])
            if str(item).strip()
        }
        if warnings & {"facts_weakly_supported", "partial_grounding", "relevance_soft"}:
            blockers.append("needs_release_review")

    return list(dict.fromkeys(blockers))


def _passes_release_gate(
    question: str,
    answer: str,
    retrieval_chunks: List[Dict[str, Any]],
) -> bool:
    if not retrieval_chunks:
        return bool(str(answer or "").strip())
    return bool(evaluate_answer(question, answer, retrieval_chunks).get("should_release"))


def _finalize_strict_factual_output(
    *,
    question: str,
    answer: str,
    pages: List[int],
    verbosity: str,
    max_sentences: int,
    context_chunks: List[Dict[str, Any]],
) -> Optional[str]:
    cited_answer = _append_page_citation(answer, pages)
    candidates: List[str] = []

    formatted = apply_response_policy(cited_answer, verbosity=verbosity)
    formatted = _maybe_force_one_line(formatted, max_sentences)
    if formatted:
        candidates.append(formatted)

    one_line_cited = _maybe_force_one_line(cited_answer, max_sentences)
    if one_line_cited:
        candidates.append(one_line_cited)

    if cited_answer:
        candidates.append(cited_answer)

    for candidate in candidates:
        normalized = clean_model_output(candidate).strip()
        if not normalized:
            continue
        if _passes_release_gate(question, normalized, list(context_chunks or [])):
            return normalized
    return None


def _deterministic_review(
    question: str,
    answer: str,
    router_output: Dict[str, Any],
    extractor_output: Dict[str, Any],
    retrieval_chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    answer = clean_model_output(answer or "").strip()
    issues: List[str] = []
    facts = list(extractor_output.get("facts") or [])
    required_fields = list(router_output.get("focus_fields") or [])
    if not answer:
        issues.append("missing_answer")
    if _sentence_count(answer) > int(router_output.get("max_sentences") or 1):
        issues.append("verbosity")
    if router_output.get("strict_copy"):
        if extractor_output.get("conflicts"):
            issues.append("conflict_unresolved")
        if extractor_output.get("missing_fields"):
            issues.append("missing_requested_field")
        for field in required_fields:
            values = [str(item.get("value") or "") for item in facts if isinstance(item, dict) and item.get("field") == field]
            if values and not any(value and value in answer for value in values):
                issues.append("missing_requested_field")
                break
    if retrieval_chunks and check_grounding(answer, retrieval_chunks).get("grounding_score", 1.0) < 0.45:
        issues.append("unsupported_claim")
    issues.extend(_release_gate_review_issues(question, answer, retrieval_chunks))
    issues = list(dict.fromkeys(issues))
    return {
        "verdict": "pass" if not issues else "fail",
        "issues": issues,
        "unsupported_spans": [],
        "approved_answer": answer if not issues else "",
        "repair_instructions": "Use only extracted facts, remove unsupported detail, and satisfy the requested answer shape." if issues else "",
        "confidence": "high" if not issues else "medium" if len(issues) == 1 else "low",
    }


def _resolve_gate_default(router_output: Dict[str, Any], extractor_output: Dict[str, Any], candidate_answer: str, review_output: Dict[str, Any]) -> Dict[str, Any]:
    if not candidate_answer:
        return {"decision": "reject", "final_answer": "", "reason_code": "missing", "console_note": "no candidate answer"}
    if extractor_output.get("conflicts"):
        return {"decision": "escalate_3b", "final_answer": "", "reason_code": "conflict", "console_note": "conflicting extracted values"}
    if review_output.get("verdict") == "pass":
        return {"decision": "release", "final_answer": candidate_answer, "reason_code": "ok", "console_note": "review passed"}
    if router_output.get("strict_copy"):
        return {"decision": "retry_once", "final_answer": "", "reason_code": "unsupported", "console_note": "strict-copy review failed"}
    return {"decision": "retry_once", "final_answer": "", "reason_code": "format", "console_note": "review failed"}


def _normalize_gate_output(gate_output: Dict[str, Any], gate_default: Dict[str, Any], review_output: Dict[str, Any], extractor_output: Dict[str, Any], candidate_answer: str) -> Dict[str, Any]:
    decision = str(gate_output.get("decision") or gate_default.get("decision") or "reject").strip().lower()
    if decision not in {"release", "retry_once", "escalate_3b", "reject"}:
        decision = str(gate_default.get("decision") or "reject")
    final_answer = clean_model_output(str(gate_output.get("final_answer") or gate_default.get("final_answer") or "")).strip()
    if extractor_output.get("conflicts"):
        decision = "escalate_3b"
        final_answer = ""
    if decision == "release" and review_output.get("verdict") != "pass":
        decision = "retry_once"
        final_answer = ""
    if decision == "release" and not final_answer:
        final_answer = clean_model_output(candidate_answer).strip()
    return {
        "decision": decision,
        "final_answer": final_answer,
        "reason_code": str(gate_output.get("reason_code") or gate_default.get("reason_code") or ""),
        "console_note": str(gate_output.get("console_note") or gate_default.get("console_note") or ""),
    }


def _instruction_router() -> str:
    return "You are RouterAgent. Do not answer the question. Return JSON keys: question_type, answer_shape, strict_copy, focus_fields, needs_multi_chunk, reviewer_mode, max_sentences, escalation_hint."


def _instruction_planner() -> str:
    return "You are RetrievalPlannerAgent. Plan retrieval bias only. For title-page metadata questions, prefer first-page, title-block, and revision-list evidence. Return JSON keys: top_k, prefer_tables, prefer_first_page, prefer_title_block, prefer_revision_block, keyword_boost_terms, required_fields, stop_if_exact_match."


def _instruction_extractor() -> str:
    return "You are EvidenceExtractorAgent. Extract exact facts from retrieval_chunks into JSON only. Return keys: facts, conflicts, missing_fields, overall_confidence."


def _instruction_draft() -> str:
    return "You are DraftWriterAgent. Write a hidden draft answer. If extracted facts exist, use only those facts. If answer_shape is one_line, output one sentence. Return keys: draft_answer, used_fields, added_claims, answer_shape."


def _instruction_review() -> str:
    return "You are GroundingReviewerAgent. Review the hidden draft against extracted facts and retrieval_chunks. Return keys: verdict, issues, unsupported_spans, approved_answer, repair_instructions, confidence."


def _instruction_repair() -> str:
    return "You are RepairAgent. Rewrite the hidden answer so it satisfies the reviewer. Do not add unsupported claims. Return keys: repaired_answer, fixed_issues, still_risky, should_escalate."


def _instruction_gate() -> str:
    return "You are ReleaseGateAgent. Decide whether to release, retry_once, escalate_3b, or reject. Return keys: decision, final_answer, reason_code, console_note."


def _instruction_senior() -> str:
    return "You are SeniorResolutionAgent. Resolve only cases that failed the normal pipeline. Use extracted facts and retrieval_chunks as source of truth. Return keys: senior_answer, senior_verdict, reason_code."


def _maybe_force_one_line(text: str, max_sentences: int) -> str:
    if max_sentences > 1:
        return text.strip()
    cleaned = str(text or "").strip()
    citation_match = re.search(r"(\[Page(?:s)?[^\]]+\])\s*$", cleaned, re.IGNORECASE)
    trailing_citation = citation_match.group(1).strip() if citation_match else ""
    body = cleaned[:citation_match.start()].strip() if citation_match else cleaned
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", body) if part.strip()]
    first_sentence = sentences[0] if sentences else body
    if trailing_citation and trailing_citation.lower() not in first_sentence.lower():
        return f"{first_sentence} {trailing_citation}".strip()
    return first_sentence


_STRICT_FACT_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "many",
    "much",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "which",
    "who",
    "will",
    "with",
}
_STRICT_FACT_NEGATIONS = (
    "not specified",
    "not provided",
    "not mentioned",
    "not available",
    "cannot be determined",
    "no information available",
)


def _extract_retrieved_pages(chunks: List[Dict[str, Any]]) -> List[int]:
    pages = sorted(
        {
            int((chunk.get("metadata") or {}).get("page_number") or chunk.get("page") or 1)
            for chunk in (chunks or [])
            if isinstance((chunk.get("metadata") or {}).get("page_number") or chunk.get("page") or 1, (int, float))
        }
    )
    return [page for page in pages if page > 0]


def _format_page_citation(pages: List[int]) -> str:
    unique_pages = sorted({int(page) for page in pages if isinstance(page, int) and page > 0})
    if not unique_pages:
        return ""
    if len(unique_pages) == 1:
        return f"[Page {unique_pages[0]}]"
    preview = ", ".join(str(page) for page in unique_pages[:3])
    if len(unique_pages) > 3:
        preview += ", ..."
    return f"[Pages {preview}]"


def _append_page_citation(text: str, pages: List[int]) -> str:
    cleaned = clean_model_output(text or "").strip()
    if not cleaned:
        return ""
    if re.search(r"\[Page(?:s)?\s+\d", cleaned, re.IGNORECASE):
        return cleaned
    citation = _format_page_citation(pages)
    if not citation:
        return cleaned
    return f"{cleaned} {citation}".strip()


def _strict_fact_question_tokens(question: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]{2,}", str(question or "").lower()))
    return tokens - _STRICT_FACT_STOPWORDS


def _clean_support_unit(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^(?:#{1,6}\s*)?(?:section|table):\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^context:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:\*{1,2}[^*]{2,40}\*{1,2}\s*){1,3}", "", cleaned)
    cleaned = re.sub(r"^([A-Z][A-Za-z]{2,})(?:\s+\1){1,2}\s+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -|")


def _split_support_units(text: str) -> List[str]:
    raw_units = re.split(r"[\r\n]+", str(text or ""))
    units: List[str] = []
    for raw in raw_units:
        fragments = re.split(r"(?=\b\d+[\.\)])", str(raw or ""))
        for fragment in fragments:
            cleaned = _clean_support_unit(fragment)
            if len(cleaned) >= 12:
                units.append(cleaned)
    if units:
        return units[:12]

    fallback = _clean_support_unit(text)
    return [fallback] if len(fallback) >= 12 else []


def _question_expects_numeric(question: str) -> bool:
    q = str(question or "").lower()
    return any(
        phrase in q
        for phrase in ("how many", "how much", "maximum", "minimum", "what is the", "current revision")
    )


def _question_expects_list(question: str) -> bool:
    q = str(question or "").lower()
    return any(phrase in q for phrase in ("list", "what are", "which are", "categories"))


def _has_standalone_numeric_value(text: str) -> bool:
    return bool(re.search(r"\b\d+(?:\.\d+)?\b", str(text or "")))


def _looks_like_list_answer(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if any(
        phrase in lowered
        for phrase in (
            "same categories",
            "same set of classifications",
            "same classifications",
            "categorized under the same",
        )
    ):
        return False
    item_like = cleaned.count(";") >= 1 or cleaned.count(",") >= 2
    enumerated = bool(re.search(r"\b1[\.\)]\s+", cleaned))
    return item_like or enumerated


def _extract_best_support_snippet(question: str, source_text: str, fallback_text: str = "") -> str:
    candidates: List[str] = []
    normalized = _normalize_search_content(source_text)
    question_lower = str(question or "").lower()
    if normalized and question_lower.startswith(("at what", "at which")):
        sentence_candidates = [
            _clean_support_unit(part)
            for part in re.split(r"(?<=[.!?])\s+", normalized)
            if len(_clean_support_unit(part)) >= 12
        ]
        if sentence_candidates:
            best_sentence = ""
            best_sentence_score = float("-inf")
            for sentence in sentence_candidates:
                score = _support_unit_score(question, _strict_fact_question_tokens(question), sentence)
                if score <= best_sentence_score:
                    continue
                best_sentence = sentence
                best_sentence_score = score
            if best_sentence:
                return best_sentence.rstrip(" .")
    if normalized:
        candidates.extend(re.split(r"(?<=[.!?])\s+", normalized))
    if source_text:
        candidates.extend(re.split(r"[\r\n]+", source_text))
    if fallback_text:
        candidates.append(fallback_text)

    best_text = ""
    best_score = float("-inf")

    for candidate in candidates:
        cleaned = _clean_support_unit(candidate)
        if len(cleaned) < 12:
            continue
        score = _support_unit_score(question, _strict_fact_question_tokens(question), cleaned)
        if _question_expects_numeric(question) and not _has_standalone_numeric_value(cleaned):
            continue
        if score > best_score:
            best_score = score
            best_text = cleaned

    if not best_text:
        best_text = _clean_support_unit(fallback_text or source_text)

    if _question_expects_numeric(question):
        best_text = re.sub(
            r"^[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,4}\s+(?=(?:A\s+number\s+of|A\s+total\s+of|Only\s+\w+|\d))",
            "",
            best_text,
        ).strip()
        for splitter in (" and ", " due to ", " however ", " but "):
            lower = best_text.lower()
            idx = lower.find(splitter)
            if idx > 25:
                best_text = best_text[:idx].strip(" ,;:")
                break

    return best_text.strip()


def _support_unit_score(question: str, question_tokens: set[str], unit: str) -> float:
    unit_tokens = set(re.findall(r"[a-z0-9]{2,}", unit.lower()))
    overlap = question_tokens & unit_tokens
    if not overlap:
        return 0.0

    score = float(len(overlap) * 2) + (len(overlap) / max(len(question_tokens), 1))
    if _question_expects_numeric(question) and re.search(r"\b\d", unit):
        score += 2.5
    if _question_expects_list(question) and re.search(r"^\d+[\.\)]\s+", unit):
        score += 1.5
    if any(phrase in unit.lower() for phrase in _STRICT_FACT_NEGATIONS):
        score -= 2.5
    return score


def _find_support_evidence(question: str, retrieval_chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    question_tokens = _strict_fact_question_tokens(question)
    if not question_tokens:
        return []

    evidence: List[Dict[str, Any]] = []
    for chunk in retrieval_chunks:
        page = int((chunk.get("metadata") or {}).get("page_number") or chunk.get("page") or 1)
        for unit in _split_support_units(chunk.get("content", "")):
            score = _support_unit_score(question, question_tokens, unit)
            if score <= 0:
                continue
            evidence.append(
                {
                    "text": unit,
                    "page": page,
                    "score": round(score, 3),
                    "source_text": str(chunk.get("content") or ""),
                }
            )

    return sorted(evidence, key=lambda item: float(item.get("score") or 0.0), reverse=True)


def _coalesce_list_items(lines: List[str]) -> str:
    items: List[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", str(line or "").strip())
        cleaned = cleaned.strip(" -;:,")
        lowered = cleaned.lower()
        if not cleaned or lowered in seen:
            continue
        seen.add(lowered)
        items.append(cleaned)

    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "; ".join(items[:3])


def _extract_list_items_from_source(text: str) -> List[str]:
    normalized = _normalize_search_content(text)
    enumerated: List[str] = []
    seen_enumerated: set[str] = set()
    for match in re.finditer(r"(?:^|\s)\d+\.\s+(.+?)(?=(?:\s+\d+\.\s+)|$)", normalized):
        cleaned = str(match.group(1) or "").strip(" -;:,")
        cleaned = re.sub(r"==>\s*picture.*?<==", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned)
        letters = re.sub(r"[^A-Za-z]+", "", cleaned)
        word_count = len(cleaned.split())
        lowered = cleaned.lower()
        if word_count < 2 or word_count > 12 or lowered in seen_enumerated:
            continue
        if letters and sum(1 for ch in letters if ch.isupper()) / max(len(letters), 1) >= 0.8:
            continue
        seen_enumerated.add(lowered)
        enumerated.append(cleaned)
    if enumerated:
        return enumerated[:4]

    units = _split_support_units(text)
    items: List[str] = []
    seen: set[str] = set()
    for unit in units:
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", str(unit or "").strip())
        cleaned = re.sub(r"==>\s*picture.*?<==", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" -;:,")
        word_count = len(cleaned.split())
        lowered = cleaned.lower()
        if not cleaned or word_count < 2 or word_count > 12 or lowered in seen:
            continue
        seen.add(lowered)
        items.append(cleaned)
    return items[:4]


def _build_passage_fact_answer(question: str, retrieval_chunks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    evidence = _find_support_evidence(question, retrieval_chunks)
    if not evidence:
        return None

    top = evidence[0]
    pages = [int(top.get("page") or 1)]

    if _question_expects_list(question):
        preferred = top
        for item in evidence:
            sibling_count = len(_extract_list_items_from_source(str(item.get("source_text") or "")))
            if sibling_count >= 2:
                preferred = item
                break

        pages = [int(preferred.get("page") or 1)]
        sibling_lines = _extract_list_items_from_source(str(preferred.get("source_text") or ""))
        if not sibling_lines:
            sibling_lines = [
                item["text"]
                for item in evidence[:4]
                if int(item.get("page") or 1) == pages[0]
                and float(item.get("score") or 0.0) >= max(float(preferred.get("score") or 0.0) - 1.25, 2.5)
            ]
        merged = _coalesce_list_items(sibling_lines)
        if merged:
            return {"answer": f"The listed items are {merged}.", "pages": pages}

    if _question_expects_numeric(question):
        support_text = _extract_best_support_snippet(
            question,
            str(top.get("source_text") or ""),
            str(top.get("text") or ""),
        )
        if not _has_standalone_numeric_value(support_text):
            return None
        return {"answer": f"According to the document, {support_text}.", "pages": pages}

    if float(top.get("score") or 0.0) < 3.0:
        return None
    support_text = _extract_best_support_snippet(
        question,
        str(top.get("source_text") or ""),
        str(top.get("text") or ""),
    ).rstrip(" .")
    return {"answer": f"According to the document, {support_text}.", "pages": pages}


def resolve_strict_factual_answer(
    *,
    question: str,
    context_chunks: Optional[List[Dict[str, Any]]],
    verbosity: str,
) -> Optional[Dict[str, Any]]:
    if not context_chunks:
        return None

    compact_chunks = _compact_chunks(context_chunks)
    router_output = _default_router_output(question)
    if not router_output.get("strict_copy"):
        return None

    planner_output = _default_planner_output(router_output, len(compact_chunks))
    prioritized_chunks = _prioritize_chunks(compact_chunks, router_output, planner_output)
    planned_chunks = prioritized_chunks[: max(1, int(planner_output.get("top_k") or 1))]
    extractor_output = _heuristic_extract_facts(
        question,
        planned_chunks,
        list(router_output.get("focus_fields") or []),
    )

    candidate = clean_model_output(_compose_fact_answer(question, router_output, extractor_output)).strip()
    if candidate:
        candidate_review = _deterministic_review(
            question,
            candidate,
            router_output,
            extractor_output,
            list(context_chunks or []),
        )
        if candidate_review.get("verdict") == "pass" and not extractor_output.get("conflicts"):
            pages = [
                int(item.get("page") or 1)
                for item in list(extractor_output.get("facts") or [])
                if isinstance(item, dict)
            ]
            final_answer = _finalize_strict_factual_output(
                question=question,
                answer=candidate,
                pages=pages,
                verbosity=verbosity,
                max_sentences=int(router_output.get("max_sentences") or 1),
                context_chunks=list(context_chunks or []),
            )
            if final_answer:
                return {"final_answer": final_answer, "mode": "field_heuristic"}

    passage_candidate = _build_passage_fact_answer(question, planned_chunks)
    if not passage_candidate:
        return None

    passage_answer = clean_model_output(str(passage_candidate.get("answer") or "")).strip()
    if not passage_answer:
        return None

    passage_review = _deterministic_review(
        question,
        passage_answer,
        router_output,
        extractor_output,
        list(context_chunks or []),
    )
    if passage_review.get("verdict") != "pass":
        return None

    final_answer = _finalize_strict_factual_output(
        question=question,
        answer=passage_answer,
        pages=[int(page) for page in list(passage_candidate.get("pages") or [])],
        verbosity=verbosity,
        max_sentences=int(router_output.get("max_sentences") or 1),
        context_chunks=list(context_chunks or []),
    )
    if final_answer:
        return {"final_answer": final_answer, "mode": "support_passage"}
    return None


def release_strict_factual_answer(
    *,
    question: str,
    answer: str,
    context_chunks: Optional[List[Dict[str, Any]]],
    verbosity: str,
) -> str:
    abstain = _append_page_citation(
        "I couldn't verify a supported answer from the retrieved document context.",
        _extract_retrieved_pages(list(context_chunks or [])),
    )
    candidate = clean_model_output(answer or "").strip()
    if not candidate:
        resolved = resolve_strict_factual_answer(
            question=question,
            context_chunks=context_chunks,
            verbosity=verbosity,
        )
        return resolved["final_answer"] if resolved else apply_response_policy(abstain, verbosity=verbosity)

    if _question_expects_numeric(question) and not _has_standalone_numeric_value(candidate):
        resolved = resolve_strict_factual_answer(
            question=question,
            context_chunks=context_chunks,
            verbosity=verbosity,
        )
        return resolved["final_answer"] if resolved else apply_response_policy(abstain, verbosity=verbosity)

    if _question_expects_list(question) and not _looks_like_list_answer(candidate):
        resolved = resolve_strict_factual_answer(
            question=question,
            context_chunks=context_chunks,
            verbosity=verbosity,
        )
        return resolved["final_answer"] if resolved else apply_response_policy(abstain, verbosity=verbosity)

    if any(phrase in candidate.lower() for phrase in _STRICT_FACT_NEGATIONS):
        resolved = resolve_strict_factual_answer(
            question=question,
            context_chunks=context_chunks,
            verbosity=verbosity,
        )
        return resolved["final_answer"] if resolved else apply_response_policy(abstain, verbosity=verbosity)

    if not context_chunks:
        return apply_response_policy(candidate, verbosity=verbosity)

    compact_chunks = _compact_chunks(context_chunks)
    router_output = _default_router_output(question)
    extractor_output = _heuristic_extract_facts(
        question,
        compact_chunks,
        list(router_output.get("focus_fields") or []),
    )
    review_output = _deterministic_review(
        question,
        candidate,
        router_output,
        extractor_output,
        list(context_chunks or []),
    )
    if review_output.get("verdict") != "pass":
        resolved = resolve_strict_factual_answer(
            question=question,
            context_chunks=context_chunks,
            verbosity=verbosity,
        )
        return resolved["final_answer"] if resolved else apply_response_policy(abstain, verbosity=verbosity)

    cited_answer = candidate
    if not re.search(r"\[Page(?:s)?\s+\d", candidate, re.IGNORECASE):
        support = _build_passage_fact_answer(question, compact_chunks)
        support_pages = (
            [int(page) for page in list(support.get("pages") or [])]
            if support
            else _extract_retrieved_pages(list(context_chunks or []))[:2]
        )
        cited_answer = _append_page_citation(candidate, support_pages)

    final_answer = apply_response_policy(cited_answer, verbosity=verbosity)
    final_answer = _maybe_force_one_line(final_answer, int(router_output.get("max_sentences") or 1))
    if _passes_release_gate(question, final_answer, list(context_chunks or [])):
        return final_answer

    resolved = resolve_strict_factual_answer(
        question=question,
        context_chunks=context_chunks,
        verbosity=verbosity,
    )
    return resolved["final_answer"] if resolved else apply_response_policy(abstain, verbosity=verbosity)


def run_agentic_review_pipeline(
    *,
    question: str,
    requested_model_id: str,
    context_chunks: Optional[List[Dict[str, Any]]],
    chat_history: Optional[List[Dict[str, Any]]],
    verbosity: str,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not AGENTIC_REVIEW_ENABLED or (session_id and is_aborted(session_id)):
        return None

    models = _select_agent_models(requested_model_id)
    if not models.get("draft") or not models.get("router") or not models.get("reviewer"):
        return None

    compact_chunks = _compact_chunks(context_chunks)
    compact_history = _compact_history(chat_history)
    print(f"[ORCH:agentic] models={models}")

    router_output = _run_json_agent(
        role="router",
        model_id=models["router"],
        instruction=_instruction_router(),
        payload={"question": question, "recent_history": compact_history},
        defaults=_default_router_output(question),
        session_id=session_id,
        max_tokens=64,
    )
    planner_output = _run_json_agent(
        role="planner",
        model_id=models["planner"],
        instruction=_instruction_planner(),
        payload={"question": question, "router_output": router_output, "retrieval_chunk_count": len(compact_chunks)},
        defaults=_default_planner_output(router_output, len(compact_chunks)),
        session_id=session_id,
        max_tokens=64,
    )
    prioritized_chunks = _prioritize_chunks(compact_chunks, router_output, planner_output)
    planned_chunks = prioritized_chunks[: max(1, int(planner_output.get("top_k") or len(prioritized_chunks) or 1))]

    extractor_fallback = _heuristic_extract_facts(question, planned_chunks, list(router_output.get("focus_fields") or []))
    extractor_output = _normalize_extractor_output(
        extractor_fallback,
        _run_json_agent(
            role="extractor",
            model_id=models["extractor"],
            instruction=_instruction_extractor(),
            payload={"question": question, "router_output": router_output, "planner_output": planner_output, "retrieval_chunks": planned_chunks},
            defaults=extractor_fallback,
            session_id=session_id,
            max_tokens=224,
        ),
    )
    _log_stage_payload("extractor_normalized", extractor_output)

    deterministic_draft = _compose_fact_answer(question, router_output, extractor_output)
    draft_output = _run_json_agent(
        role="draft",
        model_id=models["draft"],
        instruction=_instruction_draft(),
        payload={"question": question, "router_output": router_output, "extractor_output": extractor_output, "recent_history": compact_history},
        defaults={"draft_answer": deterministic_draft, "used_fields": list(router_output.get("focus_fields") or []), "added_claims": [], "answer_shape": str(router_output.get("answer_shape") or "short")},
        session_id=session_id,
        max_tokens=96 if router_output.get("answer_shape") == "one_line" else 256,
    )
    candidate = clean_model_output(str(draft_output.get("draft_answer") or deterministic_draft)).strip()
    if router_output.get("strict_copy") and deterministic_draft:
        candidate = deterministic_draft
        draft_output["draft_answer"] = deterministic_draft
    if not candidate:
        return None

    review_output = _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or []))
    review_output = _run_json_agent(
        role="review",
        model_id=models["reviewer"],
        instruction=_instruction_review(),
        payload={"question": question, "router_output": router_output, "retrieval_chunks": planned_chunks, "extractor_output": extractor_output, "draft_output": draft_output},
        defaults=review_output,
        session_id=session_id,
        max_tokens=160,
    )
    if _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or [])).get("verdict") == "fail":
        review_output = _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or []))

    repair_output = _run_json_agent(
        role="repair",
        model_id=models["repair"],
        instruction=_instruction_repair(),
        payload={"question": question, "router_output": router_output, "extractor_output": extractor_output, "draft_output": draft_output, "review_output": review_output},
        defaults={"repaired_answer": deterministic_draft or candidate, "fixed_issues": list(review_output.get("issues") or []), "still_risky": bool(review_output.get("verdict") != "pass"), "should_escalate": bool(extractor_output.get("conflicts"))},
        session_id=session_id,
        max_tokens=144,
    )
    repaired_answer = clean_model_output(str(repair_output.get("repaired_answer") or deterministic_draft or candidate)).strip()
    repaired_answer = _maybe_force_one_line(repaired_answer, int(router_output.get("max_sentences") or 1))

    repaired_review = _deterministic_review(question, repaired_answer, router_output, extractor_output, list(context_chunks or []))
    gate_default = _resolve_gate_default(router_output, extractor_output, repaired_answer, repaired_review)
    if router_output.get("strict_copy") and gate_default.get("decision") == "release":
        gate_output = dict(gate_default)
    else:
        gate_output = _normalize_gate_output(
            _run_json_agent(
                role="gate",
                model_id=models["gate"],
                instruction=_instruction_gate(),
                payload={"question": question, "router_output": router_output, "extractor_output": extractor_output, "draft_output": draft_output, "review_output": repaired_review, "repair_output": repair_output},
                defaults=gate_default,
                session_id=session_id,
                max_tokens=96,
            ),
            gate_default,
            repaired_review,
            extractor_output,
            repaired_answer,
        )

    if gate_output["decision"] == "retry_once":
        repaired_answer = _compose_fact_answer(question, router_output, extractor_output) or repaired_answer
        repaired_review = _deterministic_review(question, repaired_answer, router_output, extractor_output, list(context_chunks or []))
        gate_output = _normalize_gate_output(gate_default, gate_default, repaired_review, extractor_output, repaired_answer)

    if gate_output["decision"] == "escalate_3b" and models.get("senior"):
        senior_output = _run_json_agent(
            role="senior",
            model_id=models["senior"],
            instruction=_instruction_senior(),
            payload={"question": question, "router_output": router_output, "retrieval_chunks": planned_chunks, "extractor_output": extractor_output, "review_output": repaired_review, "repair_output": repair_output},
            defaults={"senior_answer": deterministic_draft or repaired_answer, "senior_verdict": "reject", "reason_code": "conflict" if extractor_output.get("conflicts") else "unsupported"},
            session_id=session_id,
            max_tokens=256,
        )
        senior_answer = _maybe_force_one_line(clean_model_output(str(senior_output.get("senior_answer") or "")).strip(), int(router_output.get("max_sentences") or 1))
        senior_review = _deterministic_review(question, senior_answer, router_output, extractor_output, list(context_chunks or []))
        if senior_output.get("senior_verdict") == "release" and senior_review.get("verdict") == "pass":
            gate_output = {"decision": "release", "final_answer": senior_answer, "reason_code": str(senior_output.get("reason_code") or "resolved"), "console_note": "senior escalation resolved answer"}
        else:
            gate_output = {"decision": "reject", "final_answer": "", "reason_code": str(senior_output.get("reason_code") or "unsupported"), "console_note": "senior escalation failed"}

    final_answer = clean_model_output(str(gate_output.get("final_answer") or "")).strip()
    if gate_output["decision"] != "release" or not final_answer:
        return None
    final_answer = apply_response_policy(final_answer, verbosity=verbosity)
    final_answer = _maybe_force_one_line(final_answer, int(router_output.get("max_sentences") or 1))
    trace = {"models": models, "router": router_output, "planner": planner_output, "extractor": extractor_output, "draft": draft_output, "review": repaired_review, "repair": repair_output, "gate": gate_output}
    _log_stage_payload("release", {"final_answer": final_answer, "gate": gate_output})
    return {"final_answer": final_answer, "trace": trace, "decision": gate_output["decision"]}

# ============================================================
# PUBLIC API
# ============================================================

def deliberate_answer(
    *,
    question: str,
    context_text: str,
    reasoner_models: List[str],
    verifier_models: List[str],
    editor_model: str,
    verbosity: str,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """
    Advanced reasoning entry point.

    Returns None immediately if ADVANCED_REASONING is disabled.
    """

    if not ADVANCED_REASONING_ENABLED:
        return None

    if session_id and is_aborted(session_id):
        return None

    print("🚦 [ORCH] Advanced reasoning enabled")

    # ----------------------------
    # STAGE 1 — REASONER (ONE)
    # ----------------------------

    primary_reasoner = reasoner_models[0]

    reasoner_prompt = f"""
Answer the question using ONLY the provided document.

DOCUMENT:
{context_text}

QUESTION:
{question}

ANSWER:
""".strip()

    candidate = _run_model_once(
        model_id=primary_reasoner,
        prompt=reasoner_prompt,
        role="reasoner",
        session_id=session_id,
    )

    if not candidate:
        return None

    # ----------------------------
    # STAGE 2 — OPTIONAL VERIFIER
    # ----------------------------

    if verifier_models and not is_aborted(session_id):
        verifier = verifier_models[0]

        verify_prompt = f"""
Verify whether the answer is fully supported by the document.
Return the SAME answer if correct.
Return NOTHING if incorrect.

DOCUMENT:
{context_text}

ANSWER:
{candidate}
""".strip()

        verified = _run_model_once(
            model_id=verifier,
            prompt=verify_prompt,
            role="verifier",
            session_id=session_id,
        )

        if verified:
            candidate = verified

    # ----------------------------
    # STAGE 3 — EDITOR (OPTIONAL)
    # ----------------------------

    editor_prompt = f"""
Choose the best final answer.

QUESTION:
{question}

ANSWER:
{candidate}
""".strip()

    final = _run_model_once(
        model_id=editor_model,
        prompt=editor_prompt,
        role="editor",
        session_id=session_id,
    )

    final = final or candidate
    final = apply_response_policy(final, verbosity=verbosity)

    print("🏁 [ORCH] Finished")
    return final
