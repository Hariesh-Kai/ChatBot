import psycopg2

DB_USER = "postgres"
DB_PASS = "1"
DB_HOST = "localhost"

# ─── RAG DB ───────────────────────────────────────────────
print("\n[1/3] Clearing RAG DB (rag_db)...")
try:
    conn = psycopg2.connect(dbname="rag_db", user=DB_USER, password=DB_PASS, host=DB_HOST)
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public';")
    tables = [r[0] for r in cur.fetchall()]
    print("  Tables found:", tables)
    for t in tables:
        if t in ("spatial_ref_sys",):
            continue
        try:
            cur.execute(f"TRUNCATE TABLE {t} CASCADE;")
            print(f"  Cleared: {t}")
        except Exception as e:
            conn.rollback()
            print(f"  Skipped {t}: {e}")
    conn.commit()
    conn.close()
    print("  RAG DB: DONE")
except Exception as e:
    print(f"  RAG DB error: {e}")

# ─── CHAT MEMORY DB ───────────────────────────────────────
print("\n[2/3] Clearing Chat Memory DB (chat_memory_db)...")
try:
    conn = psycopg2.connect(dbname="chat_memory_db", user=DB_USER, password=DB_PASS, host=DB_HOST)
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public';")
    tables = [r[0] for r in cur.fetchall()]
    print("  Tables found:", tables)
    for t in tables:
        try:
            cur.execute(f"TRUNCATE TABLE {t} CASCADE;")
            print(f"  Cleared: {t}")
        except Exception as e:
            conn.rollback()
            print(f"  Skipped {t}: {e}")
    conn.commit()
    conn.close()
    print("  Chat Memory DB: DONE")
except Exception as e:
    print(f"  Chat Memory DB error: {e}")

# ─── MINIO ────────────────────────────────────────────────
print("\n[3/3] Clearing MinIO bucket...")
try:
    from minio import Minio
    client = Minio("127.0.0.1:9000", access_key="minioadmin", secret_key="minioadmin", secure=False)
    buckets = ["kavin-bucket", "chat-ui-documents"]
    for bucket in buckets:
        if not client.bucket_exists(bucket):
            print(f"  Bucket '{bucket}' does not exist, skipping.")
            continue
        objects = list(client.list_objects(bucket, recursive=True))
        count = 0
        for obj in objects:
            client.remove_object(bucket, obj.object_name)
            count += 1
        print(f"  Bucket '{bucket}': deleted {count} files")
    print("  MinIO: DONE")
except Exception as e:
    print(f"  MinIO error: {e}")

print("\n✅ Full reset complete! All old data removed.")
