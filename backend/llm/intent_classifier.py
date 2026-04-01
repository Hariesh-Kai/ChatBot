# backend/llm/intent_classifier.py

"""
Zero-shot Intent Classification for Chat UI

Responsibilities:
- Semantic intent detection
- RAG-safe routing
- Confidence-aware fallback

Design goals:
- Fast path for trivial inputs
- ML only when it adds value
- NEVER block RAG
"""

import re
from typing import Literal

from backend.llm.loader import load_intent_classifier
from backend.llm.text_normalizer import token_count


# ============================================================
# PUBLIC INTENT TYPES
# ============================================================

Intent = Literal[
    "greeting",
    "definition",
    "fact_lookup",
    "reasoning",
    "follow_up",
]


# ============================================================
# ZERO-SHOT LABELS
# ============================================================

CANDIDATE_LABELS = [
    "greeting or casual conversation like hello hi good morning",
    "definition question asking for meaning or explanation of a term",
    "technical fact lookup question asking for a specific value from a document",
    "reasoning or explanation question asking why or how something works",
    "follow-up question referring to a previous answer or context",
]


# ============================================================
# CONFIDENCE CONFIG
# ============================================================

MIN_CONFIDENCE = 0.50          # avoid false negatives
MIN_CONFIDENCE_GAP = 0.08      # ambiguity threshold


# ============================================================
# FAST HEURISTICS (NO ML)
# ============================================================

_GREETINGS = {
    "hi", "hello", "hey", "hai",
    "good morning", "good afternoon", "good evening",
}

#  UPDATED: Added "more detail" triggers
_FOLLOW_UP_TRIGGERS = {
    "this", "that", "it", "again", "above", "previous",
    "same", "earlier", "explain more", "tell more",
    "more detail", "detail about", 
}

_FACT_LOOKUP_PATTERNS = (
    "company document id",
    "document id",
    "document number",
    "revision number",
    "current revision",
    "revision no",
    "tag number",
    "basis of design",
)

_SELF_CONTAINED_FACT_RE = re.compile(
    r"\b(company\s+document\s+id|document\s+id|document\s+number|revision\s+number|current\s+revision|tag\s+number)\b",
    re.IGNORECASE,
)

_FOLLOW_UP_REFERENCE_RE = re.compile(
    r"\b(this|that|it|again|above|previous|same|earlier|these|those|them|they)\b",
    re.IGNORECASE,
)


def _looks_fact_lookup(question: str) -> bool:
    q = str(question or "").strip().lower()
    if not q:
        return False

    if any(pattern in q for pattern in _FACT_LOOKUP_PATTERNS):
        return True

    if q.startswith(("what is", "state", "give", "show", "find", "list")) and _SELF_CONTAINED_FACT_RE.search(q):
        return True

    return False


def is_referential_follow_up(question: str) -> bool:
    q = str(question or "").strip()
    if not q:
        return False
    if _looks_fact_lookup(q):
        return False
    return bool(_FOLLOW_UP_REFERENCE_RE.search(q))


def _fast_intent_check(question: str) -> Intent | None:
    """
    Cheap deterministic intent detection.
    Returns intent if confident, otherwise None.
    """
    q = question.strip().lower()

    if not q:
        return "fact_lookup"

    if q.rstrip("!.?") in _GREETINGS:
        return "greeting"

    if _looks_fact_lookup(q):
        return "fact_lookup"

    tokens = token_count(q)

    # Short contextual follow-ups
    # "tell me more details" (4 tokens) should be caught
    if tokens <= 5 and any(t in q for t in _FOLLOW_UP_TRIGGERS) and is_referential_follow_up(q):
        return "follow_up"

    # Single-word confirmations should NOT block RAG
    if tokens <= 2:
        return "fact_lookup"

    return None


# ============================================================
# CORE CLASSIFIER (RAG-SAFE)
# ============================================================

def classify_intent(question: str) -> Intent:
    """
    Classify user intent using heuristics + zero-shot ML.

    CRITICAL RAG RULE:
    - definition / fact_lookup / follow_up MUST NOT block RAG
    """

    if not question or not question.strip():
        return "fact_lookup"

    if _looks_fact_lookup(question):
        return "fact_lookup"

    # --------------------------------------------------------
    # 1️⃣ FAST PATH (NO ML)
    # --------------------------------------------------------
    fast = _fast_intent_check(question)
    if fast:
        return fast

    # --------------------------------------------------------
    # 2️⃣ ZERO-SHOT CLASSIFICATION
    # --------------------------------------------------------
    # Intent detection must NEVER crash the request. If the classifier
    # model isn't available (common with local_files_only=True), fall
    # back to fact_lookup so RAG remains enabled.
    try:
        classifier = load_intent_classifier()
    except Exception as e:
        print(f"[intent] classifier unavailable -> fallback to fact_lookup: {e}")
        return "fact_lookup"

    try:
        result = classifier(
            sequences=question,
            candidate_labels=CANDIDATE_LABELS,
            multi_label=False,
        )
    except Exception as e:
        print(f"[intent] classifier call failed -> fallback to fact_lookup: {e}")
        return "fact_lookup"

    try:
        labels = result.get("labels", []) if isinstance(result, dict) else []
        scores = result.get("scores", []) if isinstance(result, dict) else []

        if not labels or not scores:
            return "fact_lookup"

        top_label = str(labels[0]).lower()
        top_score = float(scores[0]) if len(scores) > 0 else 0.0
        second_score = float(scores[1]) if len(scores) > 1 else 0.0
    except Exception as e:
        print(f"[intent] parse error -> fallback to fact_lookup: {e}")
        return "fact_lookup"

    # --------------------------------------------------------
    # 3️⃣ FOLLOW-UP (HIGHEST PRIORITY)
    # --------------------------------------------------------
    if ("follow-up" in top_label or "previous answer" in top_label) and is_referential_follow_up(question):
        return "follow_up"

    # --------------------------------------------------------
    # 4️⃣ LOW CONFIDENCE → FACT_LOOKUP
    # --------------------------------------------------------
    if top_score < MIN_CONFIDENCE:
        return "fact_lookup"

    # --------------------------------------------------------
    # 5️⃣ AMBIGUOUS → FACT_LOOKUP (RAG ENABLED)
    # --------------------------------------------------------
    if (top_score - second_score) < MIN_CONFIDENCE_GAP:
        return "fact_lookup"

    # --------------------------------------------------------
    # 6️⃣ LABEL MAPPING
    # --------------------------------------------------------
    if "greeting" in top_label or "casual conversation" in top_label:
        return "greeting"

    if "definition" in top_label or "meaning" in top_label:
        return "definition"

    if "fact lookup" in top_label or "specific value" in top_label:
        return "fact_lookup"

    if "reasoning" in top_label or "explanation" in top_label:
        return "reasoning"

    # --------------------------------------------------------
    # 7️⃣ FINAL SAFETY (RAG ON)
    # --------------------------------------------------------
    return "fact_lookup"
