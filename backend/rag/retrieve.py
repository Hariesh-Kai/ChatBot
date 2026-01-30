# backend/rag/retrieve.py

import json #  Added json import to parse bbox strings
from typing import List, Dict, Any, Optional

from langchain_core.documents import Document
from langchain_postgres import PGVector

from backend.rag.keyword_search import keyword_search
from backend.rag.rerank import rerank_documents

# ============================================================
# CONFIG
# ============================================================

RAG_MAX_K = 8
RAG_CANDIDATE_K = 25 

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
        # Is this a child?
        if doc.metadata.get("type") == "child" and doc.metadata.get("parent_id"):
            parent_ids_to_fetch.add(doc.metadata["parent_id"])
        else:
            cid = doc.metadata.get("chunk_id")
            if not cid:
                # 🔥 HARD SKIP — identity must exist
                continue
            final_docs_map[cid] = doc

        

    if not parent_ids_to_fetch:
        return list(final_docs_map.values())

    # Fetch Parents from DB
    try:
        for pid in parent_ids_to_fetch:
            # We search for the specific parent document by exact ID match
            results = vector_store.similarity_search(
                "ignored", # query ignored with exact filter usually
                k=1,
                filter={"doc_id": pid, "type": "parent"} 
            )
            if results:
                parent = results[0]
                final_docs_map[pid] = parent
                
    except Exception as e:
        print(f"Parent lookup failed: {e}")
        # Fallback: Just use the children if parents fail
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
    force_detailed: bool = False,
    extra_context_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    The CORE RAG Retrieval Pipeline.
    
    Steps:
    1. Hybrid Search (Vector + Keyword)
    2. Deduplication
    3. Reranking (FlashRank)
    4. Parent Document Resolution
    5. Formatting & BBox Parsing
    """

    # 1. Setup Filters
    metadata_filter = {
        "company_document_id": company_document_id,
        "revision_number": str(revision_number), 
    }

    # 2. Vector Search (High Recall)
    # Fetch more candidates if detailed mode is requested
    search_k = RAG_CANDIDATE_K + 10 if force_detailed else RAG_CANDIDATE_K
    
    vector_docs = vector_store.similarity_search(
        question,
        k=search_k, 
        filter=metadata_filter,
    )

    # 3. Keyword Search (Precision)
    keyword_docs = keyword_search(
        question=question,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        limit=10, 
    )

    # 4. Deduplicate (Union of Vector + Keyword)
    unique_map = {}
    for d in vector_docs + keyword_docs:
        cid = d.metadata.get("chunk_id")
        if not cid:
            continue
        unique_map[cid] = d
    candidates = list(unique_map.values())

    # 5. Reranking
    if candidates:
        final_k = RAG_MAX_K + 2 if force_detailed else RAG_MAX_K
        reranked_docs = rerank_documents(question, candidates, top_k=final_k)
    else:
        reranked_docs = []

    # 6. Parent Resolution (Context Expansion)
    # Resolves full tables if a specific row was matched
    final_docs = resolve_parent_chunks(
        reranked_docs, 
        vector_store, 
        vector_store.collection_name
    )

    # 7. Format Output for LLM & Frontend
    rag_chunks = []
    for d in final_docs:        
        cid = d.metadata.get("chunk_id")
        if not cid:
            # 🔥 Skip corrupted chunks — never invent identity
            continue

        #  FIX: Safely parse BBOX for Frontend
        # The DB might store it as a JSON string (e.g., "[[10, 20, 100, 200]]")
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
            "chunk_type": d.metadata.get("type"),
            "score": d.metadata.get("rerank_score", 0.0),
            
            #  Enhanced Metadata for Frontend "View Source"
            "metadata": {
                "page_number": int(d.metadata.get("page_number", 1)),
                "bbox": bbox_data, # Frontend highlighting relies on this!
                "source_file": d.metadata.get("source_file", ""),
                "section": d.metadata.get("section", ""),
            }
        })

    return rag_chunks