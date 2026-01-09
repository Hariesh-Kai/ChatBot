"""
response_policy.py

Final Response Controller for KavinBase

Responsibilities:
- Enforce verbosity rules
- Trim unnecessary explanation
- Clean LLM artifacts
- Prevent over-answering
- Apply human-like restraint to emojis
- Ensure user-friendly output

NO LLM calls
NO RAG logic
"""

import re
from typing import Literal


# ============================================================
# CONFIG
# ============================================================

MAX_SENTENCES = {
    "one_line": 1,
    "short": 3,
    "normal": 6,
    "detailed": 12,
}

MAX_CHARS = {
    "one_line": 200,
    "short": 600,
    "normal": 1200,
    "detailed": 3000,
}

# Unicode emoji range (safe, broad)
EMOJI_PATTERN = re.compile(r"[\U00010000-\U0010ffff]")
MAX_EMOJI_RATIO = 0.15  # Max 15% of visible characters


# ============================================================
# CORE RESPONSE POLICY
# ============================================================

def apply_response_policy(
    text: str,
    verbosity: Literal["one_line", "short", "normal", "detailed"],
) -> str:
    """
    Applies strict formatting, verbosity control,
    and human-like emoji restraint to LLM output.

    STREAM-SAFE:
    - Never injects replacement text
    - Never rewrites meaning after streaming
    """

    if not text:
        return ""

    # --------------------------------------------------------
    # 1️⃣ HARD CLEAN (prompt leakage & junk)
    # --------------------------------------------------------

    garbage_patterns = [
        r"<\|assistant\|>",
        r"<\|system\|>",
        r"<\|user\|>",
        r"^\s*assistant\s*:\s*",
        r"^\s*user\s*:\s*",
        r"^\s*final answer\s*:\s*",
        r"^\s*response\s*:\s*",
        r"^\s*revised answer\s*:\s*",
        r"^\s*draft answer\s*:\s*",
        r"you are a .* assistant",
    ]

    for pattern in garbage_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)

    # --------------------------------------------------------
    # 2️⃣ REMOVE PROMPT ECHO (ONLY IF CLEARLY A QUESTION)
    # --------------------------------------------------------

    text = re.sub(
        r"^\s*(what|why|how|when|where|who)\b[^?.!]*\?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # --------------------------------------------------------
    # 3️⃣ NORMALIZE WHITESPACE
    # --------------------------------------------------------

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()

    # --------------------------------------------------------
    # 4️⃣ SENTENCE-LEVEL CONTROL
    # --------------------------------------------------------

    # Safer sentence split (avoids breaking decimals/abbreviations)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    max_sent = MAX_SENTENCES.get(verbosity, 3)
    sentences = sentences[:max_sent]
    text = " ".join(sentences)

    # --------------------------------------------------------
    # 5️⃣ CHARACTER LIMIT SAFETY
    # --------------------------------------------------------

    max_chars = MAX_CHARS.get(verbosity, 600)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"

    # --------------------------------------------------------
    # 6️⃣ EMOJI RESTRAINT (ALLOW, BUT HUMAN-LIKE)
    # --------------------------------------------------------

    emojis = EMOJI_PATTERN.findall(text)

    if emojis:
        max_allowed = max(1, int(len(text) * MAX_EMOJI_RATIO))

        if len(emojis) > max_allowed:
            trimmed_chars = []
            emoji_count = 0

            for ch in text:
                if EMOJI_PATTERN.match(ch):
                    emoji_count += 1
                    if emoji_count > max_allowed:
                        continue
                trimmed_chars.append(ch)

            text = "".join(trimmed_chars)

    # --------------------------------------------------------
    # 7️⃣ FINAL SANITY CHECK (STREAM-SAFE)
    # --------------------------------------------------------

    # Do NOT inject fallback content here.
    # Preserve short but meaningful answers.
    if len(text.strip()) < 8:
        return text.strip()

    return text.strip()


# ============================================================
# QUICK HEURISTIC SHORTENER (OPTIONAL)
# ============================================================

def force_short_answer(text: str) -> str:
    """
    Emergency shortener if LLM goes wild.
    NON-STREAMING USE ONLY.
    """
    if not text:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return sentences[0].strip()
