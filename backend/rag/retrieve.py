# backend/rag/retrieve.py

import json
from typing import List, Dict, Any, Optional

from langchain_core.documents import Document
from langchain_postgres import PGVector

from backend.rag.keyword_search import keyword_search
from backend.rag.rerank import rerank_documents
from backend.rag.mode_profiles import normalize_rag_mode, get_retrieval_profile

# ============================================================
# CONFIG
# ============================================================

RAG_MAX_K = 8
RAG_CANDIDATE_K = 25

# Reciprocal Rank Fusion constant - higher k = less aggressive fusion
RRF_K = 60


# ============================================================
# HELPER: RECIPROCAL RANK FUSION
# ============================================================

def _reciprocal_rank_fusion(
    vector_docs: List[Document],
    keyword_results: List[tuple],  # List of (Document, score)
    *,
    rrf_k: int = RRF_K,
) -> List[Document]:
    """
    Fuses vector search results and keyword search results using RRF.

    RRF score = 1/(k + rank_vector) + 1/(k + rank_keyword)

    - Docs that rank well in BOTH searches rise to the top.
    - Docs only in one search still get a partial score.
    - The keyword ts_rank score is used to break ties between keyword candidates.
    """
    fused_scores: Dict[str, float] = {}
    doc_map: Dict[str, Document] = {}

    # Score from vector results (ranked by position)
    for rank, doc in enumerate(vector_docs):
        cid = doc.metadata.get("chunk_id")
        if not cid:
            continue
        doc_map[cid] = doc
        fused_scores[cid] = fused_scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)

    # Score from keyword results (ranked by ts_rank score then position)
    # Sort keyword results by their ts_rank score descending first
    sorted_kw = sorted(keyword_results, key=lambda x: x[1], reverse=True)
    for rank, (doc, kw_score) in enumerate(sorted_kw):
        cid = doc.metadata.get("chunk_id")
        if not cid:
            continue
        if cid not in doc_map:
            doc_map[cid] = doc
        # Add RRF component + small bonus from ts_rank score
        fused_scores[cid] = fused_scores.get(cid, 0.0) + (
            1.0 / (rrf_k + rank + 1) + 0.01 * kw_score
        )

    # Sort by fused score descending
    sorted_ids = sorted(fused_scores.keys(), key=lambda cid: fused_scores[cid], reverse=True)

    result = []
    for cid in sorted_ids:
        doc = doc_map[cid]
        # Inject fused score for downstream use
        doc.metadata["rrf_score"] = round(fused_scores[cid], 4)
        result.append(doc)

    return result


# ============================================================
# HELPER: PARENT RESOLUTION
# ============================================================

def resolve_parent_chunks(
    child_docs: List[Document],
    vector_store: PGVector,
    collection_name: str = "rag_documents"
) -> List[Document]:
    """
    For every Child chunk (row), find its Parent chunk (full table).
    If a chunk is already a Parent or Text, keep it.
    """
    parent_ids_to_fetch = set()
    final_docs_map = {}

    for doc in child_docs:
        meta = doc.metadata or {}
        chunk_type = meta.get("chunk_type") or meta.get("type")
        if chunk_type == "child" and meta.get("parent_id"):
            parent_ids_to_fetch.add(meta["parent_id"])
        else:
            cid = meta.get("chunk_id")
            if not cid:
                continue
            final_docs_map[cid] = doc

    if not parent_ids_to_fetch:
        return list(final_docs_map.values())

    # Fetch Parents from DB
    try:
        for pid in parent_ids_to_fetch:
            results = vector_store.similarity_search(
                "ignored",
                k=1,
                filter={"doc_id": pid, "chunk_type": "parent"}
            )
            if results:
                parent = results[0]
                final_docs_map[pid] = parent

    except Exception as e:
        print(f"Parent lookup failed: {e}")
        return child_docs

    return list(final_docs_map.values())


# ============================================================
# MAIN RETRIEVAL FUNCTION
# ============================================================

def retrieve_rag_context(
    question: str,
    vector_store: PGVector,
    company_document_id: str,
    revision_number: str,
    rag_mode: str = "balanced",
    force_detailed: bool = False,
    extra_context_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    The CORE RAG Retrieval Pipeline.

    Steps:
    1. Vector Search (high recall)
    2. BM25-style Keyword Search (ts_rank scored)
    3. Reciprocal Rank Fusion (RRF) - merges and scores both
    4. Reranking (FlashRank cross-encoder)
    5. Parent Document Resolution
    6. Formatting & BBox Parsing
    """

    # 1. Setup Filters
    metadata_filter = {
        "company_document_id": company_document_id,
        "revision_number": str(revision_number),
    }

    resolved_rag_mode = normalize_rag_mode(rag_mode)
    profile = get_retrieval_profile(
        resolved_rag_mode,
        force_detailed=force_detailed,
    )

    # 2. Vector Search (High Recall)
    search_k = int(profile.get("candidate_k", RAG_CANDIDATE_K))

    vector_docs = vector_store.similarity_search(
        question,
        k=search_k,
        filter=metadata_filter,
    )

    # 3. BM25-style Keyword Search (scored tuples)
    keyword_results = []
    if bool(profile.get("use_keyword", True)):
        keyword_results = keyword_search(
            question=question,
            vector_store=vector_store,
            metadata_filter=metadata_filter,
            limit=int(profile.get("keyword_limit", 12)),
        )

    # 4. RRF Fusion - replaces naive union deduplication
    if keyword_results:
        candidates = _reciprocal_rank_fusion(
            vector_docs,
            keyword_results,
            rrf_k=int(profile.get("rrf_k", RRF_K)),
        )
    else:
        candidates = list(vector_docs)
        rrf_k = int(profile.get("rrf_k", RRF_K))
        for rank, doc in enumerate(candidates):
            doc.metadata["rrf_score"] = round(1.0 / (rrf_k + rank + 1), 4)

    # 5. Reranking (FlashRank cross-encoder re-scores top candidates)
    if candidates:
        final_k = int(profile.get("final_k", RAG_MAX_K))
        if bool(profile.get("use_rerank", True)):
            reranked_docs = rerank_documents(question, candidates, top_k=final_k)
        else:
            reranked_docs = candidates[:final_k]
    else:
        reranked_docs = []

    # 6. Parent Resolution (Context Expansion for table rows)
    if bool(profile.get("use_parent_resolution", True)):
        final_docs = resolve_parent_chunks(
            reranked_docs,
            vector_store,
            vector_store.collection_name
        )
    else:
        final_docs = reranked_docs

    # 7. Format Output for LLM & Frontend
    rag_chunks = []
    for d in final_docs:
        cid = d.metadata.get("chunk_id")
        if not cid:
            continue

        # Safely parse BBOX for Frontend
        bbox_raw = d.metadata.get("bbox")
        bbox_data = []

        try:
            if isinstance(bbox_raw, str) and bbox_raw.strip().startswith("["):
                bbox_data = json.loads(bbox_raw)
            elif isinstance(bbox_raw, list):
                bbox_data = bbox_raw
        except Exception:
            bbox_data = []

        rag_chunks.append({
            "id": cid,
            "content": d.page_content,
            "section": d.metadata.get("section"),
            "chunk_type": d.metadata.get("chunk_type") or d.metadata.get("type"),
            "score": d.metadata.get("rerank_score", d.metadata.get("rrf_score", 0.0)),

            "metadata": {
                "page_number": int(d.metadata.get("page_number", 1)),
                "bbox": bbox_data,
                "source_file": d.metadata.get("source_file", ""),
                "section": d.metadata.get("section", ""),
            }
        })

    return rag_chunks


# ============================================================
# CONVERSATION-AWARE QUERY AUGMENTATION (Phase 4)
# ============================================================

import re as _re

_CONV_STOPWORDS = {
    "what", "is", "the", "are", "a", "an", "of", "in", "for", "how",
    "much", "many", "does", "can", "which", "who", "when", "where",
    "i", "me", "my", "this", "that", "it", "its", "give", "tell",
    "show", "find", "get", "please", "explain", "describe",
}

def _extract_context_keywords(messages: List[Dict]) -> List[str]:
    """Extract domain-relevant keywords from recent conversation turns."""
    keywords: list = []
    for msg in messages:
        content = msg.get("content", "")
        tokens = _re.findall(r"[a-zA-Z0-9\-\.]{3,}", content)
        for t in tokens:
            if t.lower() not in _CONV_STOPWORDS and t not in keywords:
                keywords.append(t)
    return keywords[:12]  # max 12 context keywords


def augment_query_with_context(
    question: str,
    recent_messages: List[Dict],
) -> str:
    """
    Augment a vague follow-up question with conversation context keywords.

    Examples:
        Q: "what about its material?"
        Context keywords: ["pressure", "valve", "DN200", "P-101A"]
        Result: "what about its material? [context: pressure valve DN200 P-101A]"

    Only augments if the question is short / ambiguous (< 8 tokens).
    Always returns at least the original question.
    Never raises.
    """
    try:
        if not recent_messages:
            return question

        q_tokens = question.strip().split()
        if len(q_tokens) >= 8:
            # Question is specific enough - no augmentation needed
            return question

        ctx_keywords = _extract_context_keywords(recent_messages)
        if not ctx_keywords:
            return question

        augmented = f"{question} [context: {' '.join(ctx_keywords[:6])}]"
        print(f"[CONV-AWARE] Augmented short query: {augmented[:100]}")
        return augmented

    except Exception as e:
        print(f"[CONV-AWARE] augment_query_with_context failed (non-fatal): {e}")
        return question
