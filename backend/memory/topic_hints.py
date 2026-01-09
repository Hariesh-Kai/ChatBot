# backend/memory/topic_hints.py

"""
Session Topic Hint Extraction

Purpose:
- Extract a short, stable topic hint from a user question
- Used ONLY to improve RAG retrieval across sessions
- NEVER injected into LLM prompts
- NEVER uses chat history or answers
- NEVER calls an LLM

Design Rules:
- Deterministic
- Conservative
- No summarization
- No hallucination
"""

from typing import Optional, List
import re


# ============================================================
# CONFIG
# ============================================================

# Words that should never appear in topic hints
STOPWORDS = {
    "what", "is", "the", "of", "at", "in", "on", "for", "and",
    "to", "does", "do", "did", "explain", "tell", "me", "more",
    "about", "give", "details", "this", "that", "how", "why",
    "when", "where", "which", "who", "are", "was", "were",
    "please", "can", "could", "would", "should",
}

# Max number of words allowed in a topic hint
MAX_TOPIC_WORDS = 5


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _tokenize(text: str) -> List[str]:
    """
    Extract clean tokens from text.
    Keeps alphanumerics and hyphenated identifiers.
    """
    return re.findall(r"[a-zA-Z0-9\-]+", text.lower())


def _filter_keywords(tokens: List[str]) -> List[str]:
    """
    Remove stopwords and very short tokens.
    """
    return [
        t for t in tokens
        if t not in STOPWORDS and len(t) > 2
    ]


# ============================================================
# PUBLIC API
# ============================================================

def extract_topic_hint(question: str) -> Optional[str]:
    """
    Extract a short topic hint from a user question.

    Example:
        Input:
            "What is the water depth at the Agogo-1ST1 well?"

        Output:
            "water depth agogo-1st1 well"

    Guarantees:
    - Returns None if no meaningful hint exists
    - Never invents information
    - Output is stable across calls
    """

    if not question:
        return None

    tokens = _tokenize(question)
    keywords = _filter_keywords(tokens)

    if not keywords:
        return None

    # Limit size to keep retrieval hint soft
    hint = " ".join(keywords[:MAX_TOPIC_WORDS])

    return hint if hint.strip() else None
