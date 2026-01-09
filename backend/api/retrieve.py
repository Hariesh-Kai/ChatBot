# backend/api/retrieve.py

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

import psycopg2
import os


# ============================================================
# CONFIG
# ============================================================

DEFAULT_DB = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:1@localhost:5432/rag_db",
)

COLLECTION_NAME = "rag_documents"

HF_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(prefix="/retrieve", tags=["Retrieve"])


# ============================================================
# RESPONSE SCHEMAS (FINAL)
# ============================================================

class RetrievedChunk(BaseModel):
    content: str
    cmetadata: dict


class RetrieveResponse(BaseModel):
    company_document_id: Optional[str]
    revision_number: Optional[int]
    chunks: List[RetrievedChunk]


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _normalize_conn(conn: str) -> str:
    return conn.replace("postgresql+psycopg2://", "postgresql://")


def _get_vector_store(conn: str) -> PGVector:
    embeddings = HuggingFaceEmbeddings(model_name=HF_MODEL)

    return PGVector.from_existing_index(
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        connection=conn,
    )


def _get_latest_revision_number(
    *,
    connection_string: str,
    company_document_id: str,
) -> int:
    """
    Fetch latest revision_number for a document.
    """

    conn = psycopg2.connect(_normalize_conn(connection_string))
    cur = conn.cursor()

    cur.execute(
        """
        SELECT MAX((cmetadata->>'revision_number')::int)
        FROM langchain_pg_embedding
        WHERE cmetadata->>'company_document_id' = %s
        """,
        (company_document_id,),
    )

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row or row[0] is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No revisions found for "
                f"company_document_id={company_document_id}"
            ),
        )

    return int(row[0])


# ============================================================
# RETRIEVE ENDPOINT
# ============================================================

@router.get("/", response_model=RetrieveResponse)
def retrieve_chunks(
    *,
    query: str = Query(..., min_length=3),
    company_document_id: Optional[str] = Query(None),
    top_k: int = Query(5, ge=1, le=20),
    db_connection: str = DEFAULT_DB,
):
    """
    Enterprise retrieval API.

    MODES:
    - Global retrieval (no document filter)
    - Document-scoped retrieval (latest revision only)

    GUARANTEES:
    - Never returns old revisions
    - Never mutates DB
    """

    vector_store = _get_vector_store(db_connection)

    filter_dict = None
    revision_number = None

    # --------------------------------------------------
    # DOCUMENT-SCOPED MODE
    # --------------------------------------------------

    if company_document_id:
        revision_number = _get_latest_revision_number(
            connection_string=db_connection,
            company_document_id=company_document_id,
        )

        filter_dict = {
            "company_document_id": company_document_id,
            "revision_number": revision_number,
        }

    # --------------------------------------------------
    # VECTOR SEARCH
    # --------------------------------------------------

    try:
        docs: List[Document] = vector_store.similarity_search(
            query=query,
            k=top_k,
            filter=filter_dict,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Vector search failed: {e}",
        )

    if not docs:
        return RetrieveResponse(
            company_document_id=company_document_id,
            revision_number=revision_number,
            chunks=[],
        )

    # --------------------------------------------------
    # RESPONSE
    # --------------------------------------------------

    return RetrieveResponse(
        company_document_id=company_document_id,
        revision_number=revision_number,
        chunks=[
            RetrievedChunk(
                content=d.page_content,
                cmetadata=d.cmetadata,
            )
            for d in docs
        ],
    )
