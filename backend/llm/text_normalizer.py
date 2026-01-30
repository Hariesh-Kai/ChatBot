# backend/llm/text_normalizer.py
"""
Text Normalizer for KavinBase

Purpose:
- Normalize raw user text BEFORE intent, rewrite, and generation
- Applied globally (RAG and non-RAG paths)
- Clean and normalize raw user input before intent classification
- Improve zero-shot and rule-based intent accuracy
- Keep behavior deterministic and lightweight

NO intent logic
NO ML
NO side effects
"""

import re
from typing import Optional


# ------------------------------------------------------------
# CORE NORMALIZATION
# ------------------------------------------------------------
def normalize_text(text: Optional[str]) -> str:
    """
    Normalizes user input text for intent classification.

    Steps:
    1. Handle None safely
    2. Lowercase
    3. Trim whitespace
    4. Collapse repeated characters (hiiii -> hi)
    5. Normalize punctuation
    6. Collapse extra spaces
    """

    if text is None:
        return ""

    # Convert to string defensively
    text = str(text)

    # 1️⃣ Lowercase
    text = text.lower()

    # 2️⃣ Strip leading / trailing whitespace
    text = text.strip()

    # 3️⃣ Collapse repeated characters (>=3 → 1)
    # Example: "hiiii" -> "hi", "helloooo" -> "hello"
    text = re.sub(r"(.)\1{2,}", r"\1", text)

    # 4️⃣ Normalize repeated punctuation
    # "!!!" -> "!", "??" -> "?"
    text = re.sub(r"[!?]{2,}", lambda m: m.group(0)[0], text)

    # 5️⃣ Remove excessive punctuation clutter
    # Keep only basic sentence punctuation
    text = re.sub(r"[^\w\s\?\!\.]", " ", text)

    # 6️⃣ Collapse multiple spaces
    text = re.sub(r"\s{2,}", " ", text)

    return text.strip()


# ------------------------------------------------------------
# LIGHT TOKEN COUNT (UTILITY)
# ------------------------------------------------------------
def token_count(text: str) -> int:
    """
    Very lightweight token count.
    Used ONLY for short-prompt detection.
    """
    if not text:
        return 0
    return len(text.split())
