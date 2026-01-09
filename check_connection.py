# check_connections.py
import os
import sys
import psycopg2
import redis
from minio import Minio

# Load env vars same as your config
# (Make sure to run this in a terminal where your .env is loaded)
PG_URL = os.getenv("CHAT_DB_URL", "postgresql://postgres:1@localhost:5432/chat_memory_db")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

def check_postgres():
    print(f"🐘 Testing Postgres ({PG_URL})... ", end="")
    try:
        conn = psycopg2.connect(PG_URL)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        print("✅ OK")
        return True
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False

def check_redis():
    print(f"🧠 Testing Redis ({REDIS_HOST}:{REDIS_PORT})... ", end="")
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=3)
        r.ping()
        print("✅ OK")
        return True
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False

def check_minio():
    print(f"🪣  Testing MinIO ({MINIO_ENDPOINT})... ", end="")
    try:
        client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_KEY,
            secret_key=MINIO_SECRET,
            secure=MINIO_SECURE
        )
        # Listing buckets proves auth and connectivity work
        client.list_buckets()
        print("✅ OK")
        return True
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False

if __name__ == "__main__":
    print("--- 🛠️ CONNECTION DIAGNOSTIC 🛠️ ---")
    pg = check_postgres()
    rd = check_redis()
    mn = check_minio()
    
    if all([pg, rd, mn]):
        print("\n✨ All systems go!")
        sys.exit(0)
    else:
        print("\n🔥 Some services are unreachable.")
        sys.exit(1)