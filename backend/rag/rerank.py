# backend/rag/rerank.py

import logging
from pathlib import Path
from typing import Dict, List, Optional

from flashrank import Ranker, RerankRequest
from langchain_core.documents import Document
from backend.llm.model_config_store import HF_CACHE_DIR

logger = logging.getLogger(__name__)

_RERANK_MODEL_NAME = "ms-marco-MiniLM-L-12-v2"
_RERANK_CACHE_DIR = str(Path(HF_CACHE_DIR) / "flashrank")
_ranker: Optional[Ranker] = None
_ranker_unavailable = False

def _normalize_chunk_type(metadata: Optional[Dict]) -> str:
    raw = str((metadata or {}).get("chunk_type") or (metadata or {}).get("type") or "text").strip().lower()
    if raw in {"parent", "text", "child", "image"}:
        return raw
    return "text"


def _scoring_multiplier(metadata: Optional[Dict]) -> float:
    try:
        return max(float((metadata or {}).get("scoring_multiplier") or 1.0), 0.0)
    except Exception:
        return 1.0


def _fallback_rank(
    docs: List[Document],
    *,
    top_k: int,
    query_profile: Optional[Dict] = None,
) -> List[Document]:
    del query_profile
    rescored = []
    for doc in docs:
        meta = dict(doc.metadata or {})
        meta["final_score"] = round(float(meta.get("retrieval_score") or 0.0), 8)
        rescored.append(Document(page_content=doc.page_content, metadata=meta))

    rescored.sort(
        key=lambda doc: (
            float((doc.metadata or {}).get("final_score") or 0.0),
            1 if _normalize_chunk_type(doc.metadata) == "parent" else 0,
            len(doc.page_content or ""),
        ),
        reverse=True,
    )
    return rescored[:top_k]


def _get_ranker() -> Optional[Ranker]:
    """
    Lazily initialize FlashRank so backend imports do not fail when the
    reranker weights have not been downloaded yet or network access is blocked.
    """
    global _ranker, _ranker_unavailable

    if _ranker is not None:
        return _ranker

    if _ranker_unavailable:
        return None

    try:
        _ranker = Ranker(model_name=_RERANK_MODEL_NAME, cache_dir=_RERANK_CACHE_DIR)
        return _ranker
    except Exception as exc:
        _ranker_unavailable = True
        logger.warning(
            "FlashRank is unavailable; falling back to existing retrieval order. Error: %s",
            exc,
        )
        return None


def rerank_documents(
    query: str,
    docs: List[Document],
    top_k: int = 5,
    query_profile: Optional[Dict] = None,
) -> List[Document]:
    """
    Re-orders retrieved documents based on relevance to the query.

    The extra structural boost fixes table-heavy retrieval where a strong
    row-level lexical match could otherwise outrank the correct full table.
    """
    if not docs:
        return []

    ranker = _get_ranker()
    if ranker is None:
        return _fallback_rank(
            docs,
            top_k=top_k,
            query_profile=query_profile,
        )

    passages = [
        {"id": str(i), "text": d.page_content, "meta": d.metadata}
        for i, d in enumerate(docs)
    ]

    try:
        request = RerankRequest(query=query, passages=passages)
        results = ranker.rerank(request)
    except Exception as exc:
        logger.warning(
            "FlashRank rerank failed; returning the current candidate order. Error: %s",
            exc,
        )
        return _fallback_rank(
            docs,
            top_k=top_k,
            query_profile=query_profile,
        )

    rescored = []
    for res in results:
        original_meta = dict(res.get("meta") or {})
        rerank_score = float(res.get("score") or 0.0)
        retrieval_score = float(original_meta.get("retrieval_score") or 0.0)
        scoring_multiplier = _scoring_multiplier(original_meta)
        original_meta["rerank_score"] = rerank_score
        original_meta["rerank_multiplier"] = round(scoring_multiplier, 4)
        original_meta["final_score"] = round(
            max(rerank_score * scoring_multiplier, retrieval_score),
            8,
        )

        rescored.append(
            Document(
                page_content=res["text"],
                metadata=original_meta,
            )
        )

    rescored.sort(
        key=lambda doc: (
            float((doc.metadata or {}).get("final_score") or 0.0),
            1 if _normalize_chunk_type(doc.metadata) == "parent" else 0,
            len(doc.page_content or ""),
        ),
        reverse=True,
    )
    return rescored[:top_k]
