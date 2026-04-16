# backend/rag/ingest.py

import os
import json
import threading
import time
from contextlib import closing
from typing import List, Dict, Any, Callable, Optional

import psycopg2

from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from backend.llm.hf_cache_utils import require_local_snapshot
from backend.llm.model_config_store import HF_CACHE_DIR
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)
from backend.rag.upload_cancellation import UploadCancellationError

COLLECTION_NAME = DEFAULT_RAG_COLLECTION_NAME
_EMBEDDINGS_LOCK = threading.Lock()
_EMBEDDINGS_CLIENT: Optional[HuggingFaceEmbeddings] = None
_KEYWORD_SEARCH_LOCK = threading.Lock()
_KEYWORD_SEARCH_READY = False

# ============================================================
# INTERNAL HELPERS
# ============================================================

def _normalize_conn(conn: str) -> str:
    if not conn:
        raise RuntimeError("DB connection string is required")
    return conn.replace("postgresql+psycopg2://", "postgresql://")


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _resolve_embedding_device() -> str:
    configured = str(os.getenv("RAG_EMBEDDING_DEVICE", "auto")).strip().lower()
    if configured in {"cpu", "cuda"}:
        return configured
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _resolve_embedding_batch_size(device: str) -> int:
    # Conservative defaults to avoid OOM on large docs.
    default = 32 if device == "cuda" else 8
    return max(1, _safe_int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", str(default)), default))

def _get_embeddings() -> HuggingFaceEmbeddings:
    # Reuse a single embedding client process-wide.
    # Re-initializing this for every commit causes large startup latency.
    global _EMBEDDINGS_CLIENT
    if _EMBEDDINGS_CLIENT is not None:
        return _EMBEDDINGS_CLIENT

    with _EMBEDDINGS_LOCK:
        if _EMBEDDINGS_CLIENT is not None:
            return _EMBEDDINGS_CLIENT

        device = _resolve_embedding_device()
        batch_size = _resolve_embedding_batch_size(device)
        _EMBEDDINGS_CLIENT = HuggingFaceEmbeddings(
            model_name=require_local_snapshot(HF_CACHE_DIR, "BAAI/bge-m3"),
            cache_folder=str(HF_CACHE_DIR),
            model_kwargs={"device": device, "local_files_only": True},
            encode_kwargs={
                "normalize_embeddings": True,
                "batch_size": batch_size,
            },
        )
        print(
            f"[INGEST] Embeddings initialized | device={device} batch_size={batch_size}"
        )
        return _EMBEDDINGS_CLIENT


def _get_vector_store(connection_string: str, *, collection_name: str = COLLECTION_NAME) -> PGVector:
    return PGVector.from_existing_index(
        embedding=_get_embeddings(),
        collection_name=normalize_collection_name(collection_name),
        connection=connection_string,
    )


def _count_existing_chunks(
    *,
    connection_string: str,
    company_document_id: str,
    revision_number: str,
    collection_name: str,
) -> int:
    from psycopg2 import sql

    with closing(psycopg2.connect(_normalize_conn(connection_string))) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT COUNT(DISTINCT cmetadata->>'chunk_id')
                    FROM langchain_pg_embedding
                    WHERE cmetadata->>'company_document_id' = %s
                      AND cmetadata->>'revision_number' = %s
                      AND collection_id = (
                          SELECT uuid
                          FROM langchain_pg_collection
                          WHERE name = %s
                      )
                    """
                ),
                (
                    company_document_id,
                    str(revision_number),
                    normalize_collection_name(collection_name),
                ),
            )
            row = cur.fetchone()

    return int((row or [0])[0] or 0)


def _keyword_search_schema_exists(cur) -> bool:
    cur.execute(
        """
        SELECT
            EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'langchain_pg_embedding'
                  AND column_name = 'content_tsv'
            ),
            EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'langchain_pg_embedding'
                  AND indexname = 'langchain_pg_embedding_content_tsv_idx'
            );
        """
    )
    row = cur.fetchone() or (False, False)
    return bool(row[0]) and bool(row[1])


def delete_document_revision(
    *,
    connection_string: str,
    company_document_id: str,
    revision_number: str,
    collection_name: str = COLLECTION_NAME,
) -> int:
    """
    Delete one document revision from a specific PGVector collection.

    This is mainly used by benchmark runs so repeated indexing does not create
    duplicate chunks inside the same isolated test collection.
    """
    with closing(psycopg2.connect(_normalize_conn(connection_string))) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM langchain_pg_embedding AS e
                USING langchain_pg_collection AS c
                WHERE e.collection_id = c.uuid
                  AND c.name = %s
                  AND e.cmetadata->>'company_document_id' = %s
                  AND e.cmetadata->>'revision_number' = %s
                """,
                (
                    normalize_collection_name(collection_name),
                    company_document_id,
                    str(revision_number),
                ),
            )
            deleted_rows = int(cur.rowcount or 0)
        conn.commit()
    return deleted_rows

# ============================================================
# LOAD DOCUMENTS (FIXED: CAPTURE CHUNK ID)
# ============================================================

def load_documents(json_path: str) -> List[Document]:
    """
    Load enriched chunks from JSON into LangChain Documents.
    
    🔥 FIX 1: Flattens cmetadata into main metadata.
    🔥 FIX 2: Explicitly captures 'chunk_id' from top-level JSON.
    """

    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    documents: List[Document] = []

    for item in raw:
        content = item.get("page_content")
        metadata = item.get("metadata", {})
        cmetadata = item.get("cmetadata")
        
        #  NEW: Capture the ID generated by metadata.py
        chunk_id = item.get("chunk_id")

        if not content or not cmetadata:
            continue

        if "company_document_id" not in cmetadata:
            raise RuntimeError("Missing company_document_id in cmetadata")

        if "revision_number" not in cmetadata:
            raise RuntimeError("Missing revision_number in cmetadata")

        # 🔥 FLATTEN: Merge identity directly into top-level metadata
        combined_metadata = {
            **metadata,
            **cmetadata 
        }
        if not chunk_id:
            raise RuntimeError("chunk_id missing during ingest")

        #  NEW: Inject chunk_id into metadata so it gets saved to DB
        combined_metadata["chunk_id"] = chunk_id

        documents.append(
            Document(
                page_content=content,
                metadata=combined_metadata,
            )
        )

    if not documents:
        raise RuntimeError("No valid chunks loaded from JSON")

    return documents


# ============================================================
# CHUNK QUALITY SCORING (at ingest time)
# ============================================================

def score_chunk_quality(content: str) -> Dict[str, Any]:
    """
    Score a chunk's retrieval quality based on content characteristics.
    Called at ingest time — result stored in metadata.

    Scores 0.0–1.0 on 3 signals:
    - Length score:   too short/long chunks are harder to retrieve well
    - Density score:  ratio of alpha chars (avoids noise/gibberish chunks)
    - Richness score: number of distinct meaningful words (>3 chars)

    Returns: {"quality_score": float, "quality_tier": "high"|"medium"|"low"}
    """
    import re as _re

    default = {"quality_score": 0.5, "quality_tier": "medium"}
    if not content:
        return default

    try:
        # 1. Length score (optimal: 200–1500 chars)
        length = len(content)
        if length < 50:
            length_score = 0.1
        elif length < 200:
            length_score = 0.5
        elif length <= 1500:
            length_score = 1.0
        elif length <= 3000:
            length_score = 0.7
        else:
            length_score = 0.4

        # 2. Density score (fraction of alpha chars)
        alpha_chars = sum(1 for c in content if c.isalpha())
        density_score = min(alpha_chars / max(length, 1), 1.0)

        # 3. Richness score (distinct words > 3 chars, capped at 50)
        words = _re.findall(r"[a-zA-Z]{4,}", content.lower())
        distinct = len(set(words))
        richness_score = min(distinct / 50.0, 1.0)

        overall = round(
            0.3 * length_score + 0.3 * density_score + 0.4 * richness_score, 3
        )

        if overall >= 0.70:
            tier = "high"
        elif overall >= 0.40:
            tier = "medium"
        else:
            tier = "low"

        return {"quality_score": overall, "quality_tier": tier}

    except Exception as e:
        print(f"[INGEST] score_chunk_quality error (non-fatal): {e}")
        return default


# ============================================================
# INGEST DOCUMENT REVISION (NO DELETION, REVISION SAFE)
# ============================================================

def ingest_to_pgvector(
    *,
    documents: List[Document],
    connection_string: str,
    company_document_id: str,
    revision_number: str, #  FIX: Changed to str for enterprise support
    collection_name: str = COLLECTION_NAME,
    replace_existing: bool = False,
    should_cancel: Optional[Callable[[], bool]] = None,
    batch_size: int = 48,
) -> None:
    """
    Ingest a document revision into PGVector.
    """

    if not documents:
        raise RuntimeError("No documents provided for ingestion")

    resolved_collection_name = normalize_collection_name(collection_name)
    total_docs = len(documents)
    
    # Check if chunks are already in the database (idempotency check)
    try:
        existing_count = _count_existing_chunks(
            connection_string=connection_string,
            company_document_id=company_document_id,
            revision_number=revision_number,
            collection_name=resolved_collection_name,
        )

        if existing_count >= total_docs and not replace_existing:
            print(
                f"[INGEST] Skipping ingest - chunks already exist | "
                f"collection={resolved_collection_name} existing={existing_count} required={total_docs}"
            )
            return
    except Exception as e:
        print(f"[INGEST] Could not check existing chunks, proceeding with ingest: {e}")
    
    print(
        "[INGEST] Starting vector ingest | "
        f"collection={resolved_collection_name} total_chunks={total_docs}"
    )
    vector_store = _get_vector_store(
        connection_string,
        collection_name=resolved_collection_name,
    )

    # --------------------------------------------------------
    # 🔒 DEFENSIVE IDENTITY CHECK (FIXED)
    # --------------------------------------------------------

    for doc in documents:
        # Check top-level metadata (since we flattened it)
        cm = doc.metadata 

        if cm.get("company_document_id") != company_document_id:
            raise RuntimeError(
                "company_document_id mismatch during ingest"
            )

        #  FIX: Strict string comparison for revisions
        if str(cm.get("revision_number")) != str(revision_number):
            raise RuntimeError(
                f"revision_number mismatch during ingest: "
                f"doc={cm.get('revision_number')} expected={revision_number}"
            )

    # --------------------------------------------------------
    # INGEST
    # --------------------------------------------------------

    if replace_existing:
        deleted_rows = delete_document_revision(
            connection_string=connection_string,
            company_document_id=company_document_id,
            revision_number=revision_number,
            collection_name=resolved_collection_name,
        )
        print(
            "[INGEST] Cleared existing chunks before re-index | "
            f"collection={resolved_collection_name} deleted_rows={deleted_rows}"
        )

    safe_batch_size = max(1, int(batch_size or 48))
    total_batches = (total_docs + safe_batch_size - 1) // safe_batch_size
    ingest_start_ts = time.perf_counter()
    for start in range(0, len(documents), safe_batch_size):
        if should_cancel and should_cancel():
            raise UploadCancellationError("Cancelled by user")

        batch_start_ts = time.perf_counter()
        batch = documents[start : start + safe_batch_size]
        batch_no = (start // safe_batch_size) + 1
        range_start = start + 1
        range_end = min(start + len(batch), total_docs)
        print(
            "[INGEST] Embedding batch started | "
            f"batch={batch_no}/{total_batches} chunks={len(batch)} "
            f"range={range_start}-{range_end}"
        )
        vector_store.add_documents(batch)
        batch_elapsed = time.perf_counter() - batch_start_ts
        done = range_end
        pct = int((done / max(total_docs, 1)) * 100)
        print(
            "[INGEST] Embedding batch completed | "
            f"batch={batch_no}/{total_batches} "
            f"progress={done}/{total_docs} ({pct}%) "
            f"elapsed={batch_elapsed:.2f}s"
        )

        if should_cancel and should_cancel():
            raise UploadCancellationError("Cancelled by user")

    total_elapsed = time.perf_counter() - ingest_start_ts
    print(
        "[INGEST] Embedding + vector insert completed | "
        f"total_chunks={total_docs} total_batches={total_batches} "
        f"elapsed={total_elapsed:.2f}s"
    )
    setup_keyword_search(connection_string)


# ============================================================
# METADATA UPDATE (POST-CONFIRMATION)
# ============================================================

def update_vector_metadata(
    *,
    connection_string: str,
    company_document_id: str,
    revision_number: str, 
    updated_metadata: Dict[str, Any],
) -> None:
    """
    Update cmetadata for ALL chunks of a document revision.
    """

    if not updated_metadata:
        return

    with closing(psycopg2.connect(_normalize_conn(connection_string))) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE langchain_pg_embedding
                SET cmetadata = cmetadata || %s::jsonb
                WHERE cmetadata->>'company_document_id' = %s
                  AND cmetadata->>'revision_number' = %s
                """,
                (
                    json.dumps(updated_metadata),
                    company_document_id,
                    str(revision_number),
                ),
            )
        conn.commit()


# ============================================================
# DUPLICATE METADATA CHECK
# ============================================================

def metadata_exists(
    *,
    connection_string: str,
    metadata: Dict[str, Any],
) -> bool:
    """
    Check if similar cmetadata already exists.
    """

    if not metadata:
        return False

    with closing(psycopg2.connect(_normalize_conn(connection_string))) as conn:
        with conn.cursor() as cur:
            conditions = []
            values = []

            for k, v in metadata.items():
                conditions.append("cmetadata->>%s = %s")
                values.extend([k, str(v)])

            query = f"""
                SELECT 1
                FROM langchain_pg_embedding
                WHERE {' AND '.join(conditions)}
                LIMIT 1
            """

            cur.execute(query, values)
            exists = cur.fetchone() is not None

    return exists


# ============================================================
# KEYWORD SEARCH SUPPORT
# ============================================================

def setup_keyword_search(connection_string: str) -> None:
    """
    Create keyword search schema and index when possible.

    This is best-effort. Retrieval already falls back to ILIKE when the
    full-text objects are unavailable, so uploads should never hang here.
    """
    global _KEYWORD_SEARCH_READY

    if _KEYWORD_SEARCH_READY:
        return

    with _KEYWORD_SEARCH_LOCK:
        if _KEYWORD_SEARCH_READY:
            return

        lock_timeout_ms = max(
            0,
            _safe_int(
                os.getenv("RAG_KEYWORD_SEARCH_LOCK_TIMEOUT_MS", "3000"),
                3000,
            ),
        )
        statement_timeout_ms = max(
            0,
            _safe_int(
                os.getenv("RAG_KEYWORD_SEARCH_STATEMENT_TIMEOUT_MS", "15000"),
                15000,
            ),
        )

        try:
            with closing(psycopg2.connect(_normalize_conn(connection_string))) as conn:
                with conn.cursor() as cur:
                    if _keyword_search_schema_exists(cur):
                        _KEYWORD_SEARCH_READY = True
                        return

                    if lock_timeout_ms > 0:
                        cur.execute("SET lock_timeout = %s", (f"{lock_timeout_ms}ms",))
                    if statement_timeout_ms > 0:
                        cur.execute(
                            "SET statement_timeout = %s",
                            (f"{statement_timeout_ms}ms",),
                        )

                    cur.execute(
                        """
                        ALTER TABLE langchain_pg_embedding
                        ADD COLUMN IF NOT EXISTS content_tsv tsvector
                        GENERATED ALWAYS AS (
                            to_tsvector('english', document)
                        ) STORED;
                        """
                    )

                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS
                        langchain_pg_embedding_content_tsv_idx
                        ON langchain_pg_embedding
                        USING GIN (content_tsv);
                        """
                    )
                conn.commit()
            _KEYWORD_SEARCH_READY = True
        except Exception as e:
            print(f"[INGEST] Keyword search setup skipped (non-fatal): {e}")
