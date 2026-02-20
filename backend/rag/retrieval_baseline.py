# backend/rag/retrieval_baseline.py

import os
import sys
import time
from tabulate import tabulate  # You might need to pip install tabulate

# Fix path to allow importing from backend root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings
from backend.rag.retrieve import retrieve_rag_context
from backend.llm.hf_cache_utils import resolve_local_snapshot

# ============================================================
# CONFIG
# ============================================================

# Ensure connection string is correct (postgresql://, not +psycopg2)
DB_CONNECTION = os.getenv(
    "DB_CONNECTION",
    "postgresql://postgres:1@localhost:5432/rag_db"
).replace("postgresql+psycopg2://", "postgresql://")

COLLECTION_NAME = "rag_documents"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
HF_CACHE_DIR = os.path.join(PROJECT_ROOT, "models", "hf_cache")

# ============================================================
# SETUP
# ============================================================

def setup_store():
    print("Connecting to Vector DB...")
    # Matches the embedding model used in ingest.py and chat.py
    embedding_model = HuggingFaceEmbeddings(
        model_name=resolve_local_snapshot(HF_CACHE_DIR, "BAAI/bge-m3") or "BAAI/bge-m3",
        model_kwargs={"device": "cpu", "local_files_only": True},
        encode_kwargs={"normalize_embeddings": True},
    )

    return PGVector.from_existing_index(
        embedding=embedding_model,
        collection_name=COLLECTION_NAME,
        connection=DB_CONNECTION,
    )

# ============================================================
# BASELINE RUNNER
# ============================================================

def run_baseline(question, doc_id, rev_num):
    store = setup_store()
    
    print(f"\n🧪 TEST QUESTION: '{question}'")
    print(f"📄 DOC ID: {doc_id} (v{rev_num})")
    print("-" * 60)

    start = time.time()
    
    # Call the core retrieval logic directly
    chunks = retrieve_rag_context(
        question=question,
        vector_store=store,
        company_document_id=doc_id,
        revision_number=rev_num,
        force_detailed=True # Force max context for benchmarking
    )
    
    duration = time.time() - start

    # Format Results Table
    table_data = []
    for i, c in enumerate(chunks):
        # Truncate content for display
        content_preview = c["content"].replace("\n", " ")[:80] + "..."
        
        table_data.append([
            i+1,
            f"{c['score']:.4f}",
            c['chunk_type'],
            c['section'],
            content_preview
        ])

    print(tabulate(table_data, headers=["#", "Score", "Type", "Section", "Content"], tablefmt="simple"))
    print("-" * 60)
    print(f"Found {len(chunks)} chunks in {duration:.2f}s")

if __name__ == "__main__":
    # Usage: python backend/rag/retrieval_baseline.py <question> <doc_id> <revision>
    
    if len(sys.argv) < 4:
        print(" Usage: python backend/rag/retrieval_baseline.py <question> <doc_id> <revision>")
        print('   Example: python backend/rag/retrieval_baseline.py "What is the design pressure?" "a1b2-c3d4" "1"')
        sys.exit(1)
        
    q = sys.argv[1]
    did = sys.argv[2]
    rev = sys.argv[3]
    
    run_baseline(q, did, rev)