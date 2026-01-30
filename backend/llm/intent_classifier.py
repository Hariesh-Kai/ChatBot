# backend/llm/intent_classifier.py

"""
Zero-shot Intent Classification for KavinBase

Responsibilities:
- Semantic intent detection
- RAG-safe routing
- Confidence-aware fallback

Design goals:
- Fast path for trivial inputs
- ML only when it adds value
- NEVER block RAG
"""

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


def _fast_intent_check(question: str) -> Intent | None:
    """
    Cheap deterministic intent detection.
    Returns intent if confident, otherwise None.
    """
    q = question.strip().lower()

    if not q:
        return "fact_lookup"

    if any(q.startswith(g) for g in _GREETINGS):
        return "greeting"

    tokens = token_count(q)

    # Short contextual follow-ups
    # "tell me more details" (4 tokens) should be caught
    if tokens <= 5 and any(t in q for t in _FOLLOW_UP_TRIGGERS):
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

    # --------------------------------------------------------
    # 1️⃣ FAST PATH (NO ML)
    # --------------------------------------------------------
    fast = _fast_intent_check(question)
    if fast:
        return fast

    # --------------------------------------------------------
    # 2️⃣ ZERO-SHOT CLASSIFICATION
    # --------------------------------------------------------
    classifier = load_intent_classifier()

    result = classifier(
        sequences=question,
        candidate_labels=CANDIDATE_LABELS,
        multi_label=False,
    )

    labels = result.get("labels", [])
    scores = result.get("scores", [])

    if not labels or not scores:
        return "fact_lookup"

    top_label = labels[0].lower()
    top_score = float(scores[0])
    second_score = float(scores[1]) if len(scores) > 1 else 0.0

    # --------------------------------------------------------
    # 3️⃣ FOLLOW-UP (HIGHEST PRIORITY)
    # --------------------------------------------------------
    if "follow-up" in top_label or "previous answer" in top_label:
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