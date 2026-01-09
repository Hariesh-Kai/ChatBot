# backend/memory/redis_memory.py

import redis
import json
import os
from typing import List, Set, Optional, Dict, Any

# ============================================================
# REDIS CONNECTION (SAFE + CONFIGURABLE)
# ============================================================

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

try:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True,
    )
    r.ping()
except Exception as e:
    print(f"⚠️ Redis unavailable: {e}")
    r = None  # graceful degradation

# ============================================================
# TTL CONFIG
# ============================================================

SESSION_TTL = 60 * 30    # 30 minutes
DEBUG_TTL = 60 * 10      # 10 minutes

# ============================================================
# 🔑 KEY HELPERS
# ============================================================

def _key_used_chunks(session_id: str) -> str:
    return f"rag:used_chunks:{session_id}"

def _key_topic(session_id: str) -> str:
    return f"rag:topic:{session_id}"

def _key_last_query(session_id: str) -> str:
    return f"rag:last_query:{session_id}"

def _key_rag_debug(session_id: str) -> str:
    return f"rag:debug:{session_id}"

# ============================================================
# 🧠 TOPIC TRACKING (TEXT, NOT HASH)
# ============================================================

def get_active_topic(session_id: str) -> Optional[str]:
    if not session_id or not r:
        return None
    try:
        return r.get(_key_topic(session_id))
    except Exception as e:
        print(f"⚠️ Redis get topic failed: {e}")
        return None

def set_active_topic(session_id: str, topic_text: str):
    """
    Store FULL topic text.
    Reset used chunks automatically if topic changes.
    """
    if not session_id or not topic_text or not r:
        return

    try:
        prev = r.get(_key_topic(session_id))
        if prev and prev != topic_text:
            # 🔥 Topic changed → reset used chunks
            r.delete(_key_used_chunks(session_id))

        r.setex(
            _key_topic(session_id),
            SESSION_TTL,
            topic_text,
        )
    except Exception as e:
        print(f"⚠️ Redis set topic failed: {e}")

def reset_topic(session_id: str):
    if not session_id or not r:
        return
    try:
        r.delete(_key_topic(session_id))
        r.delete(_key_used_chunks(session_id))
    except Exception as e:
        print(f"⚠️ Redis reset topic failed: {e}")

# ============================================================
# 🧠 CHUNK USAGE STATE
# ============================================================

def get_used_chunk_ids(session_id: str) -> Set[str]:
    if not session_id or not r:
        return set()

    try:
        data = r.get(_key_used_chunks(session_id))
        if not data:
            return set()

        ids = json.loads(data)
        if isinstance(ids, list):
            return set(ids)

    except Exception as e:
        print(f"⚠️ Corrupted used_chunks for {session_id}: {e}")

    # corrupted state → reset
    try:
        r.delete(_key_used_chunks(session_id))
    except Exception:
        pass

    return set()

def add_used_chunk_ids(session_id: str, chunk_ids: List[str]):
    if not session_id or not chunk_ids or not r:
        return

    try:
        existing = get_used_chunk_ids(session_id)
        updated = existing.union(set(chunk_ids))

        r.setex(
            _key_used_chunks(session_id),
            SESSION_TTL,
            json.dumps(list(updated)),
        )
    except Exception as e:
        print(f"⚠️ Redis add_used_chunk_ids failed: {e}")

def clear_used_chunk_ids(session_id: str):
    if not session_id or not r:
        return
    try:
        r.delete(_key_used_chunks(session_id))
    except Exception as e:
        print(f"⚠️ Redis clear used_chunks failed: {e}")

# ============================================================
# 🧠 LAST REWRITTEN QUERY
# ============================================================

def get_last_rewritten_query(session_id: str) -> Optional[str]:
    if not session_id or not r:
        return None
    try:
        return r.get(_key_last_query(session_id))
    except Exception as e:
        print(f"⚠️ Redis get last_query failed: {e}")
        return None

def set_last_rewritten_query(session_id: str, query: str):
    if not session_id or not query or not r:
        return
    try:
        r.setex(
            _key_last_query(session_id),
            SESSION_TTL,
            query,
        )
    except Exception as e:
        print(f"⚠️ Redis set last_query failed: {e}")

# ============================================================
# 🧪 RAG DEBUG SNAPSHOT
# ============================================================

def save_rag_debug(session_id: str, payload: Dict[str, Any]):
    if not session_id or not isinstance(payload, dict) or not r:
        return
    try:
        r.setex(
            _key_rag_debug(session_id),
            DEBUG_TTL,
            json.dumps(payload),
        )
    except Exception as e:
        print(f"⚠️ Redis save_rag_debug failed: {e}")

def get_rag_debug(session_id: str) -> Optional[Dict[str, Any]]:
    if not session_id or not r:
        return None

    try:
        data = r.get(_key_rag_debug(session_id))
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        print(f"⚠️ Corrupted rag_debug for {session_id}: {e}")
        try:
            r.delete(_key_rag_debug(session_id))
        except Exception:
            pass
        return None

def clear_rag_debug(session_id: str):
    if not session_id or not r:
        return
    try:
        r.delete(_key_rag_debug(session_id))
    except Exception as e:
        print(f"⚠️ Redis clear_rag_debug failed: {e}")

def reset_rag_state(session_id: str):
    """
    Fully reset RAG-related Redis state.
    Call on:
    - new document upload
    - new revision commit
    """
    if not session_id or not r:
        return

    try:
        r.delete(
            _key_used_chunks(session_id),
            _key_topic(session_id),
            _key_last_query(session_id),
            _key_rag_debug(session_id),
        )
    except Exception as e:
        print(f"⚠️ Redis reset_rag_state failed: {e}")

