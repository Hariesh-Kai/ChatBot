import psycopg2
import json

# --- CONFIG ---
CHAT_DB_URL = "postgresql://postgres:1@localhost:5432/chat_memory_db"
RAG_DB_URL  = "postgresql://postgres:1@localhost:5432/rag_db"

def check_chat_db():
    print("\n🔎 --- CHECKING CHAT MEMORY DB ---")
    try:
        conn = psycopg2.connect(CHAT_DB_URL)
        cur = conn.cursor()
        
        # 1. Check Table Schema (Column Types)
        print("\n[Schema Check] session_active_documents:")
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'session_active_documents';
        """)
        columns = cur.fetchall()
        for col in columns:
            print(f"   - {col[0]}: {col[1]}")

        # 2. Check Actual Data
        print("\n[Data Check] Active Documents:")
        cur.execute("SELECT session_id, company_document_id, revision_number FROM session_active_documents;")
        rows = cur.fetchall()
        if not rows:
            print("    TABLE IS EMPTY! (Did you upload a file after wiping?)")
        for row in rows:
            print(f"   - Session: {row[0]}")
            print(f"     Doc ID:  {row[1]}")
            print(f"     Rev Num: {row[2]}  <-- Type: {type(row[2])}")
            print("   -------------------------")

        conn.close()
    except Exception as e:
        print(f" Chat DB Error: {e}")

def check_rag_db():
    print("\n🔎 --- CHECKING VECTOR DB (RAG) ---")
    try:
        conn = psycopg2.connect(RAG_DB_URL)
        cur = conn.cursor()

        # 1. Check Metadata inside Vector Store
        print("\n[Data Check] Stored Chunks (Limit 3):")
        # We fetch the JSON metadata column
        cur.execute("SELECT cmetadata FROM langchain_pg_embedding LIMIT 3;")
        rows = cur.fetchall()
        
        if not rows:
            print("    VECTOR DB IS EMPTY! (You must upload a PDF to populate it)")
        
        for i, row in enumerate(rows):
            meta = row[0]
            print(f"   [Chunk {i+1}]")
            print(f"     Doc ID:  {meta.get('company_document_id')}")
            print(f"     Rev Num: {meta.get('revision_number')} <-- Type: {type(meta.get('revision_number'))}")
            print("   -------------------------")

        conn.close()
    except Exception as e:
        print(f" RAG DB Error: {e}")

if __name__ == "__main__":
    check_chat_db()
    check_rag_db()