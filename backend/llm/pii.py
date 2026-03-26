# backend/llm/pii.py

"""
PII Detection & Masking for Chat UI

Purpose:
- Detect personally identifiable information in questions before
  they are stored in chat memory or sent to the LLM
- Optionally mask PII before storage
- Configurable by sensitivity level

Design Rules:
- Regex-based, zero external dependency
- MUST NEVER raise — always returns a valid result
- Masking preserves string length to avoid breaking downstream parsing
- Detection only by default; masking is opt-in
"""

import re
from typing import List, Dict, Any, Tuple


# ============================================================
# CONFIG
# ============================================================

# Set to True to mask PII in chat memory storage
MASK_IN_MEMORY = False

# Sensitivity: "low" (only obvious PII), "medium", "high" (aggressive)
SENSITIVITY = "medium"


# ============================================================
# PII PATTERNS
# Each entry: (label, compiled_regex)
# ============================================================

_PII_PATTERNS_LOW = [
    ("EMAIL",     re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b")),
    ("PHONE_INT", re.compile(r"\+?\d[\d\s\-().]{8,}\d")),
    ("CREDIT_CARD",re.compile(r"\b(?:\d[ -]?){13,16}\b")),
]

_PII_PATTERNS_MEDIUM = _PII_PATTERNS_LOW + [
    ("NID",       re.compile(r"\b[A-Z]{1,2}\d{6,10}\b")),         # National ID-style
    ("PASSPORT",  re.compile(r"\b[A-Z]{1,2}\d{7,9}\b")),
    ("IP_ADDR",   re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
]

_PII_PATTERNS_HIGH = _PII_PATTERNS_MEDIUM + [
    # Full name heuristic: 2–3 capitalized words
    ("FULL_NAME",  re.compile(r"\b[A-Z][a-z]{1,20}\s[A-Z][a-z]{1,20}(?:\s[A-Z][a-z]{1,20})?\b")),
    ("DATE_OF_BIRTH", re.compile(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{2}[/-]\d{2})\b"
    )),
]

_PATTERN_MAP = {
    "low":    _PII_PATTERNS_LOW,
    "medium": _PII_PATTERNS_MEDIUM,
    "high":   _PII_PATTERNS_HIGH,
}


# ============================================================
# DETECTION
# ============================================================

def detect_pii(text: str, sensitivity: str = SENSITIVITY) -> List[Dict[str, Any]]:
    """
    Detect PII in text.

    Returns list of findings:
    [{"label": "EMAIL", "value": "foo@bar.com", "start": 5, "end": 16}, ...]

    Never raises.
    """
    if not text:
        return []

    patterns = _PATTERN_MAP.get(sensitivity, _PII_PATTERNS_MEDIUM)
    findings: List[Dict[str, Any]] = []
    seen_spans = set()

    try:
        for label, pattern in patterns:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                findings.append({
                    "label": label,
                    "value": m.group(),
                    "start": m.start(),
                    "end":   m.end(),
                })
        # Sort by position
        findings.sort(key=lambda x: x["start"])
    except Exception as e:
        print(f"[PII] detect_pii error (non-fatal): {e}")

    return findings


def has_pii(text: str, sensitivity: str = SENSITIVITY) -> bool:
    """Quick boolean check — True if any PII detected."""
    return bool(detect_pii(text, sensitivity))


# ============================================================
# MASKING
# ============================================================

def mask_pii(text: str, sensitivity: str = SENSITIVITY) -> Tuple[str, List[Dict]]:
    """
    Replace detected PII with [LABEL] placeholders.

    Returns: (masked_text, findings)
    Never raises.
    """
    if not text:
        return text, []

    try:
        findings = detect_pii(text, sensitivity)
        if not findings:
            return text, []

        # Replace from right to left to preserve positions
        masked = text
        for f in reversed(findings):
            placeholder = f"[{f['label']}]"
            masked = masked[:f["start"]] + placeholder + masked[f["end"]:]

        return masked, findings

    except Exception as e:
        print(f"[PII] mask_pii error (non-fatal): {e}")
        return text, []


# ============================================================
# SAFE WRAPPER FOR CHAT STORAGE
# ============================================================

def safe_for_storage(text: str) -> str:
    """
    If MASK_IN_MEMORY is enabled, returns masked text.
    Otherwise returns original text unchanged.
    Always safe to call — never raises.
    """
    if not MASK_IN_MEMORY:
        return text
    try:
        masked, findings = mask_pii(text)
        if findings:
            labels = [f["label"] for f in findings]
            print(f"[PII] Masked {len(findings)} finding(s) before storage: {labels}")
        return masked
    except Exception:
        return text
