# backend/rag/semantic_cache.py

"""
Advanced Semantic Caching for Retrieval Optimization
Implements multi-level caching with semantic similarity and TTL management
"""

import hashlib
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from threading import Lock
import redis
from langchain_huggingface import HuggingFaceEmbeddings

# Redis connection for distributed caching
try:
    redis_client = redis.Redis(
        host='localhost',
        port=6379,
        db=0,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True
    )
    redis_client.ping()  # Test connection
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False
    redis_client = None
    print("[CACHE] Redis not available, using in-memory cache only")


@dataclass
class CacheEntry:
    """Cache entry with metadata and TTL"""
    query: str
    chunks: List[Dict[str, Any]]
    timestamp: float
    ttl: int  # Time to live in seconds
    hit_count: int = 0
    semantic_hash: str = ""
    
    def is_expired(self) -> bool:
        return time.time() > (self.timestamp + self.ttl)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CacheEntry':
        return cls(**data)


class SemanticCache:
    """
    Multi-level semantic cache with similarity matching and TTL management
    """
    
    def __init__(self, embedding_model: HuggingFaceEmbeddings, similarity_threshold: float = 0.85):
        self.embedding_model = embedding_model
        self.similarity_threshold = similarity_threshold
        self.memory_cache: Dict[str, CacheEntry] = {}
        self.cache_lock = Lock()
        
        # Cache configuration
        self.default_ttl = 3600  # 1 hour
        self.max_memory_entries = 1000
        self.semantic_prefix = "semantic_cache:"
        
    def _normalize_query(self, query: str) -> str:
        return " ".join(str(query or "").lower().split())

    def _generate_semantic_hash(self, query: str) -> str:
        """
        Generate a stable exact-match hash for query lookup.

        Important:
        - Do NOT embed here. Exact cache hits should be O(1) and cheap.
        - Semantic similarity, when needed, is handled separately.
        """
        normalized = self._normalize_query(query)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _is_semantic_candidate(self, query1: str, query2: str) -> bool:
        """
        Cheap lexical guard before expensive embedding similarity.
        """
        q1_terms = set(self._normalize_query(query1).split())
        q2_terms = set(self._normalize_query(query2).split())
        if not q1_terms or not q2_terms:
            return False
        overlap = q1_terms.intersection(q2_terms)
        return len(overlap) >= max(1, min(len(q1_terms), len(q2_terms)) // 4)
    
    def _calculate_similarity(self, query1: str, query2: str) -> float:
        """Calculate semantic similarity between two queries"""
        try:
            emb1 = self.embedding_model.embed_query(query1)
            emb2 = self.embedding_model.embed_query(query2)
            
            # Cosine similarity
            dot_product = sum(a * b for a, b in zip(emb1, emb2))
            norm1 = sum(a * a for a in emb1) ** 0.5
            norm2 = sum(b * b for b in emb2) ** 0.5
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            return dot_product / (norm1 * norm2)
        except Exception as e:
            print(f"[CACHE] Similarity calculation failed: {e}")
            return 0.0
    
    def _get_cache_key(self, query: str, company_document_id: str, revision_number: str) -> str:
        """Generate cache key for query"""
        semantic_hash = self._generate_semantic_hash(query)
        return f"{self.semantic_prefix}{company_document_id}:{revision_number}:{semantic_hash}"
    
    def _cleanup_expired_entries(self):
        """Remove expired entries from memory cache"""
        current_time = time.time()
        expired_keys = [
            key for key, entry in self.memory_cache.items()
            if entry.is_expired()
        ]
        
        for key in expired_keys:
            del self.memory_cache[key]
        
        # Remove oldest entries if cache is too large
        if len(self.memory_cache) > self.max_memory_entries:
            # Sort by timestamp and remove oldest
            sorted_entries = sorted(
                self.memory_cache.items(),
                key=lambda x: x[1].timestamp
            )
            excess_count = len(self.memory_cache) - self.max_memory_entries
            for i in range(excess_count):
                del self.memory_cache[sorted_entries[i][0]]
    
    def get(
        self,
        query: str,
        company_document_id: str,
        revision_number: str,
        rag_mode: str = "balanced"
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached results for query with semantic similarity matching
        """
        cache_key = self._get_cache_key(query, company_document_id, revision_number)
        
        # Try Redis first
        if REDIS_AVAILABLE:
            try:
                cached_data = redis_client.get(cache_key)
                if cached_data:
                    entry_data = json.loads(cached_data)
                    entry = CacheEntry.from_dict(entry_data)
                    
                    if not entry.is_expired():
                        entry.hit_count += 1
                        # Update hit count in Redis
                        redis_client.setex(
                            cache_key,
                            entry.ttl,
                            json.dumps(entry.to_dict())
                        )
                        print(f"[CACHE] Redis cache hit for query: {query[:50]}...")
                        return entry.chunks
                    else:
                        # Remove expired entry
                        redis_client.delete(cache_key)
            except Exception as e:
                print(f"[CACHE] Redis get failed: {e}")
        
        # Fallback to memory cache
        with self.cache_lock:
            # Check for exact match first
            if cache_key in self.memory_cache:
                entry = self.memory_cache[cache_key]
                if not entry.is_expired():
                    entry.hit_count += 1
                    print(f"[CACHE] Memory cache hit for query: {query[:50]}...")
                    return entry.chunks
                else:
                    del self.memory_cache[cache_key]
            
            # Check for semantic similarity matches
            for key, entry in self.memory_cache.items():
                if entry.is_expired():
                    continue
                
                # Extract document info from key
                key_parts = key.split(":")
                if len(key_parts) >= 3:
                    cached_doc_id = key_parts[1]
                    cached_rev = key_parts[2]
                    
                    if (cached_doc_id == company_document_id and 
                        cached_rev == revision_number):
                        if not self._is_semantic_candidate(query, entry.query):
                            continue
                        similarity = self._calculate_similarity(query, entry.query)
                        if similarity >= self.similarity_threshold:
                            entry.hit_count += 1
                            print(f"[CACHE] Semantic cache hit (similarity: {similarity:.2f})")
                            return entry.chunks
            
            # Cleanup expired entries
            self._cleanup_expired_entries()
        
        return None
    
    def set(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        company_document_id: str,
        revision_number: str,
        rag_mode: str = "balanced",
        ttl: Optional[int] = None
    ):
        """
        Cache results for query with TTL
        """
        if not chunks:
            return
        
        ttl = ttl or self.default_ttl
        cache_key = self._get_cache_key(query, company_document_id, revision_number)
        
        entry = CacheEntry(
            query=query,
            chunks=chunks,
            timestamp=time.time(),
            ttl=ttl,
            semantic_hash=self._generate_semantic_hash(query)
        )
        
        # Store in Redis
        if REDIS_AVAILABLE:
            try:
                redis_client.setex(
                    cache_key,
                    ttl,
                    json.dumps(entry.to_dict())
                )
                print(f"[CACHE] Stored in Redis: {query[:50]}...")
            except Exception as e:
                print(f"[CACHE] Redis set failed: {e}")
        
        # Store in memory cache
        with self.cache_lock:
            self.memory_cache[cache_key] = entry
            self._cleanup_expired_entries()
            print(f"[CACHE] Stored in memory: {query[:50]}...")
    
    def invalidate_document(
        self,
        company_document_id: str,
        revision_number: str
    ):
        """
        Invalidate all cache entries for a specific document
        """
        # Invalidate Redis entries
        if REDIS_AVAILABLE:
            try:
                pattern = f"{self.semantic_prefix}{company_document_id}:{revision_number}:*"
                keys = redis_client.keys(pattern)
                if keys:
                    redis_client.delete(*keys)
                    print(f"[CACHE] Invalidated {len(keys)} Redis entries")
            except Exception as e:
                print(f"[CACHE] Redis invalidation failed: {e}")
        
        # Invalidate memory entries
        with self.cache_lock:
            keys_to_remove = [
                key for key in self.memory_cache.keys()
                if key.startswith(f"{self.semantic_prefix}{company_document_id}:{revision_number}:")
            ]
            
            for key in keys_to_remove:
                del self.memory_cache[key]
            
            print(f"[CACHE] Invalidated {len(keys_to_remove)} memory entries")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.cache_lock:
            total_entries = len(self.memory_cache)
            expired_entries = sum(
                1 for entry in self.memory_cache.values()
                if entry.is_expired()
            )
            total_hits = sum(
                entry.hit_count for entry in self.memory_cache.values()
            )
        
        redis_entries = 0
        if REDIS_AVAILABLE:
            try:
                redis_entries = len(redis_client.keys(f"{self.semantic_prefix}*"))
            except Exception:
                pass
        
        return {
            "memory_entries": total_entries,
            "memory_expired": expired_entries,
            "memory_hits": total_hits,
            "redis_entries": redis_entries,
            "redis_available": REDIS_AVAILABLE,
            "similarity_threshold": self.similarity_threshold,
        }


# Global cache instance
_global_cache: Optional[SemanticCache] = None
_cache_lock = Lock()


def get_semantic_cache(embedding_model: HuggingFaceEmbeddings) -> SemanticCache:
    """Get or create global semantic cache instance"""
    global _global_cache
    
    if _global_cache is None:
        with _cache_lock:
            if _global_cache is None:
                _global_cache = SemanticCache(embedding_model)
    
    return _global_cache

# Re-export from cache.py for clean imports
from backend.rag.cache import get_cached_chunks, set_cached_chunks, invalidate_document, get_cache_stats
