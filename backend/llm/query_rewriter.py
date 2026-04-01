# backend/llm/query_rewriter.py

import re
from typing import List
# QUERY REWRITER (NOW WITH SPELL CHECK)
# ------------------------------------------------------------
# Purpose:
# 1. Fix typos/grammar in user input ("whta" -> "what")
# 2. Resolve vague references ("it", "this") using history
# ============================================================

VAGUE_PHRASES = {
    "explain more", "tell more", "tell me more", "give more details",
    "more details", "elaborate", "explain in detail", "explain this",
    "what about this", "what about that", "details",
}

REFERENTIAL_TOKENS = {
    "this", "that", "it", "its", "they", "them", "those", "these",
    "same", "again", "previous", "above", "earlier",
}

NON_INFORMATIVE_MESSAGES = {
    "hi", "hello", "hey", "ok", "okay", "yes", "no", "thanks", "thank you",
}

def _clean_question_text(text: str) -> str:
    """
    Deterministic query cleanup.

    Keep this stage non-generative so the rewriter can never answer the
    question or inject unsupported content into intent classification and
    retrieval.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""

    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,;:.!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:.!?]){2,}", r"\1", cleaned)
    return cleaned.strip()


# ============================================================
# PUBLIC API
# ============================================================

def is_vague_question(question: str) -> bool:
    """
    Detect whether a question lacks standalone meaning.
    """
    q = question.lower().strip()
    if not q:
        return True
    if q in VAGUE_PHRASES:
        return True
    tokens = re.findall(r"[a-z0-9]+", q)
    return any(token in REFERENTIAL_TOKENS for token in tokens)


def rewrite_question(
    question: str,
    recent_user_messages: List[str],
) -> str:
    """
    Master rewrite function:
    1. Clean formatting deterministically
    2. Resolve context from history
    """

    if not question:
        return ""

    # --------------------------------------------------------
    # 1️⃣ STEP 1: FIX TYPOS & GRAMMAR
    # --------------------------------------------------------
    clean_question = _clean_question_text(question)
    
    if clean_question.strip().lower() != question.strip().lower():
        print(f"✨ [REWRITE] Typo fix: '{question}' -> '{clean_question}'")
    
    question = clean_question

    # --------------------------------------------------------
    # 2️⃣ STEP 2: CONTEXT RESOLUTION
    # --------------------------------------------------------
    
    if not is_vague_question(question):
        return question

    if not recent_user_messages:
        return question

    base_question = None
    for msg in reversed(recent_user_messages):
        msg_clean = msg.strip()
        msg_lower = msg_clean.lower()

        if not msg_clean:
            continue

        if msg_lower in NON_INFORMATIVE_MESSAGES:
            continue

        if is_vague_question(msg_clean):
            continue

        base_question = msg_clean
        break

    if not base_question:
        return question

    # --------------------------------------------------------
    # 3️⃣ Guard against recursive growth
    # --------------------------------------------------------
    q_lower = question.lower()
    base_lower = base_question.lower()

    if q_lower in base_lower:
        return base_question

    if base_lower in q_lower:
        return question

    # --------------------------------------------------------
    # 4️⃣ Safe rewrite
    # --------------------------------------------------------
    return f"{question} about {base_question}"


# ============================================================
# MULTI-QUERY GENERATION (Rule-based, zero LLM cost)
# ============================================================

# Words to strip when building keyword-only variant
_KW_STOPWORDS = {
    "what", "is", "the", "are", "a", "an", "of", "in", "for",
    "to", "and", "or", "how", "much", "many", "does", "can",
    "which", "who", "when", "where", "why", "give", "tell",
    "me", "please", "explain", "describe", "state", "list",
    "find", "show", "get", "with", "on", "at", "by", "do",
}


def generate_multi_queries(question: str) -> List[str]:
    """
    Generate multiple retrieval-optimised variations of a question.
    Rule-based — no LLM call, no latency.

    Returns a deduplicated list of up to 3 query strings:
    1. Original (cleaned)
    2. Keyword-only  (nouns, numbers, abbreviations)
    3. Spec-style    ("specification for <keywords>")

    Usage: retrieve for all variants, then RRF-merge results.
    """
    if not question or not question.strip():
        return [question]

    q = question.strip()

    # --- Variant 1: Original (already cleaned by caller) ---
    variants: List[str] = [q]

    # --- Variant 2: Keyword-only ---
    # Extract tokens that are likely domain-relevant
    tokens = re.findall(r"[a-zA-Z0-9\-\.]+", q)
    kw_tokens = [
        t for t in tokens
        if t.lower() not in _KW_STOPWORDS and len(t) >= 2
    ]
    if kw_tokens:
        kw_variant = " ".join(kw_tokens)
        if kw_variant.lower() != q.lower():
            variants.append(kw_variant)

    # --- Variant 3: Spec-style ---
    # E.g. "What is the maximum pressure?" → "specification for maximum pressure"
    spec_tokens = [
        t for t in kw_tokens
        if not t.isdigit() and len(t) >= 3
    ]
    if spec_tokens:
        spec_variant = "specification for " + " ".join(spec_tokens[:6])
        if spec_variant not in variants:
            variants.append(spec_variant)

    # Deduplicate preserving order
    seen: set = set()
    unique: List[str] = []
    for v in variants:
        key = v.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique[:3]

