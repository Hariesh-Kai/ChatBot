# backend/memory/pg_memory.py

from typing import List, Dict, Optional, Any  #  Added 'Any'
import os
from contextlib import contextmanager

import psycopg2
from psycopg2 import connect
from psycopg2.extras import RealDictCursor

from backend.state.abort_signals import is_aborted

# =========================================================
# CHAT MEMORY DATABASE (ARCHIVAL + SESSION STATE)
# =========================================================

CHAT_DB_URL = os.getenv(
    "CHAT_DB_URL",
    "postgresql://postgres:1@localhost:5432/chat_memory_db",
)

#  NEW: RAG Database Connection (for retrieving chunk text)
# We strip '+psycopg2' because the driver doesn't need it in the connection string
RAG_DB_URL = os.getenv(
    "DB_CONNECTION", 
    "postgresql://postgres:1@localhost:5432/rag_db"
).replace("postgresql+psycopg2://", "postgresql://")

# Hard safety cap
MAX_CHAT_HISTORY = 200

# =========================================================
# CONNECTION HANDLING (SAFE)
# =========================================================

@contextmanager
def get_connection():
    """
    Context manager for CHAT DATABASE operations.
    """
    conn = None
    try:
        conn = connect(CHAT_DB_URL)
        yield conn
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

# =========================================================
# 🔥 AUTO-INITIALIZATION (SELF-HEALING)
# =========================================================

def _init_db():
    """
    Automatically creates required tables in chat_memory_db.
    Safe to run multiple times (uses IF NOT EXISTS).
    """
    queries = [
        # 1. Chat Sessions
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        # 2. Chat Messages
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        # 3. Topic Hints (for better search)
        """
        CREATE TABLE IF NOT EXISTS session_topic_hints (
            session_id TEXT PRIMARY KEY,
            topic_hint TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        # 4. Active Document State
        """
        CREATE TABLE IF NOT EXISTS session_active_documents (
            session_id TEXT PRIMARY KEY,
            company_document_id TEXT NOT NULL,
            revision_number TEXT NOT NULL,
            collection_name TEXT,
            filename TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        # 5. User State (Agentic Memory)
        """
        CREATE TABLE IF NOT EXISTS user_state (
            user_id TEXT PRIMARY KEY,
            expertise_level TEXT DEFAULT 'intermediate',
            preferred_answer_style TEXT DEFAULT 'balanced',
            preferred_detail_level TEXT DEFAULT 'medium',
            interaction_count INTEGER DEFAULT 0,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            preferences JSONB DEFAULT '{}'
        );
        """,
        # 6. User Feedback (Learning Mechanism)
        """
        CREATE TABLE IF NOT EXISTS user_feedback (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            feedback_type TEXT NOT NULL,
            feedback_value INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    ]

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(q)
                cur.execute(
                    """
                    ALTER TABLE session_active_documents
                    ADD COLUMN IF NOT EXISTS collection_name TEXT;
                    """
                )
        print("Chat Database initialized (Tables verified/created).")
    except Exception as e:
        print(f"Database initialization warning: {e}")

# Run immediately on import
_init_db()


# =========================================================
# SESSION + MESSAGE (ATOMIC)
# =========================================================

def append_chat_message(session_id: str, role: str, content: str):
    """
    Atomically ensure session exists and append chat message.
    """
    if not session_id or not role or content is None:
        return

    # 🔥 DO NOT persist assistant messages after abort
    if role == "assistant" and is_aborted(session_id):
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (session_id)
                VALUES (%s)
                ON CONFLICT (session_id)
                DO UPDATE SET last_active = NOW()
                """,
                (session_id,),
            )

            cur.execute(
                """
                INSERT INTO chat_messages (session_id, role, content)
                VALUES (%s, %s, %s)
                """,
                (session_id, role, content),
            )

# =========================================================
# READ CHAT MESSAGES
# =========================================================

def get_chat_messages(
    session_id: str,
    limit: int = 50,
) -> List[Dict[str, str]]:
    if not session_id:
        return []

    limit = max(1, min(int(limit), MAX_CHAT_HISTORY))

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT role, content, created_at
                FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (session_id, limit),
            )
            return cur.fetchall() or []

def get_recent_user_messages(
    session_id: str,
    limit: int = 3,
) -> List[str]:
    if not session_id:
        return []

    limit = max(1, min(int(limit), 20))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content
                FROM chat_messages
                WHERE session_id = %s
                  AND role = 'user'
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cur.fetchall() or []

    return [r[0] for r in reversed(rows)]

# =========================================================
# SESSION TOPIC HINTS
# =========================================================

def save_topic_hint(session_id: str, topic_hint: str):
    if not session_id or not topic_hint:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_topic_hints (session_id, topic_hint)
                VALUES (%s, %s)
                ON CONFLICT (session_id)
                DO UPDATE SET
                    topic_hint = EXCLUDED.topic_hint,
                    updated_at = NOW()
                """,
                (session_id, topic_hint),
            )

def get_last_topic_hint(session_id: str) -> Optional[str]:
    if not session_id:
        return None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT topic_hint
                FROM session_topic_hints
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()

    return row[0] if row else None

# =========================================================
#  ACTIVE DOCUMENT PERSISTENCE (SELF-HEALING)
# =========================================================

def save_active_document(
    session_id: str,
    company_document_id: str,
    revision_number: str,
    collection_name: Optional[str] = None,
    filename: Optional[str] = None,   # ✅ ADD THIS
):
    """
    Persist active document for a session.

    🔥 SELF-HEALING: If table is missing, it re-creates it and retries.
    """
    if not session_id or not company_document_id:
        return

    rev_str = str(revision_number)

    print(
        f"💾 [PG] Saving Active Doc: "
        f"Session={session_id}, Doc={company_document_id}, "
        f"Rev={rev_str}, File={filename}"
    )

    def _execute_insert():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO session_active_documents
                        (session_id, company_document_id, revision_number, collection_name, filename)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (session_id)
                    DO UPDATE SET
                        company_document_id = EXCLUDED.company_document_id,
                        revision_number = EXCLUDED.revision_number,
                        collection_name = EXCLUDED.collection_name,
                        filename = EXCLUDED.filename,
                        updated_at = NOW()
                    """,
                    (session_id, company_document_id, rev_str, collection_name, filename),
                )

    try:
        _execute_insert()
    except psycopg2.errors.UndefinedTable:
        print("[PG] Table 'session_active_documents' missing. Re-creating now...")
        _init_db()
        try:
            _execute_insert()
            print(" [PG] Table created and document saved.")
        except Exception as e:
            print(f" [PG] Failed to auto-heal table: {e}")


def get_active_document(session_id: str) -> Optional[Dict[str, object]]:
    """
    Restore active document for a session.
    """
    if not session_id:
        return None

    print(f"🔍 [PG] Fetching Active Doc for Session: {session_id}")

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT company_document_id, revision_number, collection_name, filename
                    FROM session_active_documents
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
        
        if row:
            print(f"    Found: {dict(row)}")
        else:
            print(f"    Not Found (Session {session_id} has no active document)")

        return dict(row) if row else None
        
    except psycopg2.errors.UndefinedTable:
        print("[PG] Table missing during fetch. Returning None.")
        return None

def clear_active_document(session_id: str):
    """
    Remove active document binding.
    """
    if not session_id:
        return

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM session_active_documents
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
    except psycopg2.errors.UndefinedTable:
        pass # If table is gone, the data is gone anyway

# =========================================================
# 🚀 NEW: CHUNK RECOVERY (FOR FOLLOW-UPS)
# =========================================================

def get_chunks_by_ids(chunk_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Fetch full chunk content for a list of IDs.
    Used to restore context for follow-up questions.
    
    NOTE: Connects to RAG_DB_URL because chunks live in the vector DB,
    not the chat memory DB.
    """
    if not chunk_ids:
        return []

    # Safe parameter binding for dynamic list
    placeholders = ",".join(["%s"] * len(chunk_ids))
    
    conn = None
    try:
        # Connect to RAG DB directly
        conn = connect(RAG_DB_URL)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT 
                    cmetadata->>'chunk_id' as id,
                    document as content,
                    cmetadata->>'section' as section,
                    cmetadata->>'chunk_type' as chunk_type,
                    cmetadata->>'page_number' as page_number,
                    cmetadata->>'bbox' as bbox,
                    cmetadata->>'source_file' as source_file,
                    cmetadata->>'company_document_id' as company_doc_id,
                    cmetadata->>'revision_number' as revision
                FROM langchain_pg_embedding
                WHERE cmetadata->>'chunk_id' IN ({placeholders})
                """,
                tuple(chunk_ids)
            )
            rows = cur.fetchall() or []
    except Exception as e:
        print(f" [PG] Failed to fetch chunks by IDs: {e}")
        return []
    finally:
        if conn:
            conn.close()

    # Normalize output format to match retrieval pipeline
    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "content": r["content"],
            "section": r["section"],
            "chunk_type": r.get("chunk_type", "text"),
            "score": 1.0,
            "metadata": {
                "source_file": r["source_file"],
                "page_number": int(r["page_number"]) if r["page_number"] else 1,
                "bbox": r["bbox"],
                "company_document_id": r["company_doc_id"],
                "revision_number": r["revision"],
            }
        })
        
    return results


# =========================================================
# CONVERSATION SUMMARIZATION
# =========================================================

# Auto-create session_summaries table
_SUMMARY_TABLE_CREATED = False

def _ensure_summary_table():
    global _SUMMARY_TABLE_CREATED
    if _SUMMARY_TABLE_CREATED:
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS session_summaries (
                        session_id TEXT PRIMARY KEY,
                        summary TEXT NOT NULL,
                        message_count INTEGER DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
        _SUMMARY_TABLE_CREATED = True
    except Exception as e:
        print(f"[MEMORY] session_summaries table init warning: {e}")


# Config
SUMMARIZE_AFTER = 20    # messages before summarizing older turns
KEEP_VERBATIM  = 10     # most-recent messages kept as-is
MAX_ASSISTANT_CHARS = 200
MAX_USER_CHARS      = 100


def _compress_messages(messages: List[Dict]) -> str:
    """
    Compress older messages into a short context block.
    No LLM — pure truncation and formatting.
    """
    lines = ["[Earlier conversation summary]"]
    for msg in messages:
        role    = msg.get("role", "?")
        content = str(msg.get("content", "")).strip()
        if role == "user":
            snippet = content[:MAX_USER_CHARS]
            lines.append(f"User asked: {snippet}{'…' if len(content) > MAX_USER_CHARS else ''}")
        elif role == "assistant":
            snippet = content[:MAX_ASSISTANT_CHARS]
            lines.append(f"Assistant answered: {snippet}{'…' if len(content) > MAX_ASSISTANT_CHARS else ''}")
    return "\n".join(lines)


def get_summarized_history(
    session_id: str,
    limit: int = 50,
) -> List[Dict[str, str]]:
    """
    Return a history list for injection into the LLM prompt.

    If total messages <= SUMMARIZE_AFTER: return all verbatim.
    If total messages > SUMMARIZE_AFTER:
        - Compress all but the last KEEP_VERBATIM into a summary pseudo-message
        - Return [summary_message] + last KEEP_VERBATIM messages verbatim

    Never raises — falls back to get_chat_messages() on error.
    """
    _ensure_summary_table()

    try:
        all_messages = get_chat_messages(session_id, limit=limit)

        if len(all_messages) <= SUMMARIZE_AFTER:
            return list(all_messages)

        # Split: older (to summarize) + recent (verbatim)
        older  = all_messages[:-KEEP_VERBATIM]
        recent = all_messages[-KEEP_VERBATIM:]

        # Check if we have a cached summary for the same message count
        cached_summary = _get_cached_summary(session_id, len(older))
        if not cached_summary:
            cached_summary = _compress_messages(older)
            _save_cached_summary(session_id, cached_summary, len(older))

        # Inject compressed block as a system message at the front
        compressed_msg = {
            "role": "system",
            "content": cached_summary,
        }

        return [compressed_msg] + list(recent)

    except Exception as e:
        print(f"[MEMORY] get_summarized_history failed (non-fatal): {e}")
        return get_chat_messages(session_id, limit=limit)


def _get_cached_summary(session_id: str, message_count: int) -> Optional[str]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT summary FROM session_summaries
                    WHERE session_id = %s AND message_count = %s
                    """,
                    (session_id, message_count),
                )
                row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _save_cached_summary(session_id: str, summary: str, message_count: int):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO session_summaries (session_id, summary, message_count)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        message_count = EXCLUDED.message_count,
                        updated_at = NOW()
                    """,
                    (session_id, summary, message_count),
                )
    except Exception as e:
        print(f"[MEMORY] _save_cached_summary failed (non-fatal): {e}")


# =========================================================
# 🤖 AGENTIC MEMORY: USER STATE MANAGEMENT
# =========================================================

def get_or_create_user_state(user_id: str) -> Dict[str, Any]:
    """
    Get user state, create default if not exists.
    """
    if not user_id:
        return {
            "expertise_level": "intermediate",
            "preferred_answer_style": "balanced",
            "preferred_detail_level": "medium",
            "interaction_count": 0,
            "preferences": {}
        }
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM user_state WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                
                if row:
                    return dict(row)
                else:
                    # Create default user state
                    cur.execute(
                        """
                        INSERT INTO user_state (user_id)
                        VALUES (%s)
                        """,
                        (user_id,),
                    )
                    return {
                        "user_id": user_id,
                        "expertise_level": "intermediate",
                        "preferred_answer_style": "balanced",
                        "preferred_detail_level": "medium",
                        "interaction_count": 0,
                        "preferences": {}
                    }
    except Exception as e:
        print(f"[MEMORY] get_or_create_user_state failed: {e}")
        return {
            "expertise_level": "intermediate",
            "preferred_answer_style": "balanced",
            "preferred_detail_level": "medium",
            "interaction_count": 0,
            "preferences": {}
        }


def update_user_state(user_id: str, updates: Dict[str, Any]):
    """
    Update user state with new values.
    """
    if not user_id or not updates:
        return
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Build dynamic update query
                set_clauses = []
                values = []
                for key, value in updates.items():
                    if key in ["expertise_level", "preferred_answer_style", "preferred_detail_level"]:
                        set_clauses.append(f"{key} = %s")
                        values.append(value)
                    elif key == "preferences" and isinstance(value, dict):
                        set_clauses.append("preferences = preferences || %s")
                        values.append(value)
                
                if set_clauses:
                    set_clauses.append("last_active = NOW()")
                    values.append(user_id)
                    
                    cur.execute(
                        f"""
                        UPDATE user_state
                        SET {', '.join(set_clauses)}
                        WHERE user_id = %s
                        """,
                        values,
                    )
    except Exception as e:
        print(f"[MEMORY] update_user_state failed: {e}")


def increment_interaction_count(user_id: str):
    """
    Increment user interaction count for learning.
    """
    if not user_id:
        return
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_state
                    SET interaction_count = interaction_count + 1,
                        last_active = NOW()
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
    except Exception as e:
        print(f"[MEMORY] increment_interaction_count failed: {e}")


# =========================================================
# 🤖 AGENTIC MEMORY: FEEDBACK LEARNING
# =========================================================

def save_user_feedback(
    user_id: str,
    session_id: str,
    message_id: int,
    feedback_type: str,
    feedback_value: int
):
    """
    Save user feedback for learning.
    feedback_type: 'thumbs_up', 'thumbs_down', 'quality', 'relevance'
    feedback_value: 1-5 rating or binary (1/0)
    """
    if not user_id or not session_id:
        return
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_feedback (user_id, session_id, message_id, feedback_type, feedback_value)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (user_id, session_id, message_id, feedback_type, feedback_value),
                )
                
                # Update user state based on feedback
                if feedback_type == "thumbs_up" and feedback_value > 0:
                    # Positive feedback - potentially adjust expertise level
                    cur.execute(
                        """
                        UPDATE user_state
                        SET interaction_count = interaction_count + 1
                        WHERE user_id = %s
                        """,
                        (user_id,),
                    )
                    
        print(f"[MEMORY] Saved feedback: user={user_id}, type={feedback_type}, value={feedback_value}")
    except Exception as e:
        print(f"[MEMORY] save_user_feedback failed: {e}")


def get_user_feedback_summary(user_id: str) -> Dict[str, Any]:
    """
    Get summary of user feedback for learning.
    """
    if not user_id:
        return {}
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT 
                        feedback_type,
                        COUNT(*) as count,
                        AVG(feedback_value) as avg_value
                    FROM user_feedback
                    WHERE user_id = %s
                    GROUP BY feedback_type
                    """,
                    (user_id,),
                )
                rows = cur.fetchall() or []
                
                summary = {}
                for row in rows:
                    summary[row["feedback_type"]] = {
                        "count": row["count"],
                        "avg_value": row["avg_value"]
                    }
                
                return summary
    except Exception as e:
        print(f"[MEMORY] get_user_feedback_summary failed: {e}")
        return {}


# =========================================================
# 🤖 AGENTIC MEMORY: PROACTIVE SUGGESTIONS
# =========================================================

def save_proactive_suggestion(
    session_id: str,
    suggestion_type: str,
    suggestion_text: str,
    context: Optional[Dict[str, Any]] = None
):
    """
    Save proactive suggestions for follow-up questions or actions.
    """
    if not session_id or not suggestion_text:
        return
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO session_topic_hints (session_id, topic_hint)
                    VALUES (%s, %s)
                    ON CONFLICT (session_id)
                    DO UPDATE SET
                        topic_hint = EXCLUDED.topic_hint,
                        updated_at = NOW()
                    """,
                    (session_id, f"[{suggestion_type}] {suggestion_text}"),
                )
        print(f"[MEMORY] Saved proactive suggestion: {suggestion_type}")
    except Exception as e:
        print(f"[MEMORY] save_proactive_suggestion failed: {e}")


def get_proactive_suggestions(session_id: str) -> List[str]:
    """
    Get proactive suggestions for a session.
    """
    if not session_id:
        return []
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT topic_hint
                    FROM session_topic_hints
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
                
                if row and row[0]:
                    # Parse suggestions from topic hint
                    hint = row[0]
                    suggestions = []
                    if "[" in hint and "]" in hint:
                        # Extract suggestions in format [type] text
                        parts = hint.split("[")
                        for part in parts[1:]:  # Skip first empty part
                            if "]" in part:
                                type_end = part.index("]")
                                suggestions.append(part[type_end + 1:].strip())
                    else:
                        suggestions.append(hint)
                    return suggestions
                return []
    except Exception as e:
        print(f"[MEMORY] get_proactive_suggestions failed: {e}")
        return []


# =========================================================
# 🤖 AGENTIC MEMORY: SELF-CORRECTION LOGS
# =========================================================

def log_self_correction(
    session_id: str,
    correction_type: str,
    original_text: str,
    corrected_text: str,
    reason: str
):
    """
    Log self-correction actions for learning and debugging.
    """
    if not session_id:
        return
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Store as a system message with special prefix
                correction_message = f"[SELF-CORRECTION:{correction_type}] {reason}"
                cur.execute(
                    """
                    INSERT INTO chat_messages (session_id, role, content)
                    VALUES (%s, 'system', %s)
                    """,
                    (session_id, correction_message),
                )
        print(f"[MEMORY] Logged self-correction: {correction_type}")
    except Exception as e:
        print(f"[MEMORY] log_self_correction failed: {e}")


def get_self_corrections(session_id: str) -> List[Dict[str, str]]:
    """
    Get self-correction logs for a session.
    """
    if not session_id:
        return []
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT content, created_at
                    FROM chat_messages
                    WHERE session_id = %s AND role = 'system' AND content LIKE '[SELF-CORRECTION%'
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    (session_id,),
                )
                rows = cur.fetchall() or []
                
                corrections = []
                for row in rows:
                    content = row["content"]
                    if content.startswith("[SELF-CORRECTION:"):
                        type_end = content.index("]")
                        correction_type = content[17:type_end]
                        reason = content[type_end + 1:].strip()
                        corrections.append({
                            "type": correction_type,
                            "reason": reason,
                            "created_at": row["created_at"]
                        })
                return corrections
    except Exception as e:
        print(f"[MEMORY] get_self_corrections failed: {e}")
        return []
