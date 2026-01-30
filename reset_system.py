# reset_system.py

import psycopg2
import os
from minio import Minio

# ============================================================
# CONFIGURATION
# ============================================================

# 1. Database Credentials
DB_HOST = "localhost"
DB_PORT = "5432"
DB_USER = "postgres"
DB_PASS = "1"

# 2. MinIO Credentials
MINIO_ENDPOINT = "127.0.0.1:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_BUCKET = "kavin-documents"
MINIO_SECURE = False

# ============================================================
# CLEANUP FUNCTIONS
# ============================================================

def clean_minio():
    print(f"\n🗑️  Cleaning MinIO Bucket: '{MINIO_BUCKET}'...")
    try:
        client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )

        if not client.bucket_exists(MINIO_BUCKET):
            print(f"Bucket '{MINIO_BUCKET}' does not exist. Skipping.")
            return

        # List all objects (recursive)
        objects = client.list_objects(MINIO_BUCKET, recursive=True)
        
        count = 0
        for obj in objects:
            client.remove_object(MINIO_BUCKET, obj.object_name)
            count += 1
            
        print(f"Deleted {count} files from MinIO.")

    except Exception as e:
        print(f"    MinIO Error: {e}")


def clean_rag_db():
    print(f"\nCleaning RAG Database: 'rag_db'...")
    try:
        conn = psycopg2.connect(
            dbname="rag_db",
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        # Truncate the vector store table. 
        # Note: LangChain usually names it 'langchain_pg_embedding'
        cur.execute("TRUNCATE TABLE langchain_pg_embedding CASCADE;")
        
        conn.commit()
        cur.close()
        conn.close()
        print("RAG Vectors/Chunks deleted successfully.")
        
    except psycopg2.errors.UndefinedTable:
        print("Table 'langchain_pg_embedding' not found (DB might be empty).")
        if conn: conn.rollback()
    except Exception as e:
        print(f"RAG DB Error: {e}")


def clean_chat_db():
    print(f"\nCleaning Chat Memory: 'chat_memory_db'...")
    tables = [
        "chat_messages", 
        "chat_sessions", 
        "session_topic_hints", 
        "session_active_documents"
    ]
    
    try:
        conn = psycopg2.connect(
            dbname="chat_memory_db",
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        for table in tables:
            try:
                cur.execute(f"TRUNCATE TABLE {table} CASCADE;")
                print(f"   - Truncated {table}")
            except psycopg2.errors.UndefinedTable:
                print(f"   - Table {table} not found (skipping)")
                conn.rollback() 
            except Exception as e:
                print(f"   - Error on {table}: {e}")
                conn.rollback()

        conn.commit()
        cur.close()
        conn.close()
        print("Chat Memory cleared successfully.")

    except Exception as e:
        print(f"    Chat DB Error: {e}")


# ============================================================
# MAIN EXECUTION
# ============================================================

if __name__ == "__main__":
    print("!!! DANGER ZONE !!!")
    print("This script will DELETE ALL DATA from MinIO and Databases.")
    confirm = input("Type 'DELETE' to confirm: ")
    
    if confirm == "DELETE":
        clean_minio()
        clean_rag_db()
        clean_chat_db()
        print("\n✨ System Reset Complete. You can now start fresh.")
    else:
        print(" Operation aborted.")