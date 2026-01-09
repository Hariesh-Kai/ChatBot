# backend/rag/confidence.py

"""
Answer Confidence Scoring

Purpose:
- Compute a reliability/confidence score for a RAG answer
- Uses retrieval signals ONLY
- NEVER calls an LLM
- NEVER inspects answer text
- NEVER touches chat memory

Confidence ≠ correctness
Confidence = strength + consistency of retrieved evidence
"""

from typing import List, Dict


# ============================================================
# CONFIG
# ============================================================

# Weight tuning (balanced & conservative)
WEIGHT_CHUNK_COUNT = 0.30
WEIGHT_SIMILARITY = 0.50
WEIGHT_REDUNDANCY = 0.20

# Thresholds
HIGH_CONFIDENCE = 0.75
MEDIUM_CONFIDENCE = 0.45

# Safety limits
MAX_EFFECTIVE_CHUNKS = 5
MIN_SIMILARITY_FLOOR = 0.15


# ============================================================
# PUBLIC API
# ============================================================

def compute_confidence(
    rag_chunks: List[Dict],
    similarity_scores: List[float],
) -> Dict[str, object]:
    """
    Compute confidence score for an answer.

    Inputs:
    - rag_chunks: list of retrieved chunks (dicts)
    - similarity_scores: similarity score per chunk (0–1, higher = better)

    Output:
    {
        "confidence": float (0–1),
        "level": "high" | "medium" | "low"
    }
    """

    if not rag_chunks or not similarity_scores:
        return _low_confidence()

    # --------------------------------------------------------
    # Safety: align lengths
    # --------------------------------------------------------
    n = min(len(rag_chunks), len(similarity_scores))
    if n <= 0:
        return _low_confidence()

    chunks = rag_chunks[:n]
    scores = _sanitize_scores(similarity_scores[:n])

    # 🔥 Guard: similarity collapsed to floor only
    if not scores or all(s <= MIN_SIMILARITY_FLOOR for s in scores):
        return _low_confidence()

    # --------------------------------------------------------
    # Individual signals
    # --------------------------------------------------------
    chunk_score = _chunk_count_score(len(chunks))
    similarity_score = _similarity_strength(scores)
    redundancy_score = _redundancy_score(chunks)

    confidence = (
        WEIGHT_CHUNK_COUNT * chunk_score +
        WEIGHT_SIMILARITY * similarity_score +
        WEIGHT_REDUNDANCY * redundancy_score
    )

    # 🔥 Absolute safety clamp
    confidence = float(confidence)
    if confidence != confidence:  # NaN guard
        return _low_confidence()

    confidence = max(0.0, min(1.0, confidence))
    confidence = round(confidence, 2)

    return {
        "confidence": confidence,
        "level": _confidence_level(confidence),
    }


# ============================================================
# INTERNAL SCORING FUNCTIONS
# ============================================================

def _sanitize_scores(scores: List[float]) -> List[float]:
    """
    Clamp similarity scores into a safe range.
    """
    safe: List[float] = []

    for s in scores:
        try:
            s = float(s)
        except Exception:
            continue

        s = max(0.0, min(1.0, s))
        if s >= MIN_SIMILARITY_FLOOR:
            safe.append(s)

    return safe


def _chunk_count_score(count: int) -> float:
    """
    Confidence grows with evidence count,
    but saturates quickly to avoid inflation.
    """
    if count <= 0:
        return 0.0

    effective = min(count, MAX_EFFECTIVE_CHUNKS)
    return effective / MAX_EFFECTIVE_CHUNKS


def _similarity_strength(scores: List[float]) -> float:
    """
    Measures strength of retrieval using
    top-weighted similarity instead of raw average.
    """
    if not scores:
        return 0.0

    scores = sorted(scores, reverse=True)

    if len(scores) == 1:
        return scores[0]

    top = scores[0]
    secondary = scores[1]

    return round((0.7 * top + 0.3 * secondary), 2)


def _redundancy_score(chunks: List[Dict]) -> float:
    """
    Measures corroboration across document sections.
    """
    sections = [c.get("section") for c in chunks if c.get("section")]

    if not sections:
        return 0.4

    unique = set(sections)

    if len(unique) == 1:
        return 0.6

    ratio = len(unique) / len(sections)

    if ratio <= 0.4:
        return 1.0
    if ratio <= 0.6:
        return 0.8
    if ratio <= 0.8:
        return 0.6
    return 0.4


def _confidence_level(value: float) -> str:
    """
    Map numeric confidence to label.
    """
    if value >= HIGH_CONFIDENCE:
        return "high"
    if value >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def _low_confidence() -> Dict[str, object]:
    """
    Fallback for no / weak evidence.
    """
    return {
        "confidence": 0.15,
        "level": "low",
    }
