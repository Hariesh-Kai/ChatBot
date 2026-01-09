# verify_ingest_logic.py
import json
import os
from langchain_core.documents import Document

# 1. Create a dummy enriched_chunks.json
dummy_json = [
    {
        "page_content": "Test content",
        "metadata": {"section": "Test Section"},
        "cmetadata": {
            "company_document_id": "TEST_ID",
            "revision_number": "05"
        }
    }
]

with open("dummy_chunks.json", "w") as f:
    json.dump(dummy_json, f)

# 2. Import your actual ingest function
try:
    from backend.rag.ingest import load_documents
    
    print("🔍 Testing load_documents from backend/rag/ingest.py...")
    docs = load_documents("dummy_chunks.json")
    
    first_doc = docs[0]
    meta = first_doc.metadata
    
    print("\n[Resulting Metadata Structure]:")
    print(json.dumps(meta, indent=2))
    
    # 3. Validation
    if "company_document_id" in meta and meta["company_document_id"] == "TEST_ID":
        print("\n✅ SUCCESS: Metadata is FLATTENED correctly.")
    elif "cmetadata" in meta:
        print("\n❌ FAILURE: Metadata is still NESTED inside 'cmetadata'.")
        print("👉 You did not save the updated ingest.py file correctly.")
    else:
        print("\n❌ FAILURE: Identity keys are missing completely.")

except Exception as e:
    print(f"\n❌ CRITICAL ERROR: {e}")

finally:
    if os.path.exists("dummy_chunks.json"):
        os.remove("dummy_chunks.json")