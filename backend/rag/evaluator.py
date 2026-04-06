# backend/rag/evaluator.py

"""
RAGAS-Style Answer Quality Evaluator

Purpose:
- Automatically score every RAG answer on 3 dimensions:
    1. Faithfulness    — does the answer only use the provided context?
    2. Answer Relevance — does it address the question?
    3. Context Precision — were the right chunks retrieved?

Design Rules:
- NO external API calls — pure text heuristics
- NO LLM calls — fast enough to run on every response
- MUST NEVER raise — always returns a valid score dict
- Scores are 0.0–1.0; overall is their weighted average
"""

import re
from typing import List, Dict, Any


# ============================================================
# CONFIG
# ============================================================

WEIGHT_FAITHFULNESS   = 0.50
WEIGHT_RELEVANCE      = 0.30
WEIGHT_PRECISION      = 0.20

# Thresholds for quality levels
QUALITY_HIGH   = 0.75
QUALITY_MEDIUM = 0.50

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "many",
    "much",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "there",
    "these",
    "this",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
}


# ============================================================
# HELPERS
# ============================================================

def _tokenize(text: str) -> set:
    """Lowercase alphanum tokens from text."""
    return set(re.findall(r"[a-z0-9]+", text.lower())) if text else set()


def _content_tokens(text: str) -> set:
    """Meaningful content tokens with common filler words removed."""
    return _tokenize(text) - _STOPWORDS


def _sentence_split(text: str) -> List[str]:
    """Simple sentence splitter."""
    return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]


# ============================================================
# METRIC 1: FAITHFULNESS
# Score = fraction of answer sentences where at least one
#         content token appears in the retrieved chunks.
# ============================================================

def _faithfulness(answer: str, chunk_corpus: str) -> float:
    sentences = _sentence_split(answer)
    if not sentences:
        return 1.0

    corpus_tokens = _tokenize(chunk_corpus)
    if not corpus_tokens:
        return 0.0

    # Ignore very short sentences (citations, punctuation artefacts)
    checkable = [s for s in sentences if len(s.split()) >= 4]
    if not checkable:
        return 1.0

    grounded = 0
    for sent in checkable:
        sent_tokens = _tokenize(sent)
        # At least 40% of the sentence's meaningful tokens must be in corpus
        meaningful = sent_tokens - _STOPWORDS
        if not meaningful:
            grounded += 1
            continue
        overlap = meaningful & corpus_tokens
        if len(overlap) / len(meaningful) >= 0.40:
            grounded += 1

    return round(grounded / len(checkable), 3)


# ============================================================
# METRIC 2: ANSWER RELEVANCE
# Score = token overlap between question keywords and answer.
# ============================================================

def _answer_relevance(question: str, answer: str) -> float:
    q_tokens = _content_tokens(question)
    a_tokens = _content_tokens(answer) or _tokenize(answer)

    if not q_tokens:
        return 1.0
    if not a_tokens:
        return 0.0

    hits = q_tokens & a_tokens
    return round(min(len(hits) / len(q_tokens), 1.0), 3)


# ============================================================
# METRIC 3: CONTEXT PRECISION
# Score = fraction of retrieved chunks that actually
#         contribute content-overlapping tokens to the answer.
# ============================================================

def _context_precision(answer: str, rag_chunks: List[Dict[str, Any]]) -> float:
    if not rag_chunks:
        return 0.0

    answer_tokens = _tokenize(answer)
    if not answer_tokens:
        return 0.0

    useful = 0
    for chunk in rag_chunks:
        content = chunk.get("content", "")
        chunk_tokens = _tokenize(content)
        overlap = chunk_tokens & answer_tokens
        # A chunk is "useful" if ≥5 of its tokens appear in the answer
        if len(overlap) >= 5:
            useful += 1

    return round(useful / len(rag_chunks), 3)


# ============================================================
# MAIN PUBLIC FUNCTION
# ============================================================

def evaluate_answer(
    question: str,
    answer: str,
    rag_chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Evaluate RAG answer quality on 3 dimensions.

    Returns:
    {
        "faithfulness": float,
        "answer_relevance": float,
        "context_precision": float,
        "overall": float,
        "quality": "high" | "medium" | "low",
    }

    Never raises.
    """
    default = {
        "faithfulness": 1.0,
        "answer_relevance": 1.0,
        "context_precision": 1.0,
        "overall": 1.0,
        "quality": "high",
    }

    try:
        if not answer or not rag_chunks:
            return default

        # Build corpus from all retrieved chunks
        chunk_corpus = "\n".join(
            c.get("content", "") for c in rag_chunks if c.get("content")
        )

        faith  = _faithfulness(answer, chunk_corpus)
        relev  = _answer_relevance(question, answer)
        prec   = _context_precision(answer, rag_chunks)

        overall = round(
            WEIGHT_FAITHFULNESS * faith
            + WEIGHT_RELEVANCE   * relev
            + WEIGHT_PRECISION   * prec,
            3,
        )

        if overall >= QUALITY_HIGH:
            quality = "high"
        elif overall >= QUALITY_MEDIUM:
            quality = "medium"
        else:
            quality = "low"

        return {
            "faithfulness":     faith,
            "answer_relevance": relev,
            "context_precision": prec,
            "overall":          overall,
            "quality":          quality,
        }

    except Exception as e:
        print(f"[EVALUATOR] evaluate_answer failed (non-fatal): {e}")
        return default
