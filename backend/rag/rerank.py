# backend/rag/rerank.py

from typing import List, Dict
from flashrank import Ranker, RerankRequest
from langchain_core.documents import Document

# Lightweight, high-performance reranker
_ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", cache_dir="models")

def rerank_documents(query: str, docs: List[Document], top_k: int = 5) -> List[Document]:
    """
    Re-orders retrieved documents based on relevance to the query.
    Filters out noise.
    """
    if not docs:
        return []

    # Format for FlashRank
    passages = [
        {"id": str(i), "text": d.page_content, "meta": d.metadata}
        for i, d in enumerate(docs)
    ]

    request = RerankRequest(query=query, passages=passages)
    results = _ranker.rerank(request)

    # Reconstruct Documents
    reranked_docs = []
    for res in results[:top_k]:
        # Restore original metadata
        original_meta = res["meta"]
        # Inject score for debugging
        original_meta["rerank_score"] = res["score"]
        
        reranked_docs.append(Document(
            page_content=res["text"],
            metadata=original_meta
        ))

    return reranked_docs