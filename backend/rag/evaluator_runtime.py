# backend/rag/evaluator_runtime.py

"""
Runtime-safe answer evaluator for RAG responses.

This version acts more like a lightweight release gate:
- checks factual support in retrieved context
- validates page citations
- uses answer-shape heuristics for list/numeric/code questions
- returns a release decision in addition to raw metric scores
"""

import re
from typing import Any, Dict, List

WEIGHT_FAITHFULNESS = 0.25
WEIGHT_RELEVANCE = 0.20
WEIGHT_PRECISION = 0.10
WEIGHT_CITATION = 0.20
WEIGHT_FACT_SUPPORT = 0.25

QUALITY_HIGH = 0.80
QUALITY_MEDIUM = 0.60
RELEASE_THRESHOLD = 0.72
REVIEW_THRESHOLD = 0.55

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
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
    "their",
    "there",
    "these",
    "this",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
}
_CODE_FIELD_TERMS = (
    "document id",
    "company document id",
    "document number",
    "revision code",
)
_REVISION_FIELD_TERMS = (
    "revision number",
    "current revision",
)
_ABSTENTION_PATTERNS = (
    "couldn't verify",
    "could not verify",
    "cannot verify",
    "can't verify",
    "not specified",
    "not provided",
    "insufficient context",
    "insufficient information",
    "no supported answer",
    "unable to verify",
)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) if text else set()


def _content_tokens(text: str) -> set[str]:
    return _tokenize(text) - _STOPWORDS


def _sentence_split(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]


def _chunk_corpus(rag_chunks: List[Dict[str, Any]]) -> str:
    return "\n".join(c.get("content", "") for c in rag_chunks if c.get("content"))


def _strip_page_citations(text: str) -> str:
    return re.sub(r"\[Pages?[^\]]+\]", "", str(text or ""), flags=re.IGNORECASE)


def _looks_like_abstention(text: str) -> bool:
    lowered = _strip_page_citations(text).lower()
    return any(pattern in lowered for pattern in _ABSTENTION_PATTERNS)


def _question_expects_revision(question: str) -> bool:
    q = str(question or "").lower()
    return any(term in q for term in _REVISION_FIELD_TERMS)


def _question_expects_numeric(question: str) -> bool:
    q = str(question or "").lower()
    return any(
        phrase in q
        for phrase in ("how many", "how much", "maximum", "minimum", "number of")
    )


def _question_expects_list(question: str) -> bool:
    q = str(question or "").lower()
    return any(
        phrase in q
        for phrase in ("what categories", "which categories", "what are", "which are", "list")
    )


def _question_expects_code(question: str) -> bool:
    q = str(question or "").lower()
    return any(term in q for term in _CODE_FIELD_TERMS)


def _question_anchor_phrases(question: str) -> List[str]:
    if _question_expects_list(question):
        return []

    words = [
        token
        for token in re.findall(r"[a-z0-9%]+", str(question or "").lower())
        if token and token not in _STOPWORDS
    ]
    if len(words) < 2:
        return []

    phrases: List[str] = []
    seen: set[str] = set()
    max_n = min(3, len(words))
    for n in range(max_n, 1, -1):
        for idx in range(len(words) - n + 1):
            phrase = " ".join(words[idx : idx + n]).strip()
            if not phrase or phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
    return phrases[:12]


def _anchor_phrase_score(question: str, answer: str) -> float:
    phrases = _question_anchor_phrases(question)
    if not phrases:
        return 1.0

    normalized = re.sub(r"\s+", " ", _strip_page_citations(answer).lower()).strip()
    if not normalized:
        return 0.0

    hits = sum(1 for phrase in phrases if phrase in normalized)
    return round(hits / max(len(phrases), 1), 3)


def _extract_cited_pages(text: str) -> List[int]:
    pages: List[int] = []
    raw = str(text or "")
    for match in re.finditer(r"\[Pages?\s+([0-9,\s.]+)\]", raw, flags=re.IGNORECASE):
        for value in re.findall(r"\d+", match.group(1)):
            try:
                pages.append(int(value))
            except Exception:
                continue
    return pages


def _extract_retrieved_pages(rag_chunks: List[Dict[str, Any]]) -> List[int]:
    pages: List[int] = []
    for chunk in rag_chunks:
        meta = chunk.get("metadata") or {}
        raw = meta.get("page_number")
        try:
            page = int(raw)
        except Exception:
            continue
        if page > 0:
            pages.append(page)
    return sorted(set(pages))


def _extract_numeric_tokens(text: str) -> List[str]:
    values = []
    seen: set[str] = set()
    for token in re.findall(r"\b\d+(?:[.,]\d+)?\b", _strip_page_citations(text)):
        clean = token.replace(",", "")
        if clean and clean not in seen:
            seen.add(clean)
            values.append(clean)
    return values


def _extract_code_tokens(text: str) -> List[str]:
    values = []
    seen: set[str] = set()
    patterns = [
        r"\b[A-Z0-9]{6,}\b",
        r"\b[A-Z0-9]{2,}(?:[-_/\.][A-Z0-9]{2,})+\b",
    ]
    raw = _strip_page_citations(text)
    for pattern in patterns:
        for token in re.findall(pattern, raw):
            clean = str(token).strip()
            if clean and clean not in seen:
                seen.add(clean)
                values.append(clean)
    return values


def _extract_list_items(answer: str) -> List[str]:
    text = str(answer or "").strip()
    if not text:
        return []

    cleaned = re.sub(r"\[Pages?[^\]]+\]", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"^The listed items are\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^The categories(?: listed)? (?:are|include)\s*", "", cleaned, flags=re.IGNORECASE)
    parts = [part.strip(" -;:,") for part in cleaned.split(";") if part.strip()]
    if len(parts) >= 2:
        return parts[:6]

    if cleaned.count(",") >= 2:
        parts = [part.strip(" -;:,") for part in cleaned.split(",") if part.strip()]
        if len(parts) >= 3:
            return parts[:6]

    return []


def _fact_in_corpus(fragment: str, corpus: str) -> bool:
    cleaned = re.sub(r"\s+", " ", str(fragment or "").strip()).lower()
    cleaned = cleaned.strip(" -;:,")
    if not cleaned:
        return False
    if cleaned in corpus:
        return True
    tokens = [token for token in _content_tokens(cleaned) if len(token) >= 3]
    if not tokens:
        return False
    overlap = sum(1 for token in tokens if token in corpus)
    return overlap / max(len(tokens), 1) >= 0.60


def _faithfulness(answer: str, chunk_corpus: str) -> float:
    sentences = _sentence_split(answer)
    if not sentences:
        return 1.0

    corpus_tokens = _tokenize(chunk_corpus)
    if not corpus_tokens:
        return 0.0

    checkable = [s for s in sentences if len(s.split()) >= 4]
    if not checkable:
        return 1.0

    grounded = 0
    for sent in checkable:
        meaningful = _tokenize(sent) - _STOPWORDS
        if not meaningful:
            grounded += 1
            continue
        overlap = meaningful & corpus_tokens
        if len(overlap) / len(meaningful) >= 0.40:
            grounded += 1

    return round(grounded / len(checkable), 3)


def _fact_support(question: str, answer: str, rag_chunks: List[Dict[str, Any]]) -> float:
    if not rag_chunks:
        return 0.0
    if _looks_like_abstention(answer):
        return 0.0

    corpus = _chunk_corpus(rag_chunks).lower()
    if not corpus.strip():
        return 0.0

    if _question_expects_list(question):
        items = _extract_list_items(answer)
        if not items:
            return 0.0
        hits = sum(1 for item in items if _fact_in_corpus(item, corpus))
        return round(hits / max(len(items), 1), 3)

    tokens = _extract_numeric_tokens(answer)
    if _question_expects_code(question):
        tokens.extend(_extract_code_tokens(answer))

    if tokens:
        unique_tokens: List[str] = []
        seen: set[str] = set()
        for token in tokens:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_tokens.append(token)
        hits = sum(1 for token in unique_tokens if token.lower() in corpus)
        return round(hits / max(len(unique_tokens), 1), 3)

    answer_tokens = _content_tokens(answer)
    if not answer_tokens:
        return 0.0

    hits = sum(1 for token in answer_tokens if token in corpus)
    return round(hits / max(len(answer_tokens), 1), 3)


def _answer_relevance(question: str, answer: str, rag_chunks: List[Dict[str, Any]]) -> float:
    if _looks_like_abstention(answer):
        return 0.0

    q_tokens = _content_tokens(question)
    normalized_answer = _strip_page_citations(answer)
    a_tokens = _content_tokens(normalized_answer) or _tokenize(normalized_answer)

    if not q_tokens:
        return 1.0
    if not a_tokens:
        return 0.0

    direct = min(len(q_tokens & a_tokens) / len(q_tokens), 1.0)
    score = direct
    anchor_score = _anchor_phrase_score(question, normalized_answer)

    support = _fact_support(question, answer, rag_chunks) if rag_chunks else 0.0
    if _question_expects_numeric(question) and _extract_numeric_tokens(answer):
        score = max(score, 0.70 + 0.30 * support)
    if _question_expects_revision(question) and _extract_numeric_tokens(answer):
        score = max(score, 0.72 + 0.28 * support)
    if _question_expects_code(question) and _extract_code_tokens(answer):
        score = max(score, 0.75 + 0.25 * support)
    if _question_expects_list(question):
        items = _extract_list_items(answer)
        if len(items) >= 2:
            score = max(score, 0.70 + 0.30 * support)
        elif items:
            score = max(score, 0.55 + 0.25 * support)
    elif _question_anchor_phrases(question):
        if anchor_score <= 0.0:
            score = min(score, 0.20)
        else:
            score = min(max(score, 0.50 + 0.50 * anchor_score), 1.0)

    return round(min(score, 1.0), 3)


def _context_precision(answer: str, rag_chunks: List[Dict[str, Any]]) -> float:
    if not rag_chunks:
        return 0.0

    answer_tokens = _content_tokens(answer)
    if not answer_tokens:
        answer_tokens = {
            token
            for token in _tokenize(answer)
            if len(token) >= 3 or any(ch.isdigit() for ch in token)
        }
    if not answer_tokens:
        return 0.0

    useful = 0
    for chunk in rag_chunks:
        content = chunk.get("content", "")
        chunk_tokens = _content_tokens(content) or _tokenize(content)
        overlap = chunk_tokens & answer_tokens
        if not overlap:
            continue

        overlap_ratio = len(overlap) / max(len(answer_tokens), 1)
        if len(answer_tokens) <= 4:
            useful += 1
            continue
        if len(overlap) >= 3 or overlap_ratio >= 0.20:
            useful += 1

    return round(useful / len(rag_chunks), 3)


def _citation_quality(question: str, answer: str, rag_chunks: List[Dict[str, Any]]) -> float:
    if not rag_chunks:
        return 0.0

    cited_pages = _extract_cited_pages(answer)
    retrieved_pages = _extract_retrieved_pages(rag_chunks)
    requires_citation = bool(
        _question_expects_numeric(question)
        or _question_expects_revision(question)
        or _question_expects_list(question)
        or _question_expects_code(question)
        or _extract_numeric_tokens(answer)
        or _extract_code_tokens(answer)
    )

    if not cited_pages:
        return 0.0 if requires_citation else 0.40
    if not retrieved_pages:
        return 0.0

    hits = sum(1 for page in cited_pages if page in retrieved_pages)
    return round(hits / max(len(cited_pages), 1), 3)


def _quality_label(overall: float) -> str:
    if overall >= QUALITY_HIGH:
        return "high"
    if overall >= QUALITY_MEDIUM:
        return "medium"
    return "low"


def _release_gate(
    *,
    question: str,
    answer: str,
    rag_chunks: List[Dict[str, Any]],
    faithfulness: float,
    relevance: float,
    precision: float,
    citation_quality: float,
    fact_support: float,
    overall: float,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    anchor_score = _anchor_phrase_score(question, answer)

    if not answer.strip():
        blockers.append("missing_answer")
    elif _looks_like_abstention(answer):
        blockers.append("abstained")

    if (_question_expects_numeric(question) or _question_expects_revision(question)) and not _extract_numeric_tokens(answer):
        blockers.append("missing_numeric_value")
    if _question_expects_code(question) and not _extract_code_tokens(answer):
        blockers.append("missing_code_value")
    if _question_expects_list(question) and len(_extract_list_items(answer)) < 2:
        blockers.append("missing_list_items")
    if not _question_expects_list(question) and _question_anchor_phrases(question):
        if anchor_score <= 0.0:
            blockers.append("missing_target_phrase")
        elif anchor_score < 0.25:
            warnings.append("target_phrase_weak")

    if rag_chunks and citation_quality < 0.50:
        blockers.append("citation_missing_or_invalid")
    elif rag_chunks and citation_quality < 1.0:
        warnings.append("citation_partial")

    if fact_support < 0.60:
        blockers.append("facts_not_supported")
    elif fact_support < 0.85:
        warnings.append("facts_weakly_supported")

    if faithfulness < 0.60:
        blockers.append("unsupported_claim")
    elif faithfulness < 0.80:
        warnings.append("partial_grounding")

    if relevance < 0.45:
        blockers.append("off_target")
    elif relevance < 0.70:
        warnings.append("relevance_soft")

    if precision < 0.20:
        warnings.append("low_context_precision")

    if blockers:
        decision = "reject"
    elif overall >= RELEASE_THRESHOLD:
        decision = "release"
    elif overall >= REVIEW_THRESHOLD:
        decision = "review"
    else:
        decision = "reject"

    return {
        "decision": decision,
        "should_release": decision == "release",
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def evaluate_answer(
    question: str,
    answer: str,
    rag_chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Evaluate RAG answer quality and return a release-style decision.

    Never raises.
    """
    default_gate = {
        "decision": "reject",
        "should_release": False,
        "blockers": ["missing_answer"],
        "warnings": [],
    }
    default = {
        "faithfulness": 0.0,
        "answer_relevance": 0.0,
        "context_precision": 0.0,
        "citation_quality": 0.0,
        "fact_support": 0.0,
        "overall": 0.0,
        "quality": "low",
        **default_gate,
    }

    try:
        if not answer:
            return default

        if not rag_chunks:
            relevance = _answer_relevance(question, answer, [])
            overall = round(WEIGHT_RELEVANCE * relevance, 3)
            gate = {
                "decision": "reject",
                "should_release": False,
                "blockers": ["missing_context"],
                "warnings": [],
            }
            return {
                "faithfulness": 0.0,
                "answer_relevance": relevance,
                "context_precision": 0.0,
                "citation_quality": 0.0,
                "fact_support": 0.0,
                "overall": overall,
                "quality": _quality_label(overall),
                **gate,
            }

        chunk_corpus = _chunk_corpus(rag_chunks)
        faithfulness = _faithfulness(answer, chunk_corpus)
        relevance = _answer_relevance(question, answer, rag_chunks)
        precision = _context_precision(answer, rag_chunks)
        citation_quality = _citation_quality(question, answer, rag_chunks)
        fact_support = _fact_support(question, answer, rag_chunks)

        overall = round(
            WEIGHT_FAITHFULNESS * faithfulness
            + WEIGHT_RELEVANCE * relevance
            + WEIGHT_PRECISION * precision
            + WEIGHT_CITATION * citation_quality
            + WEIGHT_FACT_SUPPORT * fact_support,
            3,
        )

        gate = _release_gate(
            question=question,
            answer=answer,
            rag_chunks=rag_chunks,
            faithfulness=faithfulness,
            relevance=relevance,
            precision=precision,
            citation_quality=citation_quality,
            fact_support=fact_support,
            overall=overall,
        )

        return {
            "faithfulness": faithfulness,
            "answer_relevance": relevance,
            "context_precision": precision,
            "citation_quality": citation_quality,
            "fact_support": fact_support,
            "overall": overall,
            "quality": _quality_label(overall),
            **gate,
        }

    except Exception as exc:
        print(f"[EVALUATOR] evaluate_answer failed (non-fatal): {exc}")
        return default
