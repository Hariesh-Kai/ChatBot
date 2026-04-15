from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Optional
import uuid

from backend.auth.deps import require_user
from backend.memory.pg_memory import get_connection
from backend.memory.redis_memory import r as redis_client, reset_rag_state
from backend.state.job_persistence import get_job_run, delete_job_run
from backend.storage.minio_client import get_minio_client, delete_pdf

router = APIRouter(prefix="/session", tags=["Session"])

@router.post("/new")
def new_session():
    return {"session_id": str(uuid.uuid4())}


@router.delete("/{session_id}")
def delete_session(
    session_id: str,
    _user: Any = Depends(require_user)
) -> Dict[str, Any]:
    """
    Cascade delete a session and all associated data:
    - Chat messages and session metadata
    - Redis cache data (topic hints, used chunks, RAG debug)
    - Upload jobs and preprocessing artifacts
    - Uploaded PDF files (if not shared with other sessions)
    
    Returns summary of deleted data
    """
    if not session_id:
        raise HTTPException(400, "session_id is required")
    
    deleted_summary = {
        "session_id": session_id,
        "deleted_items": {
            "chat_messages": 0,
            "chat_session": False,
            "topic_hints": False,
            "active_documents": False,
            "session_summaries": False,
            "redis_cache": False,
            "upload_jobs": 0,
            "preprocessing_artifacts": False,
            "pdf_files": False,
        },
        "warnings": []
    }
    
    try:
        # 1. Delete PostgreSQL chat data
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Delete chat messages
                cur.execute(
                    "DELETE FROM chat_messages WHERE session_id = %s RETURNING id",
                    (session_id,)
                )
                deleted_summary["deleted_items"]["chat_messages"] = len(cur.fetchall())
                
                # Delete chat session
                cur.execute(
                    "DELETE FROM chat_sessions WHERE session_id = %s",
                    (session_id,)
                )
                deleted_summary["deleted_items"]["chat_session"] = cur.rowcount > 0
                
                # Delete topic hints
                cur.execute(
                    "DELETE FROM session_topic_hints WHERE session_id = %s",
                    (session_id,)
                )
                deleted_summary["deleted_items"]["topic_hints"] = cur.rowcount > 0
                
                # Delete active documents
                cur.execute(
                    "DELETE FROM session_active_documents WHERE session_id = %s",
                    (session_id,)
                )
                deleted_summary["deleted_items"]["active_documents"] = cur.rowcount > 0
                
                # Delete session summaries
                try:
                    cur.execute(
                        "DELETE FROM session_summaries WHERE session_id = %s",
                        (session_id,)
                    )
                    deleted_summary["deleted_items"]["session_summaries"] = cur.rowcount > 0
                except Exception:
                    # Table might not exist
                    pass
                
            conn.commit()
        
        # 2. Delete Redis cache data
        if redis_client:
            try:
                reset_rag_state(session_id)
                deleted_summary["deleted_items"]["redis_cache"] = True
            except Exception as e:
                deleted_summary["warnings"].append(f"Redis cleanup warning: {e}")
        
        # 3. Delete upload jobs and preprocessing artifacts
        try:
            job = get_job_run(session_id)
            if job:
                # Get document metadata before deleting job
                company_document_id = job.get("metadata", {}).get("company_document_id")
                revision_number = job.get("metadata", {}).get("revision_number")
                filename = job.get("metadata", {}).get("filename")
                
                # Delete job and artifacts
                delete_job_run(job["job_id"])
                deleted_summary["deleted_items"]["upload_jobs"] = 1
                deleted_summary["deleted_items"]["preprocessing_artifacts"] = True
                
                # Delete PDF file from MinIO if document metadata is available
                if company_document_id and revision_number and filename:
                    try:
                        minio_client = get_minio_client()
                        if minio_client:
                            # Check if file is shared with other sessions before deleting
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "SELECT COUNT(*) FROM session_active_documents WHERE company_document_id = %s AND session_id != %s",
                                        (company_document_id, session_id)
                                    )
                                    other_sessions_count = cur.fetchone()[0] if cur.rowcount > 0 else 0
                            
                            # Only delete if not shared with other sessions
                            if other_sessions_count == 0:
                                delete_pdf(
                                    document_id=company_document_id,
                                    revision=int(revision_number),
                                    filename=filename
                                )
                                deleted_summary["deleted_items"]["pdf_files"] = True
                            else:
                                deleted_summary["warnings"].append(
                                    f"PDF file kept in storage as it is shared with {other_sessions_count} other session(s)"
                                )
                    except Exception as e:
                        deleted_summary["warnings"].append(f"MinIO PDF deletion warning: {e}")
        except Exception as e:
            deleted_summary["warnings"].append(f"Upload job cleanup warning: {e}")
        
        return {
            "status": "success",
            "message": "Session and associated data deleted successfully",
            "summary": deleted_summary
        }
        
    except Exception as e:
        raise HTTPException(500, f"Failed to delete session: {e}")
