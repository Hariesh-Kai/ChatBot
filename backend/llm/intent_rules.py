# backend/llm/intent_rules.py
"""
Rule-based Intent Detection for KavinBase

Purpose:
- Catch ultra-simple linguistic cases
- Provide fast deterministic routing
- NEVER block document / RAG questions

NO ML
NO LLM
NO RAG
"""

from typing import Optional, Literal
from backend.llm.text_normalizer import token_count


Intent = Literal[
    "greeting",
    "confirmation",
    "conversation",
    "unknown",
]


# ------------------------------------------------------------
# STATIC VOCABULARY
# ------------------------------------------------------------

GREETINGS = {
    "hi",
    "hello",
    "hey",
    "hai",
    "hola",
    "good morning",
    "good afternoon",
    "good evening",
}

CONFIRMATIONS = {
    "ok",
    "okay",
    "yes",
    "yeah",
    "yep",
    "no",
    "nah",
    "thanks",
    "thank you",
    "cool",
    "fine",
}


# ------------------------------------------------------------
# RULE-BASED INTENT DETECTOR (RAG-SAFE)
# ------------------------------------------------------------
def detect_rule_intent(text: str) -> Optional[Intent]:
    """
    Detect intent using deterministic linguistic rules.

    CRITICAL RULE:
    - Rules must NEVER block document questions
    """

    if not text:
        return "unknown"

    text = text.strip().lower()
    tokens = token_count(text)

    # --------------------------------------------------------
    # 1️⃣ Exact greetings (ONLY exact matches)
    # --------------------------------------------------------
    if text in GREETINGS:
        return "greeting"

    # --------------------------------------------------------
    # 2️⃣ Exact confirmations (ONLY exact matches)
    # --------------------------------------------------------
    if text in CONFIRMATIONS:
        return "confirmation"

    # --------------------------------------------------------
    # 3️⃣ Ultra-short conversational fillers
    # --------------------------------------------------------
    if tokens <= 2:
        # Allow ML to decide if it looks technical
        if any(ch.isdigit() for ch in text):
            return None

        if len(text) >= 4:
            return None

        return "conversation"

    # --------------------------------------------------------
    # 4️⃣ Greeting phrases at sentence start
    # --------------------------------------------------------
    for g in GREETINGS:
        if text.startswith(f"{g} "):
            return "greeting"

    # --------------------------------------------------------
    # Let ML classifier decide
    # --------------------------------------------------------
    return None
