# backend/llm/answer_policy.py

"""
answer_policy.py

Adaptive Answer Decision Engine for Chat UI

PHASE 2 FIXES:
- Greetings NEVER require clarification
- Greetings ALWAYS one-line
- Conversational messages NEVER block generation
- Clarification logic is conservative (no silence)
-  FIX: Explicit "detailed" trigger for follow-up requests
"""

import re
from dataclasses import dataclass
from typing import Optional


def _embedded_fact_lookup(q: str) -> bool:
    """
    Factual questions often do not start with 'what is the' (e.g. they start with
    'In Section 4.4...' then ask 'what specific ...'). Without this, verbosity
    stays 'short' and strict_factual is false — models ramble and mix in
    unrelated table/context (common in Lite/GGUF).
    """
    if not q or q.startswith(
        ("why ", "explain ", "compare ", "describe ", "summarize ", "how come ")
    ):
        return False
    # "what specific factor/value/..." (matches user's Section 4.4 style questions)
    if re.search(r"\bwhat\s+specific\b", q):
        return True
    if re.search(r"\bwhich\s+specific\b", q):
        return True
    # "In section X ... what / which ..."
    if re.search(r"\bin\s+section\s+[\w.]+\b", q) and re.search(
        r"\b(what|which)\s+", q
    ):
        return True
    # "regarding X ... what ... is included / are reported"
    if re.search(r"\b(regarding|concerning)\s+", q) and re.search(
        r"\b(what|which)\s+.{0,120}?\b(is|are)\s+(included|reported|mentioned|stated)\b",
        q,
    ):
        return True
    # "according to the revision list ... what specific change ..."
    if "revision list" in q and re.search(r"\b(what|which)\b", q):
        return True
    if any(term in q for term in ("table", "revision history", "row", "column")) and re.search(
        r"\b(what|which|where|when|how much|how many)\b",
        q,
    ):
        return True
    if q.startswith("according to") and re.search(
        r"\b(what|which|where|when|how much|how many)\b.{0,160}\b(table|row|column|change|revision|factor|value|pressure|temperature|material|capacity|flow)\b",
        q,
    ):
        return True
    return False


def _multi_item_fact_lookup(q: str) -> bool:
    """
    Detect factual questions that still need a short multi-item answer instead of a
    single compact line. These were being over-classified as exact one-line lookups,
    which made Lite/Base/Net all feel like they stopped abruptly after the update.
    """
    if not q:
        return False

    if q.startswith(("what are the", "which are the", "what are ", "which are ")):
        return True

    if any(phrase in q for phrase in (
        " range ",
        " ranges ",
        "depth range",
        "depth ranges",
        "water depth",
        "temperature range",
        "pressure range",
        "values for",
    )) and " and " in q:
        return True

    if re.search(r"\b(for|of)\b.{0,120}\band\b.{0,120}\b", q) and any(
        term in q for term in (
            "range",
            "ranges",
            "depth",
            "temperature",
            "pressure",
            "value",
            "values",
            "field",
            "discovery",
        )
    ):
        return True

    return False


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class AnswerStyle:
    """
    EXACT object expected by generate.py
    """
    verbosity: str              # "one_line" | "short" | "normal" | "detailed"
    needs_refinement: bool


@dataclass
class AnswerIntent:
    """
    Internal policy-level intent (NOT sent to the LLM)
    """
    use_rag: bool
    use_deliberation: bool
    verbosity: str
    is_follow_up: bool
    needs_context: bool
    needs_clarification: bool
    needs_refinement: bool
    strict_factual: bool


# ============================================================
# CORE POLICY ENGINE
# ============================================================

def infer_answer_policy(
    question: str,
    previous_question: Optional[str] = None,
    previous_answer: Optional[str] = None,
) -> AnswerIntent:
    """
    Infers HOW the system should answer (not WHAT to answer).
    """

    q = (question or "").strip().lower()
    word_count = len(q.split())

    # --------------------------------------------------------
    # 0️⃣ CONVERSATIONAL DETECTION (🔥 EARLY & AUTHORITATIVE)
    # --------------------------------------------------------

    conversational_set = {
        "hi", "hello", "hey",
        "thanks", "thank you",
        "ok", "okay", "cool"
    }

    is_conversational = q in conversational_set

    # --------------------------------------------------------
    # 1️⃣ FOLLOW-UP DETECTION
    # --------------------------------------------------------

    follow_up_triggers = (
        "explain again",
        "why",
        "how",
        "clarify",
        "again",
        "above",
        "previous",
    )

    is_follow_up = bool(
        previous_question and any(t in q for t in follow_up_triggers)
    )

    # --------------------------------------------------------
    # 2️⃣ QUESTION TYPE CLASSIFICATION
    # --------------------------------------------------------

    is_definition = q.startswith((
        "what is",
        "define",
        "meaning of",
    ))

    is_fact_lookup = q.startswith((
        "what is the",
        "what are the",
        "what categories are",
        "which categories are",
        "what categories",
        "which categories",
        "at what",
        "at which",
        "state",
        "list",
        "give",
        "how much",
        "maximum",
        "minimum",
        "how many",
        "where",
    )) or _embedded_fact_lookup(q)

    is_reasoning = bool(
        q.startswith(("why", "explain", "compare", "describe", "detail"))
        or (
            q.startswith("how")
            and not q.startswith(("how many", "how much"))
        )
    )

    is_vague = word_count <= 3 and not is_conversational

    # --------------------------------------------------------
    # 3️⃣ CONTEXT DEPENDENCY
    # --------------------------------------------------------

    vague_terms = ("this", "that", "it", "same", "above", "previous")
    needs_context = (
        not is_conversational
        and (any(v in q for v in vague_terms) or is_follow_up)
    )

    # --------------------------------------------------------
    # 4️⃣ VERBOSITY CONTROL (UPDATED)
    # --------------------------------------------------------

    multi_item_fact = _multi_item_fact_lookup(q)

    # 🔥 Conversational ALWAYS wins
    if is_conversational:
        verbosity = "one_line"
    elif is_reasoning or is_follow_up:
        verbosity = "normal"
    elif is_definition:
        verbosity = "short"
    elif is_fact_lookup and not multi_item_fact:
        verbosity = "one_line"
    elif is_fact_lookup and multi_item_fact:
        verbosity = "short"
    elif is_vague:
        verbosity = "short"
    else:
        verbosity = "short"

    # Explicit overrides
    if any(p in q for p in ("in short", "brief", "one line")):
        verbosity = "one_line"

    #  FIX: Explicitly trigger DETAILED mode for elaboration requests
    # This prevents response_policy.py from cutting off the answer.
    if any(p in q for p in (
        "explain fully", "detailed", "in detail", 
        "more detail", "more info", "elaborate",
        "not getting any knowledge", "expand on", "tell me more"
    )):
        verbosity = "detailed"

    # --------------------------------------------------------
    # 5️⃣ RAG USAGE
    # --------------------------------------------------------

    use_rag = bool(
        not is_conversational
        and (is_fact_lookup or needs_context or is_reasoning)
    )

    if is_definition and not needs_context:
        use_rag = False

    # --------------------------------------------------------
    # 6️⃣ STRICT FACTUAL MODE
    # --------------------------------------------------------

    strict_factual = bool(
        is_fact_lookup
        and not is_reasoning
        and not is_conversational
    )

    # --------------------------------------------------------
    # 7️⃣ DELIBERATION DECISION
    # --------------------------------------------------------

    use_deliberation = bool(
        not is_conversational
        and not is_vague
        and (
            is_reasoning
            or (is_fact_lookup and needs_context)
        )
    )

    # --------------------------------------------------------
    # 8️⃣ CLARIFICATION (🔥 FIXED: NEVER FOR GREETINGS)
    # --------------------------------------------------------

    needs_clarification = False

    if not is_conversational:
        if is_vague and not previous_question:
            needs_clarification = True

        if needs_context and not previous_answer:
            needs_clarification = True

    # --------------------------------------------------------
    # 9️⃣ REFINEMENT DECISION
    # --------------------------------------------------------

    needs_refinement = bool(
        not strict_factual
        and not is_conversational
        and verbosity in ("normal", "detailed")
    )

    # --------------------------------------------------------
    # 🔟 RETURN
    # --------------------------------------------------------

    return AnswerIntent(
        use_rag=use_rag,
        use_deliberation=use_deliberation,
        verbosity=verbosity,
        is_follow_up=is_follow_up,
        needs_context=needs_context,
        needs_clarification=needs_clarification,
        needs_refinement=needs_refinement,
        strict_factual=strict_factual,
    )


# ============================================================
# COMPATIBILITY LAYER (USED BY generate.py)
# ============================================================

def decide_answer_style(
    question: str,
    context_chunks=None,
) -> AnswerStyle:
    """
    Thin wrapper for generation.
    """

    intent = infer_answer_policy(question)

    return AnswerStyle(
        verbosity=intent.verbosity,
        needs_refinement=intent.needs_refinement,
    )
