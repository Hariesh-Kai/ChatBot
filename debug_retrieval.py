# debug_retrieval.py
import os
import uuid
from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings
from backend.rag.keyword_search import keyword_search

# --- CONFIG ---
DB_CONNECTION = "postgresql+psycopg2://postgres:1@localhost:5432/rag_db"
COLLECTION_NAME = "rag_documents"

# --- MOCK DATA (From your logs) ---
SESSION_ID = "debug-session"
QUESTION = "What specific naming convention does the document suggest for separate chapter files?"
DOC_ID = "81f79cd6-a357-5dc1-8cc2-2b24e61fd3e4"
REV_NUM = "10"

def debug_retrieval():
    print(f"🔍 DEBUG: Starting retrieval test for Doc ID: {DOC_ID}")
    
    # 1. Init Vector Store
    try:
        embedding_model = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        vector_store = PGVector.from_existing_index(
            embedding=embedding_model,
            collection_name=COLLECTION_NAME,
            connection=DB_CONNECTION,
        )
        print("Vector Store initialized")
    except Exception as e:
        print(f" Failed to init vector store: {e}")
        return

    # 2. Filter
    metadata_filter = {
        "company_document_id": DOC_ID,
        "revision_number": str(REV_NUM), 
    }
    print(f"🔍 Filter: {metadata_filter}")

    # 3. Vector Search
    print("\n--- 1. VECTOR SEARCH ---")
    vector_docs = vector_store.similarity_search(QUESTION, k=4, filter=metadata_filter)
    print(f"Found {len(vector_docs)} docs")
    for i, d in enumerate(vector_docs):
        print(f"  [{i}] Content Length: {len(d.page_content)}")
        print(f"      Metadata Keys: {list(d.metadata.keys())}")
        print(f"      Chunk ID: {d.metadata.get('chunk_id')}")

    # 4. Keyword Search
    print("\n--- 2. KEYWORD SEARCH ---")
    keyword_docs = keyword_search(
        question=QUESTION,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        limit=4,
    )
    print(f"Found {len(keyword_docs)} docs")
    for i, d in enumerate(keyword_docs):
        print(f"  [{i}] Content Length: {len(d.page_content)}")
        # Keyword docs store metadata in 'cmetadata' usually, let's check both
        print(f"      Metadata: {d.metadata}")
        # Note: keyword_search.py returns docs with empty 'metadata' but populated 'cmetadata' attribute
        # We need to see if chat.py handles this logic correctly.

    # 5. Merging Logic (The suspected failure point)
    print("\n--- 3. MERGING LOGIC ---")
    merged = {}
    
    all_docs = vector_docs + keyword_docs
    
    for i, d in enumerate(all_docs):
        # SIMULATING CHAT.PY LOGIC
        chunk_id = d.metadata.get("chunk_id")
        
        # If chunk_id is missing, we try to grab it from cmetadata if it exists (LangChain quirk)
        if not chunk_id and hasattr(d, "cmetadata"):
             chunk_id = d.cmetadata.get("chunk_id")

        status = "Found ID"
        if not chunk_id:
            chunk_id = str(uuid.uuid4())
            status = "Generated ID"
            
        print(f"  Doc {i}: {status} -> {chunk_id}")
        merged.setdefault(chunk_id, d)

    print(f"\n📊 Final Merged Count: {len(merged)}")
    
    if len(merged) == 0:
        print(" CRITICAL: Merged list is empty! Chatbot receives nothing.")
    else:
        print("SUCCESS: Merged list has data. The issue is likely in the LLM Prompt.")

if __name__ == "__main__":
    debug_retrieval()