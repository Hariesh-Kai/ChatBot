# backend/rag/optimized_retrieve.py

"""
Optimized Retrieval Integration Layer
Combines parallel processing, connection pooling, and advanced caching
"""

import time
from typing import List, Dict, Any, Optional
from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings

from backend.rag.parallel_retrieve import retrieve_rag_context_parallel
from backend.rag.connection_pool import create_optimized_vector_store
from backend.rag.semantic_cache import get_semantic_cache
from backend.rag.retrieve import (
    retrieve_rag_context,
    MAX_CONTEXT_CHUNKS,
)


class OptimizedRetriever:
    """
    High-performance retrieval system that combines:
    1. Parallel lane execution
    2. Connection pooling
    3. Multi-level caching
    4. Adaptive strategies
    """
    
    def __init__(
        self,
        embedding_model: HuggingFaceEmbeddings,
        connection_string: str,
        collection_name: str = "rag_documents",
        enable_parallel: bool = True,
        enable_caching: bool = True,
        cache_similarity_threshold: float = 0.85,
        max_workers: int = 4,
        pool_size: int = 10,
    ):
        self.embedding_model = embedding_model
        self.connection_string = connection_string
        self.collection_name = collection_name
        self.enable_parallel = enable_parallel
        self.enable_caching = enable_caching
        self.max_workers = max_workers
        
        # Initialize optimized vector store with connection pooling
        self.vector_store = create_optimized_vector_store(
            connection_string=connection_string,
            collection_name=collection_name,
            embedding_model=embedding_model,
            pool_size=pool_size,
            max_overflow=pool_size * 2,
        )
        
        # Initialize semantic cache
        if self.enable_caching:
            self.cache = get_semantic_cache(embedding_model)
            self.cache_similarity_threshold = cache_similarity_threshold
        else:
            self.cache = None
    
    def retrieve(
        self,
        question: str,
        company_document_id: str,
        revision_number: str,
        rag_mode: str = "balanced",
        force_detailed: bool = False,
        enable_hybrid_retrieval: bool = True,
        use_cache: Optional[bool] = None,
        parallel: Optional[bool] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Optimized retrieval with automatic fallbacks and performance monitoring
        """
        start_time = time.time()
        
        # Determine cache and parallel settings
        use_cache = use_cache if use_cache is not None else self.enable_caching
        parallel = parallel if parallel is not None else self.enable_parallel
        
        # Try cache first
        if use_cache and self.cache:
            cached_results = self.cache.get(
                question=question,
                company_document_id=company_document_id,
                revision_number=revision_number,
                rag_mode=rag_mode
            )
            
            if cached_results:
                cache_latency = (time.time() - start_time) * 1000
                print(f"[OPTIMIZED] Cache hit in {cache_latency:.0f}ms")
                return cached_results[:MAX_CONTEXT_CHUNKS]
        
        # Perform retrieval
        try:
            if parallel:
                # Use parallel retrieval
                results = retrieve_rag_context_parallel(
                    question=question,
                    vector_store=self.vector_store,
                    company_document_id=company_document_id,
                    revision_number=revision_number,
                    rag_mode=rag_mode,
                    force_detailed=force_detailed,
                    enable_hybrid_retrieval=enable_hybrid_retrieval,
                    max_workers=self.max_workers,
                )
            else:
                # Fallback to sequential retrieval
                results = retrieve_rag_context(
                    question=question,
                    vector_store=self.vector_store,
                    company_document_id=company_document_id,
                    revision_number=revision_number,
                    rag_mode=rag_mode,
                    force_detailed=force_detailed,
                    enable_hybrid_retrieval=enable_hybrid_retrieval,
                )
            
            # Cache results
            if use_cache and self.cache and results:
                self.cache.set(
                    query=question,
                    chunks=results,
                    company_document_id=company_document_id,
                    revision_number=revision_number,
                    rag_mode=rag_mode,
                )
            
            retrieval_latency = (time.time() - start_time) * 1000
            print(f"[OPTIMIZED] Retrieval completed in {retrieval_latency:.0f}ms "
                  f"({'parallel' if parallel else 'sequential'}, {len(results)} chunks)")
            
            return results[:MAX_CONTEXT_CHUNKS]
            
        except Exception as e:
            print(f"[OPTIMIZED] Retrieval failed: {e}")
            # Emergency fallback to basic similarity search
            try:
                fallback_results = self._emergency_fallback(
                    question=question,
                    company_document_id=company_document_id,
                    revision_number=revision_number,
                )
                
                if use_cache and self.cache and fallback_results:
                    self.cache.set(
                        query=question,
                        chunks=fallback_results,
                        company_document_id=company_document_id,
                        revision_number=revision_number,
                        rag_mode=rag_mode,
                        ttl=300,  # Shorter TTL for fallback results
                    )
                
                fallback_latency = (time.time() - start_time) * 1000
                print(f"[OPTIMIZED] Emergency fallback in {fallback_latency:.0f}ms")
                return fallback_results
                
            except Exception as fallback_error:
                print(f"[OPTIMIZED] Emergency fallback also failed: {fallback_error}")
                return []
    
    def _emergency_fallback(
        self,
        question: str,
        company_document_id: str,
        revision_number: str,
    ) -> List[Dict[str, Any]]:
        """
        Emergency fallback using basic similarity search
        """
        try:
            metadata_filter = {
                "company_document_id": company_document_id,
                "revision_number": revision_number,
            }
            
            docs = self.vector_store.similarity_search(
                question,
                k=MAX_CONTEXT_CHUNKS,
                filter=metadata_filter,
            )
            
            results = []
            for doc in docs:
                metadata = doc.metadata or {}
                chunk_id = str(metadata.get("chunk_id") or "").strip()
                if chunk_id:
                    results.append({
                        "id": chunk_id,
                        "content": doc.page_content,
                        "section": metadata.get("section"),
                        "chunk_type": metadata.get("chunk_type", "text"),
                        "score": metadata.get("similarity_distance", 0.0),
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
            
            return results[:MAX_CONTEXT_CHUNKS]
            
        except Exception as e:
            print(f"[OPTIMIZED] Emergency fallback error: {e}")
            return []
    
    def invalidate_document_cache(
        self,
        company_document_id: str,
        revision_number: str
    ):
        """Invalidate cache for specific document"""
        if self.cache:
            self.cache.invalidate_document(company_document_id, revision_number)
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance and cache statistics"""
        stats = {
            "parallel_enabled": self.enable_parallel,
            "cache_enabled": self.enable_caching,
            "max_workers": self.max_workers,
        }
        
        if self.cache:
            cache_stats = self.cache.get_cache_stats()
            stats.update(cache_stats)
        
        return stats


# Global optimized retriever instance
_global_retriever: Optional[OptimizedRetriever] = None


def get_optimized_retriever(
    embedding_model: HuggingFaceEmbeddings,
    connection_string: str,
    collection_name: str = "rag_documents",
    **kwargs
) -> OptimizedRetriever:
    """
    Get or create global optimized retriever instance
    """
    global _global_retriever
    
    if _global_retriever is None:
        _global_retriever = OptimizedRetriever(
            embedding_model=embedding_model,
            connection_string=connection_string,
            collection_name=collection_name,
            **kwargs
        )
    
    return _global_retriever


def retrieve_rag_context_optimized(
    question: str,
    company_document_id: str,
    revision_number: str,
    rag_mode: str = "balanced",
    force_detailed: bool = False,
    enable_hybrid_retrieval: bool = True,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Drop-in replacement for retrieve_rag_context with optimizations
    """
    from backend.api.chat import embedding_model, DB_CONNECTION, COLLECTION_NAME
    
    if not embedding_model:
        print("[OPTIMIZED] Embedding model not available, falling back to original")
        from backend.rag.retrieve import retrieve_rag_context
        return retrieve_rag_context(
            question=question,
            vector_store=None,  # Will be created in original function
            company_document_id=company_document_id,
            revision_number=revision_number,
            rag_mode=rag_mode,
            force_detailed=force_detailed,
            enable_hybrid_retrieval=enable_hybrid_retrieval,
        )
    
    retriever = get_optimized_retriever(
        embedding_model=embedding_model,
        connection_string=DB_CONNECTION,
        collection_name=COLLECTION_NAME,
    )
    
    return retriever.retrieve(
        question=question,
        company_document_id=company_document_id,
        revision_number=revision_number,
        rag_mode=rag_mode,
        force_detailed=force_detailed,
        enable_hybrid_retrieval=enable_hybrid_retrieval,
        **kwargs
    )
