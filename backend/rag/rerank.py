# backend/rag/rerank.py

import logging
from pathlib import Path
from typing import List, Optional

from flashrank import Ranker, RerankRequest
from langchain_core.documents import Document
from backend.llm.model_config_store import HF_CACHE_DIR

logger = logging.getLogger(__name__)

_RERANK_MODEL_NAME = "ms-marco-MiniLM-L-12-v2"
_RERANK_CACHE_DIR = str(Path(HF_CACHE_DIR) / "flashrank")
_ranker: Optional[Ranker] = None
_ranker_unavailable = False


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


def rerank_documents(query: str, docs: List[Document], top_k: int = 5) -> List[Document]:
    """
    Re-orders retrieved documents based on relevance to the query.
    Falls back to the incoming order if FlashRank is unavailable.
    """
    if not docs:
        return []

    ranker = _get_ranker()
    if ranker is None:
        return docs[:top_k]

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
        return docs[:top_k]

    reranked_docs = []
    for res in results[:top_k]:
        original_meta = dict(res.get("meta") or {})
        original_meta["rerank_score"] = res.get("score")

        reranked_docs.append(
            Document(
                page_content=res["text"],
                metadata=original_meta,
            )
        )

    return reranked_docs
