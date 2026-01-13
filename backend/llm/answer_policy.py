# backend/llm/answer_policy.py

"""
answer_policy.py

Adaptive Answer Decision Engine for KavinBase

PHASE 2 FIXES:
- Greetings NEVER require clarification
- Greetings ALWAYS one-line
- Conversational messages NEVER block generation
- Clarification logic is conservative (no silence)
- ✅ FIX: Explicit "detailed" trigger for follow-up requests
"""

from dataclasses import dataclass
from typing import Optional


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
        "state",
        "list",
        "give",
        "how much",
        "maximum",
        "minimum",
        "how many",
    ))

    is_reasoning = q.startswith((
        "why",
        "how",
        "explain",
        "compare",
        "describe",
        "detail",
    ))

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

    # 🔥 Conversational ALWAYS wins
    if is_conversational:
        verbosity = "one_line"
    elif is_vague:
        verbosity = "one_line"
    elif is_fact_lookup:
        verbosity = "one_line"
    elif is_definition:
        verbosity = "short"
    elif is_reasoning or is_follow_up:
        verbosity = "normal"
    else:
        verbosity = "short"

    # Explicit overrides
    if any(p in q for p in ("in short", "brief", "one line")):
        verbosity = "one_line"

    # ✅ FIX: Explicitly trigger DETAILED mode for elaboration requests
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