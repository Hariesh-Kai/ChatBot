# backend/llm/query_rewriter.py

import re
from typing import List, Optional, Tuple

#  NEW: Import Lite LLM loader to perform the correction
from backend.llm.loader import get_llm
from backend.llm.model_selector import resolve_model_id
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

NON_INFORMATIVE_MESSAGES = {
    "hi", "hello", "hey", "ok", "okay", "yes", "no", "thanks", "thank you",
}

# ============================================================
#  LLM-BASED CORRECTION (The Fix)
# ============================================================

def _clean_with_llm(text: str) -> str:
    """
    Uses the Lite LLM to fix typos and grammar explicitly.
    Example: "whta is the presure" -> "What is the pressure?"
    """
    try:
        # Load the configured Lite model (with selector fallback safeguards).
        llm_info = get_llm(resolve_model_id("lite"))
        
        prompt = f"""<|start_header_id|>system<|end_header_id|>

You are a query auto-corrector.
Your ONLY job is to fix spelling and grammar errors in the user's text.
- Do NOT answer the question.
- Do NOT explain your changes.
- Do NOT add punctuation if not needed.
- Return ONLY the corrected text.

Example:
Input: whta is presure
Output: What is pressure?

<|eot_id|><|start_header_id|>user<|end_header_id|>

Input: {text}
Output:<|eot_id|><|start_header_id|>assistant<|end_header_id|>
"""

        cleaned = ""

        #  FIX: Handle Streaming Generator & Remove 'echo' arg
        if llm_info["type"] == "gguf":
            # The loader returns a generator, so we must consume it loop-by-loop.
            # We removed 'echo=False' because the loader wrapper doesn't support it.
            stream = llm_info["llm"](prompt, max_tokens=30, stop=["\n"])
            
            full_text = []
            for chunk in stream:
                # Chunk format: {'choices': [{'text': '...'}]}
                content = chunk.get("choices", [{}])[0].get("text", "")
                full_text.append(content)
            
            cleaned = "".join(full_text).strip()

        else:
            # HuggingFace fallback (remains same)
            model = llm_info["model"]
            tokenizer = llm_info["tokenizer"]
            inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
            tokens = model.generate(
                **inputs, 
                max_new_tokens=30, 
                pad_token_id=tokenizer.eos_token_id,
                do_sample=False
            )
            cleaned = tokenizer.decode(tokens[0], skip_special_tokens=True)
            if "Output:" in cleaned:
                cleaned = cleaned.split("Output:")[-1].strip()

        # Safety: If LLM returns nothing or goes crazy, revert to original
        if not cleaned or len(cleaned) > len(text) * 2:
            return text
            
        return cleaned

    except Exception as e:
        print(f"Query correction failed: {e}")
        return text


# ============================================================
# PUBLIC API
# ============================================================

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
    Master rewrite function:
    1. Fix typos (LLM)
    2. Resolve context (History)
    """

    if not question:
        return ""

    # --------------------------------------------------------
    # 1️⃣ STEP 1: FIX TYPOS & GRAMMAR
    # --------------------------------------------------------
    clean_question = _clean_with_llm(question)
    
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

