# backend/llm/query_rewriter.py

from typing import List

# ============================================================
# QUERY REWRITER
# ------------------------------------------------------------
# Purpose:
# - Rewrite vague follow-up questions into explicit queries
# - Use ONLY user messages
# - NEVER add facts, numbers, or conclusions
#
# STRICT RULES:
# ❌ Do NOT hallucinate
# ❌ Do NOT summarize answers
# ❌ Do NOT expand meaning
# ❌ Do NOT grow query recursively
# ============================================================


VAGUE_PHRASES = {
    "explain more",
    "tell more",
    "tell me more",
    "give more details",
    "more details",
    "elaborate",
    "explain in detail",
    "explain this",
    "what about this",
    "what about that",
}

# Messages that are useless as rewrite context
NON_INFORMATIVE_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "ok",
    "okay",
    "yes",
    "no",
    "thanks",
    "thank you",
}


def is_vague_question(question: str) -> bool:
    """
    Detect whether a question lacks standalone meaning.
    """
    q = question.lower().strip()
    return q in VAGUE_PHRASES or len(q.split()) <= 3


def rewrite_question(
    question: str,
    recent_user_messages: List[str],
) -> str:
    """
    Rewrite a vague question using recent user context.

    Inputs:
    - question: current user question
    - recent_user_messages: previous USER messages (chronological)

    Output:
    - rewritten question (explicit, safe, non-recursive)
    """

    if not question:
        return question

    question_clean = question.strip()

    # --------------------------------------------------------
    # 1️⃣ Not vague → return as-is
    # --------------------------------------------------------
    if not is_vague_question(question_clean):
        return question_clean

    if not recent_user_messages:
        return question_clean

    # --------------------------------------------------------
    # 2️⃣ Find last meaningful user question
    # --------------------------------------------------------
    base_question = None

    for msg in reversed(recent_user_messages):
        msg_clean = msg.strip()
        msg_lower = msg_clean.lower()

        if not msg_clean:
            continue

        if msg_lower in NON_INFORMATIVE_MESSAGES:
            continue

        # Avoid chaining vague questions
        if is_vague_question(msg_clean):
            continue

        base_question = msg_clean
        break

    if not base_question:
        return question_clean

    # --------------------------------------------------------
    # 3️⃣ Guard against recursive growth
    # --------------------------------------------------------
    q_lower = question_clean.lower()
    base_lower = base_question.lower()

    # If base already contains the vague intent, reuse base
    if q_lower in base_lower:
        return base_question

    # If question already references base, do not expand
    if base_lower in q_lower:
        return question_clean

    # --------------------------------------------------------
    # 4️⃣ Safe rewrite (minimal, deterministic)
    # --------------------------------------------------------
    return f"{question_clean} about {base_question}"
