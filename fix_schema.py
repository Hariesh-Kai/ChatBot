# fix_schema.py
import psycopg2

DB_PARAMS = {
    "dbname": "chat_memory_db",
    "user": "postgres",
    "password": "1",
    "host": "localhost",
    "port": "5432"
}

def fix():
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        
        print("🔨 Dropping 'session_active_documents' table to fix schema...")
        cur.execute("DROP TABLE IF EXISTS session_active_documents CASCADE;")
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Table dropped. Restart Backend to recreate it correctly.")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    fix()       
   