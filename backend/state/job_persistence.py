from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from backend.memory.pg_memory import get_connection

_TABLE = "rag_job_runs"


def init_job_runs_table() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    progress_label TEXT,
                    error TEXT,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{_TABLE}_session_updated
                ON {_TABLE} (session_id, updated_at DESC);
                """
            )


def _to_json_obj(value: Any, default: Any) -> str:
    try:
        return json.dumps(value if value is not None else default)
    except Exception:
        return json.dumps(default)


def upsert_job_run(
    *,
    job_id: str,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    progress_label: Optional[str] = None,
    error: Optional[str] = None,
    replace_error: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    missing_fields: Optional[List[str]] = None,
) -> None:
    init_job_runs_table()

    clean_job_id = (job_id or "").strip()
    if not clean_job_id:
        return

    safe_progress: Optional[int]
    if progress is None:
        safe_progress = None
    else:
        try:
            safe_progress = max(0, min(100, int(progress)))
        except Exception:
            safe_progress = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_TABLE} (
                    job_id,
                    session_id,
                    status,
                    progress,
                    progress_label,
                    error,
                    metadata,
                    missing_fields,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    COALESCE(%s, 'PROCESSING'),
                    COALESCE(%s, 0),
                    %s,
                    %s,
                    %s::jsonb,
                    %s::jsonb,
                    NOW()
                )
                ON CONFLICT (job_id)
                DO UPDATE SET
                    session_id = COALESCE(EXCLUDED.session_id, {_TABLE}.session_id),
                    status = COALESCE(EXCLUDED.status, {_TABLE}.status),
                    progress = COALESCE(EXCLUDED.progress, {_TABLE}.progress),
                    progress_label = COALESCE(EXCLUDED.progress_label, {_TABLE}.progress_label),
                    error = CASE
                        WHEN %s THEN EXCLUDED.error
                        ELSE {_TABLE}.error
                    END,
                    metadata = CASE
                        WHEN %s::jsonb = '{{}}'::jsonb THEN {_TABLE}.metadata
                        ELSE %s::jsonb
                    END,
                    missing_fields = CASE
                        WHEN %s::jsonb = '[]'::jsonb THEN {_TABLE}.missing_fields
                        ELSE %s::jsonb
                    END,
                    updated_at = NOW();
                """,
                (
                    clean_job_id,
                    (session_id or "").strip() or None,
                    status,
                    safe_progress,
                    progress_label,
                    error,
                    _to_json_obj(metadata, {}),
                    _to_json_obj(missing_fields, []),
                    bool(replace_error),
                    _to_json_obj(metadata, {}),
                    _to_json_obj(metadata, {}),
                    _to_json_obj(missing_fields, []),
                    _to_json_obj(missing_fields, []),
                ),
            )


def get_job_run(job_id: str) -> Optional[Dict[str, Any]]:
    init_job_runs_table()
    clean_job_id = (job_id or "").strip()
    if not clean_job_id:
        return None

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    job_id,
                    session_id,
                    status,
                    progress,
                    progress_label,
                    error,
                    metadata,
                    missing_fields,
                    created_at,
                    updated_at
                FROM {_TABLE}
                WHERE job_id = %s
                LIMIT 1;
                """,
                (clean_job_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_latest_job_run_for_session(session_id: str) -> Optional[Dict[str, Any]]:
    init_job_runs_table()
    clean_session_id = (session_id or "").strip()
    if not clean_session_id:
        return None

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    job_id,
                    session_id,
                    status,
                    progress,
                    progress_label,
                    error,
                    metadata,
                    missing_fields,
                    created_at,
                    updated_at
                FROM {_TABLE}
                WHERE session_id = %s
                ORDER BY updated_at DESC
                LIMIT 1;
                """,
                (clean_session_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def delete_job_run(job_id: str) -> None:
    init_job_runs_table()
    clean_job_id = (job_id or "").strip()
    if not clean_job_id:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {_TABLE} WHERE job_id = %s;",
                (clean_job_id,),
            )

