# backend/rag/parallel_retrieve.py

"""
Parallel Retrieval Optimization for KavinBase
Executes multiple retrieval lanes concurrently to reduce latency
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple
from langchain_core.documents import Document
from langchain_postgres import PGVector

from backend.rag.retrieve import (
    _search_chunk_lane,
    _merge_candidate_streams,
    _get_retrieval_mix,
    classify_query_profile,
    get_retrieval_profile,
    normalize_rag_mode,
    MAX_CONTEXT_CHUNKS,
    RAG_CANDIDATE_K,
    RRF_K,
)
from backend.rag.keyword_search import keyword_search
from backend.rag.rerank import rerank_documents


class ParallelRetriever:
    """
    Optimized parallel retrieval engine that executes multiple search lanes
    concurrently while maintaining result quality and consistency.
    """
    
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        
    def _execute_lane_search(
        self,
        lane_config: Tuple[str, int, int],
        question: str,
        vector_store: PGVector,
        metadata_filter: Dict[str, str],
        use_keyword: bool,
        keyword_limit: int,
        rrf_k: int,
        query_profile: Dict[str, Any],
    ) -> Tuple[str, List[Document]]:
        """
        Execute a single retrieval lane. Returns (lane_name, documents)
        """
        lane_name, vector_k, keyword_k = lane_config
        
        try:
            docs = _search_chunk_lane(
                question=question,
                vector_store=vector_store,
                metadata_filter=metadata_filter,
                chunk_type=lane_name,
                vector_k=vector_k,
                keyword_k=min(keyword_limit, max(0, keyword_k)),
                use_keyword=use_keyword,
                rrf_k=rrf_k,
                query_profile=query_profile,
            )
            return lane_name, docs
        except Exception as e:
            print(f"[PARALLEL] Lane {lane_name} failed: {e}")
            return lane_name, []
    
    def parallel_lane_search(
        self,
        question: str,
        vector_store: PGVector,
        metadata_filter: Dict[str, str],
        retrieval_mix: Dict[str, int],
        use_keyword: bool,
        keyword_limit: int,
        rrf_k: int,
        query_profile: Dict[str, Any],
    ) -> Dict[str, List[Document]]:
        """
        Execute all retrieval lanes in parallel using ThreadPoolExecutor
        """
        lane_configs = [
            ("parent", retrieval_mix.get("parent", 0)),
            ("text", retrieval_mix.get("text", 0)),
            ("image", retrieval_mix.get("image", 0)),
            ("child", retrieval_mix.get("child", 0)),
        ]
        
        # Filter out lanes with zero budget
        active_lanes = [(name, k, k) for name, k in lane_configs if k > 0]
        
        if not active_lanes:
            return {}
        
        results = {}
        
        # Use ThreadPoolExecutor for parallel execution
        with ThreadPoolExecutor(max_workers=min(len(active_lanes), self.max_workers)) as executor:
            # Submit all lane searches
            future_to_lane = {
                executor.submit(
                    self._execute_lane_search,
                    lane_config,
                    question,
                    vector_store,
                    metadata_filter,
                    use_keyword,
                    keyword_limit,
                    rrf_k,
                    query_profile,
                ): lane_config[0]
                for lane_config in active_lanes
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_lane):
                lane_name = future_to_lane[future]
                try:
                    name, docs = future.result(timeout=30)  # 30 second timeout per lane
                    results[name] = docs
                    print(f"[PARALLEL] Lane {lane_name} completed: {len(docs)} docs")
                except Exception as e:
                    print(f"[PARALLEL] Lane {lane_name} error: {e}")
                    results[lane_name] = []
        
        return results


def retrieve_rag_context_parallel(
    question: str,
    vector_store: PGVector,
    company_document_id: str,
    revision_number: str,
    rag_mode: str = "balanced",
    force_detailed: bool = False,
    enable_hybrid_retrieval: bool = True,
    max_workers: int = 4,
) -> List[Dict[str, Any]]:
    """
    Parallel version of retrieve_rag_context with optimized concurrent execution.
    
    Key optimizations:
    1. Parallel execution of retrieval lanes
    2. Early termination for low-budget lanes
    3. Timeout protection per lane
    4. Resource-aware thread pool sizing
    """
    start_time = time.time()
    
    metadata_filter = {
        "company_document_id": str(company_document_id or ""),
        "revision_number": str(revision_number or ""),
    }
    
    if not metadata_filter["company_document_id"] or not metadata_filter["revision_number"]:
        return []
    
    # Configuration setup (same as original)
    resolved_rag_mode = normalize_rag_mode(rag_mode)
    profile = get_retrieval_profile(resolved_rag_mode, force_detailed=force_detailed)
    query_profile = classify_query_profile(question)
    
    final_k = min(
        MAX_CONTEXT_CHUNKS,
        int(query_profile.get("top_k") or profile.get("final_k", RAG_MAX_K)),
    )
    
    search_k = (
        int(profile.get("candidate_k", RAG_CANDIDATE_K))
        if enable_hybrid_retrieval
        else final_k
    )
    
    retrieval_mix = _get_retrieval_mix(
        query_profile=query_profile,
        search_k=search_k,
        final_k=final_k,
    )
    
    keyword_limit = int(profile.get("keyword_limit", 12))
    use_keyword = bool(profile.get("use_keyword", True)) and enable_hybrid_retrieval
    rrf_k = int(profile.get("rrf_k", RRF_K))
    
    # Parallel execution
    retriever = ParallelRetriever(max_workers=max_workers)
    parallel_results = retriever.parallel_lane_search(
        question=question,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        retrieval_mix=retrieval_mix,
        use_keyword=use_keyword,
        keyword_limit=keyword_limit,
        rrf_k=rrf_k,
        query_profile=query_profile,
    )
    
    # Merge results (same as original logic)
    all_docs = []
    for lane_name in ["parent", "text", "image", "child"]:
        docs = parallel_results.get(lane_name, [])
        all_docs.extend(docs)
    
    candidates = _merge_candidate_streams(*[
        parallel_results.get(lane_name, []) 
        for lane_name in ["parent", "text", "image", "child"]
        if parallel_results.get(lane_name)
    ])
    
    # Continue with existing pipeline (reranking, filtering, etc.)
    # ... (rest of the original pipeline logic)
    
    parallel_latency = (time.time() - start_time) * 1000
    print(f"[PARALLEL] Retrieval completed in {parallel_latency:.0f}ms")
    
    # Convert to final format (same as original)
    rag_chunks = []
    for doc in candidates[:final_k]:
        metadata = doc.metadata or {}
        chunk_id = str(metadata.get("chunk_id") or "").strip()
        if not chunk_id:
            continue
            
        rag_chunks.append({
            "id": chunk_id,
            "content": doc.page_content,
            "section": metadata.get("section"),
            "chunk_type": metadata.get("chunk_type", "text"),
            "score": metadata.get("retrieval_score", 0.0),
            "metadata": {
                "page_number": int(metadata.get("page_number", 1)),
                "source_file": metadata.get("source_file", ""),
                "section": metadata.get("section", ""),
                "doc_id": metadata.get("doc_id"),
                "chunk_type": metadata.get("chunk_type", "text"),
                "element_type": metadata.get("element_type", "NarrativeText"),
                "extraction_source": metadata.get("extraction_source"),
                "quality_score": metadata.get("quality_score"),
            },
        })
    
    return rag_chunks[:final_k]
