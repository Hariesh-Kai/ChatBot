# backend/rag/connection_pool.py

"""
Optimized Database Connection Pool for Retrieval Operations
Reduces connection overhead and improves concurrent query performance
"""

import threading
from contextlib import contextmanager
from typing import Dict, Any, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from langchain_postgres import PGVector

# Global connection pool cache
_connection_pools: Dict[str, Any] = {}
_pool_lock = threading.Lock()


def get_or_create_pool(
    connection_string: str,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: int = 30,
    pool_recycle: int = 3600,
) -> Any:
    """
    Get or create a connection pool for the given connection string.
    Uses singleton pattern to ensure one pool per connection string.
    """
    if connection_string not in _connection_pools:
        with _pool_lock:
            if connection_string not in _connection_pools:
                engine = create_engine(
                    connection_string,
                    poolclass=QueuePool,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_timeout=pool_timeout,
                    pool_recycle=pool_recycle,
                    pool_pre_ping=True,  # Validate connections before use
                    echo=False,
                )
                _connection_pools[connection_string] = engine
                print(f"[POOL] Created new connection pool for: {connection_string[:50]}...")
    
    return _connection_pools[connection_string]


@contextmanager
def get_db_connection(connection_string: str):
    """
    Context manager for getting database connections from the pool.
    Automatically handles connection cleanup.
    """
    engine = get_or_create_pool(connection_string)
    connection = None
    try:
        connection = engine.connect()
        yield connection
    finally:
        if connection:
            connection.close()


class OptimizedPGVector(PGVector):
    """
    Optimized version of PGVector with connection pooling and caching.
    """
    
    def __init__(self, *args, **kwargs):
        # Extract connection string for pooling
        self._connection_string = kwargs.get("connection")
        if not self._connection_string:
            for arg in args:
                if isinstance(arg, str) and "://" in arg:
                    self._connection_string = arg
                    break
        
        # Initialize parent class
        super().__init__(*args, **kwargs)
        
        # Cache for collection metadata
        self._collection_cache = {}
        self._cache_lock = threading.Lock()
    
    def similarity_search_with_pooling(
        self,
        query: str,
        k: int = 4,
        filter: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> list:
        """
        Optimized similarity search using connection pooling.
        """
        if not self._connection_string:
            # Fallback to original method
            return self.similarity_search(query, k=k, filter=filter, **kwargs)
        
        try:
            with get_db_connection(self._connection_string) as conn:
                # Build the search query
                collection_name = getattr(self, 'collection_name', '')
                
                # Prepare filter conditions
                filter_conditions = []
                params = {
                    'query_embedding': self._embedding.embed_query(query),
                    'limit': k,
                    'collection_name': collection_name
                }
                
                if filter:
                    for key, value in filter.items():
                        filter_conditions.append(f"cmetadata->>'{key}' = :filter_{key}")
                        params[f'filter_{key}'] = str(value)
                
                filter_sql = ' AND '.join(filter_conditions) if filter_conditions else '1=1'
                
                sql = text(f"""
                    SELECT document, cmetadata, embedding <=> :query_embedding as distance
                    FROM langchain_pg_embedding e
                    JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                    WHERE c.name = :collection_name AND {filter_sql}
                    ORDER BY distance
                    LIMIT :limit
                """)
                
                results = conn.execute(sql, params).fetchall()
                
                # Convert to Document objects
                documents = []
                for row in results:
                    from langchain_core.documents import Document
                    doc = Document(
                        page_content=row[0],
                        metadata=row[1] or {}
                    )
                    # Add distance as metadata
                    doc.metadata['similarity_distance'] = float(row[2])
                    documents.append(doc)
                
                return documents
                
        except Exception as e:
            print(f"[POOL] Optimized search failed, falling back: {e}")
            # Fallback to original method
            return self.similarity_search(query, k=k, filter=filter, **kwargs)
    
    def batch_similarity_search(
        self,
        queries: list[str],
        k: int = 4,
        filter: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> list[list]:
        """
        Batch similarity search for multiple queries.
        Reduces round trips to database.
        """
        if not self._connection_string or len(queries) == 1:
            # Fallback to individual searches
            return [
                self.similarity_search_with_pooling(query, k=k, filter=filter, **kwargs)
                for query in queries
            ]
        
        try:
            with get_db_connection(self._connection_string) as conn:
                # Embed all queries at once
                embeddings = [self._embedding.embed_query(query) for query in queries]
                
                collection_name = getattr(self, 'collection_name', '')
                
                # Prepare filter conditions (same for all queries)
                filter_conditions = []
                base_params = {'collection_name': collection_name}
                
                if filter:
                    for key, value in filter.items():
                        filter_conditions.append(f"cmetadata->>'{key}' = :filter_{key}")
                        base_params[f'filter_{key}'] = str(value)
                
                filter_sql = ' AND '.join(filter_conditions) if filter_conditions else '1=1'
                
                # Build batch query
                results_by_query = []
                
                for i, query_embedding in enumerate(embeddings):
                    params = {
                        **base_params,
                        'query_embedding': query_embedding,
                        'limit': k
                    }
                    
                    sql = text(f"""
                        SELECT document, cmetadata, embedding <=> :query_embedding as distance
                        FROM langchain_pg_embedding e
                        JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                        WHERE c.name = :collection_name AND {filter_sql}
                        ORDER BY distance
                        LIMIT :limit
                    """)
                    
                    results = conn.execute(sql, params).fetchall()
                    
                    # Convert to Document objects
                    documents = []
                    for row in results:
                        from langchain_core.documents import Document
                        doc = Document(
                            page_content=row[0],
                            metadata=row[1] or {}
                        )
                        doc.metadata['similarity_distance'] = float(row[2])
                        documents.append(doc)
                    
                    results_by_query.append(documents)
                
                return results_by_query
                
        except Exception as e:
            print(f"[POOL] Batch search failed, falling back: {e}")
            # Fallback to individual searches
            return [
                self.similarity_search_with_pooling(query, k=k, filter=filter, **kwargs)
                for query in queries
            ]


def create_optimized_vector_store(
    connection_string: str,
    collection_name: str,
    embedding_model,
    pool_size: int = 10,
    max_overflow: int = 20,
) -> OptimizedPGVector:
    """
    Create an optimized vector store with connection pooling.
    """
    # Ensure connection pool exists
    get_or_create_pool(
        connection_string,
        pool_size=pool_size,
        max_overflow=max_overflow
    )
    
    # Create optimized PGVector instance with the embedding function attached.
    # The currently installed langchain_postgres build requires `embeddings`
    # during construction; omitting it causes the optimized path to fail and
    # forces every request through a slower fallback.
    vector_store = OptimizedPGVector(
        embeddings=embedding_model,
        collection_name=collection_name,
        connection=connection_string,
    )
    
    # Keep compatibility with helper methods that access different attributes.
    try:
        vector_store.embedding_function = embedding_model
    except Exception:
        pass
    try:
        vector_store._embedding = embedding_model
    except Exception:
        pass
    
    return vector_store
