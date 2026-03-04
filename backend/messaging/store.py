from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from psycopg2.extras import RealDictCursor

from backend.auth.user_store import (
    User,
    ROLE_PIPE_DESIGNER,
    ROLE_PIPE_STRESS_ENGINEER,
    ROLE_PIPE_LEAD,
    ROLE_PIPING_ADMIN,
    normalize_role,
    get_configured_user,
    get_user_by_identifier,
    list_users,
)
from backend.memory.pg_memory import get_connection


_INIT_LOCK = Lock()
_SCHEMA_READY = False

_VALID_PRIORITIES = {"low", "medium", "high", "critical"}
_VALID_STATUSES = {"planning", "active", "blocked", "done"}

_COLOR_STOPS = [
    "from-emerald-500 to-teal-500",
    "from-cyan-500 to-blue-500",
    "from-indigo-500 to-sky-500",
    "from-orange-500 to-amber-500",
    "from-pink-500 to-rose-500",
    "from-violet-500 to-fuchsia-500",
    "from-lime-500 to-emerald-500",
]


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _normalize_username(value: Optional[str]) -> str:
    return _normalize_text(value).lower()


def _slug(value: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-") or "member"


def _member_id(username: Optional[str], email: Optional[str] = None) -> str:
    base = _normalize_text(username) or _normalize_text(email) or "member"
    return f"member-{_slug(base)}"


def _to_ms(value: Optional[datetime]) -> int:
    if not value:
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _role_title(role: str) -> str:
    r = normalize_role(role)
    if r == ROLE_PIPING_ADMIN:
        return "Piping Admin"
    if r == ROLE_PIPE_LEAD:
        return "Pipe Lead"
    if r == ROLE_PIPE_STRESS_ENGINEER:
        return "Pipe Stress Engineer"
    if r == ROLE_PIPE_DESIGNER:
        return "Pipe Designer"
    return "Team Member"


def _role_department(role: str) -> str:
    r = normalize_role(role)
    if r == ROLE_PIPING_ADMIN:
        return "Piping Administration"
    if r == ROLE_PIPE_LEAD:
        return "Piping Lead Office"
    if r in (ROLE_PIPE_STRESS_ENGINEER, ROLE_PIPE_DESIGNER):
        return "Piping Engineering"
    return "Engineering"


def _role_color(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(_COLOR_STOPS)
    return _COLOR_STOPS[idx]


def _member_payload_from_user(user: User) -> Dict[str, Any]:
    username = _normalize_text(user.username)
    email = _normalize_text(user.email)
    member_id = _member_id(username, email)
    return {
        "id": member_id,
        "name": username or email or "Member",
        "title": _role_title(user.role),
        "department": _role_department(user.role),
        "color": _role_color(username or email or member_id),
    }


def _member_payload_fallback(username: str) -> Dict[str, Any]:
    member_id = _member_id(username, username)
    return {
        "id": member_id,
        "name": username or "Unknown Member",
        "title": "Team Member",
        "department": "Operations",
        "color": _role_color(username or member_id),
    }


def _all_active_users() -> List[User]:
    out: List[User] = []
    seen: Set[str] = set()

    admin = get_configured_user()
    admin_key = _normalize_username(admin.username)
    seen.add(admin_key)
    out.append(admin)

    for item in list_users():
        if bool(item.get("disabled", False)):
            continue
        username = _normalize_text(item.get("username"))
        email = _normalize_text(item.get("email"))
        if not username:
            continue
        key = _normalize_username(username)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            User(
                username=username,
                email=email,
                role=_normalize_text(item.get("role")) or ROLE_PIPE_DESIGNER,
                disabled=False,
            )
        )
    return out


def _resolve_users(usernames: Iterable[str]) -> Dict[str, User]:
    keys = {_normalize_username(value) for value in usernames if _normalize_text(value)}
    if not keys:
        return {}

    resolved: Dict[str, User] = {}

    for candidate in _all_active_users():
        key = _normalize_username(candidate.username)
        if key in keys:
            resolved[key] = candidate

    missing = [key for key in keys if key not in resolved]
    for key in missing:
        found = get_user_by_identifier(key)
        if found:
            resolved[key] = found

    return resolved


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _INIT_LOCK:
        if _SCHEMA_READY:
            return

        queries = [
            """
            CREATE TABLE IF NOT EXISTS team_conversations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS team_conversation_members (
                conversation_id TEXT NOT NULL REFERENCES team_conversations(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                email TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, username)
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS team_conv_members_username_idx
            ON team_conversation_members (username);
            """,
            """
            CREATE TABLE IF NOT EXISTS team_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES team_conversations(id) ON DELETE CASCADE,
                sender_username TEXT NOT NULL,
                sender_email TEXT,
                content TEXT NOT NULL,
                project_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS team_messages_conv_created_idx
            ON team_messages (conversation_id, created_at);
            """,
            """
            CREATE TABLE IF NOT EXISTS team_message_reads (
                conversation_id TEXT NOT NULL REFERENCES team_conversations(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                last_read_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, username)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS team_projects (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL REFERENCES team_conversations(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                owner_username TEXT NOT NULL,
                owner_email TEXT,
                assignee_usernames TEXT[] NOT NULL DEFAULT '{}',
                priority TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'planning',
                due_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS team_projects_conv_idx
            ON team_projects (conversation_id, created_at DESC);
            """,
            """
            CREATE TABLE IF NOT EXISTS team_workspace_meta (
                singleton BOOLEAN PRIMARY KEY DEFAULT TRUE,
                project_counter INTEGER NOT NULL DEFAULT 1066
            );
            """,
            """
            INSERT INTO team_workspace_meta (singleton, project_counter)
            VALUES (TRUE, 1066)
            ON CONFLICT (singleton) DO NOTHING;
            """,
            """
            UPDATE team_conversations
            SET name = 'Team Workspace'
            WHERE name = 'Enterprise Coordination';
            """,
            """
            UPDATE team_messages
            SET content = 'Welcome to team messaging. Start coordinating work here.'
            WHERE content = 'Welcome to enterprise team messaging. Start coordinating work here.';
            """,
        ]

        with get_connection() as conn:
            with conn.cursor() as cur:
                for query in queries:
                    cur.execute(query)

        _SCHEMA_READY = True


def _reserve_project_code(cur) -> Tuple[str, int]:
    cur.execute(
        """
        SELECT project_counter
        FROM team_workspace_meta
        WHERE singleton = TRUE
        FOR UPDATE
        """
    )
    row = cur.fetchone()
    if isinstance(row, dict):
        raw_value = row.get("project_counter")
    else:
        raw_value = row[0] if row else None
    current = int(raw_value) if raw_value is not None else 1066
    cur.execute(
        """
        UPDATE team_workspace_meta
        SET project_counter = %s
        WHERE singleton = TRUE
        """,
        (current + 1,),
    )
    return f"PRJ-{current}", current + 1


def _get_project_counter(cur) -> int:
    cur.execute(
        """
        SELECT project_counter
        FROM team_workspace_meta
        WHERE singleton = TRUE
        """
    )
    row = cur.fetchone()
    if isinstance(row, dict):
        raw_value = row.get("project_counter")
    else:
        raw_value = row[0] if row else None
    return int(raw_value) if raw_value is not None else 1066


def _ensure_user_has_workspace(user: User) -> None:
    ensure_schema()
    username_key = _normalize_username(user.username)
    if not username_key:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT conversation_id
                FROM team_conversation_members
                WHERE username = %s
                LIMIT 1
                """,
                (username_key,),
            )
            member_row = cur.fetchone()
            if member_row:
                return

            cur.execute(
                """
                SELECT id
                FROM team_conversations
                ORDER BY created_at ASC
                LIMIT 1
                """
            )
            first_conversation = cur.fetchone()

            if first_conversation:
                conv_id = str(first_conversation[0])
                cur.execute(
                    """
                    INSERT INTO team_conversation_members (conversation_id, username, email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (conversation_id, username) DO NOTHING
                    """,
                    (conv_id, username_key, _normalize_text(user.email) or None),
                )
                cur.execute(
                    """
                    INSERT INTO team_message_reads (conversation_id, username, last_read_at, updated_at)
                    VALUES (%s, %s, NOW(), NOW())
                    ON CONFLICT (conversation_id, username)
                    DO UPDATE SET
                        last_read_at = EXCLUDED.last_read_at,
                        updated_at = NOW()
                    """,
                    (conv_id, username_key),
                )
                return

            conv_id = str(uuid.uuid4())
            conv_name = "Team Workspace"
            created_by = username_key

            cur.execute(
                """
                INSERT INTO team_conversations (id, name, created_by)
                VALUES (%s, %s, %s)
                """,
                (conv_id, conv_name, created_by),
            )

            users = _all_active_users()
            participant_keys: List[str] = []
            for candidate in users:
                key = _normalize_username(candidate.username)
                if key and key not in participant_keys:
                    participant_keys.append(key)

            if username_key not in participant_keys:
                participant_keys.append(username_key)

            for key in participant_keys:
                resolved = get_user_by_identifier(key)
                email = _normalize_text(resolved.email) if resolved else None
                cur.execute(
                    """
                    INSERT INTO team_conversation_members (conversation_id, username, email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (conversation_id, username) DO NOTHING
                    """,
                    (conv_id, key, email or None),
                )
                cur.execute(
                    """
                    INSERT INTO team_message_reads (conversation_id, username, last_read_at, updated_at)
                    VALUES (%s, %s, NOW(), NOW())
                    ON CONFLICT (conversation_id, username)
                    DO UPDATE SET
                        last_read_at = EXCLUDED.last_read_at,
                        updated_at = NOW()
                    """,
                    (conv_id, key),
                )

            welcome_message_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO team_messages (id, conversation_id, sender_username, sender_email, content, project_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    welcome_message_id,
                    conv_id,
                    username_key,
                    _normalize_text(user.email) or None,
                    "Welcome to team messaging. Start coordinating work here.",
                    None,
                ),
            )


def get_user_conversation_ids(user: User) -> List[str]:
    ensure_schema()
    _ensure_user_has_workspace(user)
    username_key = _normalize_username(user.username)
    if not username_key:
        return []

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT conversation_id
                FROM team_conversation_members
                WHERE username = %s
                ORDER BY conversation_id ASC
                """,
                (username_key,),
            )
            return [str(row[0]) for row in (cur.fetchall() or []) if row and row[0]]


def user_has_conversation(user: User, conversation_id: str) -> bool:
    ensure_schema()
    clean_conversation_id = _normalize_text(conversation_id)
    username_key = _normalize_username(user.username)
    if not clean_conversation_id or not username_key:
        return False

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM team_conversation_members
                WHERE conversation_id = %s AND username = %s
                LIMIT 1
                """,
                (clean_conversation_id, username_key),
            )
            row = cur.fetchone()
            return bool(row)


def _build_workspace(
    *,
    user: User,
    conversation_rows: List[Dict[str, Any]],
    participants_rows: List[Dict[str, Any]],
    message_rows: List[Dict[str, Any]],
    read_rows: List[Dict[str, Any]],
    project_rows: List[Dict[str, Any]],
    project_counter: int,
) -> Dict[str, Any]:
    participants_by_conversation: Dict[str, List[str]] = {}
    usernames: Set[str] = set()

    for row in participants_rows:
        conversation_id = str(row.get("conversation_id") or "")
        username = _normalize_username(row.get("username"))
        if not conversation_id or not username:
            continue
        participants_by_conversation.setdefault(conversation_id, []).append(username)
        usernames.add(username)

    for row in message_rows:
        sender = _normalize_username(row.get("sender_username"))
        if sender:
            usernames.add(sender)

    for row in project_rows:
        owner = _normalize_username(row.get("owner_username"))
        if owner:
            usernames.add(owner)
        for assignee in row.get("assignee_usernames") or []:
            clean_assignee = _normalize_username(assignee)
            if clean_assignee:
                usernames.add(clean_assignee)

    users_by_username = _resolve_users(usernames)
    members_by_id: Dict[str, Dict[str, Any]] = {}
    member_id_by_username: Dict[str, str] = {}

    for username in sorted(usernames):
        resolved = users_by_username.get(username)
        if resolved:
            payload = _member_payload_from_user(resolved)
        else:
            payload = _member_payload_fallback(username)
        members_by_id[payload["id"]] = payload
        member_id_by_username[username] = payload["id"]

    current_username = _normalize_username(user.username)
    if current_username and current_username not in member_id_by_username:
        current_payload = _member_payload_from_user(user)
        members_by_id[current_payload["id"]] = current_payload
        member_id_by_username[current_username] = current_payload["id"]

    messages_by_conversation: Dict[str, List[Dict[str, Any]]] = {}
    for row in message_rows:
        conversation_id = str(row.get("conversation_id") or "")
        sender_username = _normalize_username(row.get("sender_username"))
        sender_id = member_id_by_username.get(sender_username, _member_id(sender_username, sender_username))
        payload = {
            "id": str(row.get("id") or ""),
            "senderId": sender_id,
            "content": str(row.get("content") or ""),
            "createdAt": _to_ms(row.get("created_at")),
            "projectId": str(row.get("project_id")) if row.get("project_id") else None,
        }
        messages_by_conversation.setdefault(conversation_id, []).append(payload)

    reads_by_conversation: Dict[str, Dict[str, int]] = {}
    for row in read_rows:
        conversation_id = str(row.get("conversation_id") or "")
        username = _normalize_username(row.get("username"))
        member_id = member_id_by_username.get(username)
        if not conversation_id or not member_id:
            continue
        reads_by_conversation.setdefault(conversation_id, {})[member_id] = _to_ms(
            row.get("last_read_at")
        )

    project_ids_by_conversation: Dict[str, List[str]] = {}
    projects_payload: List[Dict[str, Any]] = []
    for row in project_rows:
        conversation_id = str(row.get("conversation_id") or "")
        owner_username = _normalize_username(row.get("owner_username"))
        owner_id = member_id_by_username.get(owner_username, _member_id(owner_username, owner_username))
        assignee_ids = [
            member_id_by_username.get(_normalize_username(value), _member_id(value, value))
            for value in (row.get("assignee_usernames") or [])
            if _normalize_text(value)
        ]
        payload = {
            "id": str(row.get("id") or ""),
            "code": str(row.get("code") or ""),
            "name": str(row.get("name") or ""),
            "description": str(row.get("description") or ""),
            "conversationId": conversation_id,
            "ownerId": owner_id,
            "assigneeIds": assignee_ids,
            "priority": str(row.get("priority") or "medium"),
            "status": str(row.get("status") or "planning"),
            "dueDate": row.get("due_date").isoformat() if row.get("due_date") else None,
            "createdAt": _to_ms(row.get("created_at")),
        }
        projects_payload.append(payload)
        if conversation_id:
            project_ids_by_conversation.setdefault(conversation_id, []).append(payload["id"])

    conversations_payload: List[Dict[str, Any]] = []
    for row in conversation_rows:
        conversation_id = str(row.get("id") or "")
        participant_usernames = participants_by_conversation.get(conversation_id, [])
        participant_ids = [
            member_id_by_username.get(key, _member_id(key, key))
            for key in participant_usernames
        ]
        payload = {
            "id": conversation_id,
            "name": str(row.get("name") or "Team Conversation"),
            "participantIds": participant_ids,
            "projectIds": project_ids_by_conversation.get(conversation_id, []),
            "messages": messages_by_conversation.get(conversation_id, []),
            "updatedAt": _to_ms(row.get("updated_at")),
            "lastSeenAt": reads_by_conversation.get(conversation_id, {}),
        }
        conversations_payload.append(payload)

    conversations_payload.sort(key=lambda item: item["updatedAt"], reverse=True)

    return {
        "members": list(members_by_id.values()),
        "conversations": conversations_payload,
        "projects": sorted(projects_payload, key=lambda item: item["createdAt"], reverse=True),
        "activeConversationId": conversations_payload[0]["id"] if conversations_payload else None,
        "projectCounter": int(project_counter),
    }


def get_workspace(user: User, *, message_limit_per_conversation: int = 150) -> Dict[str, Any]:
    ensure_schema()
    _ensure_user_has_workspace(user)
    username_key = _normalize_username(user.username)
    if not username_key:
        raise ValueError("User username is required")

    limit = max(20, min(int(message_limit_per_conversation), 500))

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.id, c.name, c.updated_at
                FROM team_conversations c
                INNER JOIN team_conversation_members m
                    ON c.id = m.conversation_id
                WHERE m.username = %s
                ORDER BY c.updated_at DESC
                """,
                (username_key,),
            )
            conversation_rows = cur.fetchall() or []

            conversation_ids = [str(row["id"]) for row in conversation_rows if row.get("id")]
            if not conversation_ids:
                return {
                    "members": [],
                    "conversations": [],
                    "projects": [],
                    "activeConversationId": None,
                    "projectCounter": _get_project_counter(cur),
                }

            cur.execute(
                """
                SELECT conversation_id, username
                FROM team_conversation_members
                WHERE conversation_id = ANY(%s)
                ORDER BY conversation_id, username
                """,
                (conversation_ids,),
            )
            participants_rows = cur.fetchall() or []

            cur.execute(
                """
                SELECT id, conversation_id, sender_username, content, project_id, created_at
                FROM (
                    SELECT
                        id,
                        conversation_id,
                        sender_username,
                        content,
                        project_id,
                        created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY conversation_id
                            ORDER BY created_at DESC
                        ) AS rn
                    FROM team_messages
                    WHERE conversation_id = ANY(%s)
                ) latest
                WHERE rn <= %s
                ORDER BY conversation_id ASC, created_at ASC
                """,
                (conversation_ids, limit),
            )
            message_rows = cur.fetchall() or []

            cur.execute(
                """
                SELECT conversation_id, username, last_read_at
                FROM team_message_reads
                WHERE conversation_id = ANY(%s)
                """,
                (conversation_ids,),
            )
            read_rows = cur.fetchall() or []

            cur.execute(
                """
                SELECT
                    id,
                    code,
                    conversation_id,
                    name,
                    description,
                    owner_username,
                    assignee_usernames,
                    priority,
                    status,
                    due_date,
                    created_at
                FROM team_projects
                WHERE conversation_id = ANY(%s)
                ORDER BY created_at DESC
                """,
                (conversation_ids,),
            )
            project_rows = cur.fetchall() or []

            counter = _get_project_counter(cur)

    return _build_workspace(
        user=user,
        conversation_rows=conversation_rows,
        participants_rows=participants_rows,
        message_rows=message_rows,
        read_rows=read_rows,
        project_rows=project_rows,
        project_counter=counter,
    )


def create_conversation(user: User, *, name: str, participant_ids: List[str]) -> str:
    ensure_schema()
    clean_name = _normalize_text(name)
    if not clean_name:
        raise ValueError("Conversation name is required")

    username_key = _normalize_username(user.username)
    if not username_key:
        raise ValueError("User username is required")

    users = _all_active_users()
    by_member_id: Dict[str, User] = {}
    by_username: Dict[str, User] = {}
    for candidate in users:
        member_id = _member_id(candidate.username, candidate.email)
        by_member_id[member_id] = candidate
        by_username[_normalize_username(candidate.username)] = candidate

    participants: List[User] = []
    seen_usernames: Set[str] = set()

    for member_id in participant_ids or []:
        clean_member_id = _normalize_text(member_id)
        if not clean_member_id:
            continue
        candidate = by_member_id.get(clean_member_id)
        if not candidate:
            continue
        key = _normalize_username(candidate.username)
        if key and key not in seen_usernames:
            seen_usernames.add(key)
            participants.append(candidate)

    if username_key not in seen_usernames:
        participants.append(user)
        seen_usernames.add(username_key)

    if not participants:
        participants = [user]

    conversation_id = str(uuid.uuid4())

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO team_conversations (id, name, created_by)
                VALUES (%s, %s, %s)
                """,
                (conversation_id, clean_name, username_key),
            )
            for participant in participants:
                participant_key = _normalize_username(participant.username)
                cur.execute(
                    """
                    INSERT INTO team_conversation_members (conversation_id, username, email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (conversation_id, username) DO NOTHING
                    """,
                    (
                        conversation_id,
                        participant_key,
                        _normalize_text(participant.email) or None,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO team_message_reads (conversation_id, username, last_read_at, updated_at)
                    VALUES (%s, %s, NOW(), NOW())
                    ON CONFLICT (conversation_id, username)
                    DO UPDATE SET
                        last_read_at = EXCLUDED.last_read_at,
                        updated_at = NOW()
                    """,
                    (conversation_id, participant_key),
                )

    return conversation_id


def _message_payload_from_row(
    *,
    row: Dict[str, Any],
    sender_username: str,
    sender_email: Optional[str],
) -> Dict[str, Any]:
    sender_id = _member_id(sender_username, sender_email)
    return {
        "id": str(row.get("id") or ""),
        "senderId": sender_id,
        "content": str(row.get("content") or ""),
        "createdAt": _to_ms(row.get("created_at")),
        "projectId": str(row.get("project_id")) if row.get("project_id") else None,
    }


def create_message(
    user: User,
    *,
    conversation_id: str,
    content: str,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_schema()
    clean_conversation_id = _normalize_text(conversation_id)
    clean_content = _normalize_text(content)
    clean_project_id = _normalize_text(project_id) or None
    username_key = _normalize_username(user.username)

    if not clean_conversation_id:
        raise ValueError("conversation_id is required")
    if not clean_content:
        raise ValueError("Message content is required")
    if not username_key:
        raise ValueError("User username is required")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT 1
                FROM team_conversation_members
                WHERE conversation_id = %s AND username = %s
                LIMIT 1
                """,
                (clean_conversation_id, username_key),
            )
            if not cur.fetchone():
                raise PermissionError("User is not a member of this conversation")

            if clean_project_id:
                cur.execute(
                    """
                    SELECT 1
                    FROM team_projects
                    WHERE id = %s AND conversation_id = %s
                    LIMIT 1
                    """,
                    (clean_project_id, clean_conversation_id),
                )
                if not cur.fetchone():
                    raise ValueError("project_id does not belong to this conversation")

            message_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO team_messages (
                    id,
                    conversation_id,
                    sender_username,
                    sender_email,
                    content,
                    project_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, content, project_id, created_at
                """,
                (
                    message_id,
                    clean_conversation_id,
                    username_key,
                    _normalize_text(user.email) or None,
                    clean_content,
                    clean_project_id,
                ),
            )
            row = cur.fetchone() or {}

            cur.execute(
                """
                UPDATE team_conversations
                SET updated_at = NOW()
                WHERE id = %s
                """,
                (clean_conversation_id,),
            )

            cur.execute(
                """
                INSERT INTO team_message_reads (conversation_id, username, last_read_at, updated_at)
                VALUES (%s, %s, NOW(), NOW())
                ON CONFLICT (conversation_id, username)
                DO UPDATE SET
                    last_read_at = EXCLUDED.last_read_at,
                    updated_at = NOW()
                """,
                (clean_conversation_id, username_key),
            )

    payload = _message_payload_from_row(
        row=row,
        sender_username=username_key,
        sender_email=user.email,
    )
    payload["conversationId"] = clean_conversation_id
    return payload


def mark_read(user: User, *, conversation_id: str) -> Dict[str, Any]:
    ensure_schema()
    clean_conversation_id = _normalize_text(conversation_id)
    username_key = _normalize_username(user.username)
    if not clean_conversation_id:
        raise ValueError("conversation_id is required")
    if not username_key:
        raise ValueError("User username is required")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT 1
                FROM team_conversation_members
                WHERE conversation_id = %s AND username = %s
                LIMIT 1
                """,
                (clean_conversation_id, username_key),
            )
            if not cur.fetchone():
                raise PermissionError("User is not a member of this conversation")

            cur.execute(
                """
                SELECT MAX(created_at) AS max_created_at
                FROM team_messages
                WHERE conversation_id = %s
                """,
                (clean_conversation_id,),
            )
            row = cur.fetchone() or {}
            max_created_at = row.get("max_created_at")
            if not max_created_at:
                max_created_at = datetime.now(tz=timezone.utc)

            cur.execute(
                """
                INSERT INTO team_message_reads (conversation_id, username, last_read_at, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (conversation_id, username)
                DO UPDATE SET
                    last_read_at = GREATEST(team_message_reads.last_read_at, EXCLUDED.last_read_at),
                    updated_at = NOW()
                RETURNING last_read_at
                """,
                (clean_conversation_id, username_key, max_created_at),
            )
            saved = cur.fetchone() or {}

    return {
        "conversationId": clean_conversation_id,
        "memberId": _member_id(user.username, user.email),
        "readAt": _to_ms(saved.get("last_read_at")),
    }


def _parse_due_date(raw_due_date: Optional[str]) -> Optional[date]:
    clean = _normalize_text(raw_due_date)
    if not clean:
        return None
    try:
        return date.fromisoformat(clean)
    except Exception as exc:
        raise ValueError("due_date must use YYYY-MM-DD format") from exc


def create_project(
    user: User,
    *,
    conversation_id: str,
    name: str,
    description: Optional[str],
    assignee_ids: List[str],
    priority: str,
    due_date: Optional[str],
) -> Dict[str, Any]:
    ensure_schema()
    clean_conversation_id = _normalize_text(conversation_id)
    clean_name = _normalize_text(name)
    clean_description = _normalize_text(description)
    clean_priority = _normalize_text(priority).lower() or "medium"
    username_key = _normalize_username(user.username)
    if not clean_conversation_id:
        raise ValueError("conversation_id is required")
    if not clean_name:
        raise ValueError("Project name is required")
    if not username_key:
        raise ValueError("User username is required")
    if clean_priority not in _VALID_PRIORITIES:
        raise ValueError("priority must be one of: low, medium, high, critical")

    users = _all_active_users()
    users_by_member_id: Dict[str, User] = {}
    for candidate in users:
        users_by_member_id[_member_id(candidate.username, candidate.email)] = candidate

    assignee_usernames: List[str] = []
    for assignee_id in assignee_ids or []:
        candidate = users_by_member_id.get(_normalize_text(assignee_id))
        if not candidate:
            continue
        username = _normalize_username(candidate.username)
        if username and username not in assignee_usernames:
            assignee_usernames.append(username)

    if not assignee_usernames:
        raise ValueError("At least one assignee is required")

    parsed_due_date = _parse_due_date(due_date)

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT 1
                FROM team_conversation_members
                WHERE conversation_id = %s AND username = %s
                LIMIT 1
                """,
                (clean_conversation_id, username_key),
            )
            if not cur.fetchone():
                raise PermissionError("User is not a member of this conversation")

            project_id = str(uuid.uuid4())
            project_code, next_counter = _reserve_project_code(cur)

            for username in assignee_usernames:
                resolved = get_user_by_identifier(username)
                cur.execute(
                    """
                    INSERT INTO team_conversation_members (conversation_id, username, email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (conversation_id, username) DO NOTHING
                    """,
                    (
                        clean_conversation_id,
                        username,
                        _normalize_text(resolved.email) if resolved else None,
                    ),
                )

            cur.execute(
                """
                INSERT INTO team_projects (
                    id,
                    code,
                    conversation_id,
                    name,
                    description,
                    owner_username,
                    owner_email,
                    assignee_usernames,
                    priority,
                    status,
                    due_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'planning', %s)
                RETURNING
                    id,
                    code,
                    conversation_id,
                    name,
                    description,
                    owner_username,
                    assignee_usernames,
                    priority,
                    status,
                    due_date,
                    created_at
                """,
                (
                    project_id,
                    project_code,
                    clean_conversation_id,
                    clean_name,
                    clean_description,
                    username_key,
                    _normalize_text(user.email) or None,
                    assignee_usernames,
                    clean_priority,
                    parsed_due_date,
                ),
            )
            project_row = cur.fetchone() or {}

            summary = ", ".join(assignee_usernames)
            project_message_id = str(uuid.uuid4())
            project_message_text = (
                f"[Project Setup] {clean_name}. {project_code} assigned to {summary}."
            )

            cur.execute(
                """
                INSERT INTO team_messages (
                    id,
                    conversation_id,
                    sender_username,
                    sender_email,
                    content,
                    project_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, content, project_id, created_at
                """,
                (
                    project_message_id,
                    clean_conversation_id,
                    username_key,
                    _normalize_text(user.email) or None,
                    project_message_text,
                    project_id,
                ),
            )
            project_message_row = cur.fetchone() or {}

            cur.execute(
                """
                UPDATE team_conversations
                SET updated_at = NOW()
                WHERE id = %s
                """,
                (clean_conversation_id,),
            )

            cur.execute(
                """
                INSERT INTO team_message_reads (conversation_id, username, last_read_at, updated_at)
                VALUES (%s, %s, NOW(), NOW())
                ON CONFLICT (conversation_id, username)
                DO UPDATE SET
                    last_read_at = EXCLUDED.last_read_at,
                    updated_at = NOW()
                """,
                (clean_conversation_id, username_key),
            )

    owner_id = _member_id(username_key, user.email)
    assignee_member_ids = [_member_id(value, value) for value in assignee_usernames]

    project_payload = {
        "id": str(project_row.get("id") or project_id),
        "code": str(project_row.get("code") or project_code),
        "name": str(project_row.get("name") or clean_name),
        "description": str(project_row.get("description") or clean_description),
        "conversationId": clean_conversation_id,
        "ownerId": owner_id,
        "assigneeIds": assignee_member_ids,
        "priority": str(project_row.get("priority") or clean_priority),
        "status": str(project_row.get("status") or "planning"),
        "dueDate": project_row.get("due_date").isoformat() if project_row.get("due_date") else None,
        "createdAt": _to_ms(project_row.get("created_at")),
    }

    project_message_payload = _message_payload_from_row(
        row=project_message_row,
        sender_username=username_key,
        sender_email=user.email,
    )
    project_message_payload["conversationId"] = clean_conversation_id

    return {
        "project": project_payload,
        "message": project_message_payload,
        "projectCounter": next_counter,
    }


def update_project_status(
    user: User,
    *,
    project_id: str,
    status: str,
) -> Dict[str, Any]:
    ensure_schema()
    clean_project_id = _normalize_text(project_id)
    clean_status = _normalize_text(status).lower()
    username_key = _normalize_username(user.username)

    if not clean_project_id:
        raise ValueError("project_id is required")
    if clean_status not in _VALID_STATUSES:
        raise ValueError("status must be one of: planning, active, blocked, done")
    if not username_key:
        raise ValueError("User username is required")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    conversation_id,
                    name,
                    description,
                    owner_username,
                    assignee_usernames,
                    priority,
                    status,
                    due_date,
                    created_at
                FROM team_projects
                WHERE id = %s
                LIMIT 1
                """,
                (clean_project_id,),
            )
            existing = cur.fetchone()
            if not existing:
                raise ValueError("Project not found")

            conversation_id = str(existing.get("conversation_id") or "")
            cur.execute(
                """
                SELECT 1
                FROM team_conversation_members
                WHERE conversation_id = %s AND username = %s
                LIMIT 1
                """,
                (conversation_id, username_key),
            )
            if not cur.fetchone():
                raise PermissionError("User is not a member of this conversation")

            cur.execute(
                """
                UPDATE team_projects
                SET status = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id,
                    code,
                    conversation_id,
                    name,
                    description,
                    owner_username,
                    assignee_usernames,
                    priority,
                    status,
                    due_date,
                    created_at
                """,
                (clean_status, clean_project_id),
            )
            updated = cur.fetchone() or {}

            status_message_id = str(uuid.uuid4())
            status_message_text = (
                f"[Project Status] {updated.get('code')}: {clean_status.upper()}."
            )
            cur.execute(
                """
                INSERT INTO team_messages (
                    id,
                    conversation_id,
                    sender_username,
                    sender_email,
                    content,
                    project_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, content, project_id, created_at
                """,
                (
                    status_message_id,
                    conversation_id,
                    username_key,
                    _normalize_text(user.email) or None,
                    status_message_text,
                    clean_project_id,
                ),
            )
            status_message_row = cur.fetchone() or {}

            cur.execute(
                """
                UPDATE team_conversations
                SET updated_at = NOW()
                WHERE id = %s
                """,
                (conversation_id,),
            )

    owner_username = _normalize_username(updated.get("owner_username"))
    owner_id = _member_id(owner_username, owner_username)
    assignee_ids = [
        _member_id(value, value)
        for value in (updated.get("assignee_usernames") or [])
        if _normalize_text(value)
    ]

    project_payload = {
        "id": str(updated.get("id") or clean_project_id),
        "code": str(updated.get("code") or ""),
        "name": str(updated.get("name") or ""),
        "description": str(updated.get("description") or ""),
        "conversationId": str(updated.get("conversation_id") or ""),
        "ownerId": owner_id,
        "assigneeIds": assignee_ids,
        "priority": str(updated.get("priority") or "medium"),
        "status": str(updated.get("status") or clean_status),
        "dueDate": updated.get("due_date").isoformat() if updated.get("due_date") else None,
        "createdAt": _to_ms(updated.get("created_at")),
    }

    message_payload = _message_payload_from_row(
        row=status_message_row,
        sender_username=username_key,
        sender_email=user.email,
    )
    message_payload["conversationId"] = project_payload["conversationId"]

    return {"project": project_payload, "message": message_payload}


def update_project_assignees(
    user: User,
    *,
    project_id: str,
    assignee_ids: List[str],
) -> Dict[str, Any]:
    ensure_schema()
    clean_project_id = _normalize_text(project_id)
    username_key = _normalize_username(user.username)

    if not clean_project_id:
        raise ValueError("project_id is required")
    if not username_key:
        raise ValueError("User username is required")

    users = _all_active_users()
    users_by_member_id: Dict[str, User] = {}
    for candidate in users:
        users_by_member_id[_member_id(candidate.username, candidate.email)] = candidate

    assignee_usernames: List[str] = []
    for assignee_id in assignee_ids or []:
        candidate = users_by_member_id.get(_normalize_text(assignee_id))
        if not candidate:
            continue
        clean_username = _normalize_username(candidate.username)
        if clean_username and clean_username not in assignee_usernames:
            assignee_usernames.append(clean_username)

    if not assignee_usernames:
        raise ValueError("At least one assignee is required")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    conversation_id,
                    name,
                    description,
                    owner_username,
                    assignee_usernames,
                    priority,
                    status,
                    due_date,
                    created_at
                FROM team_projects
                WHERE id = %s
                LIMIT 1
                """,
                (clean_project_id,),
            )
            existing = cur.fetchone()
            if not existing:
                raise ValueError("Project not found")

            conversation_id = str(existing.get("conversation_id") or "")
            cur.execute(
                """
                SELECT 1
                FROM team_conversation_members
                WHERE conversation_id = %s AND username = %s
                LIMIT 1
                """,
                (conversation_id, username_key),
            )
            if not cur.fetchone():
                raise PermissionError("User is not a member of this conversation")

            for username in assignee_usernames:
                resolved = get_user_by_identifier(username)
                cur.execute(
                    """
                    INSERT INTO team_conversation_members (conversation_id, username, email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (conversation_id, username) DO NOTHING
                    """,
                    (
                        conversation_id,
                        username,
                        _normalize_text(resolved.email) if resolved else None,
                    ),
                )

            cur.execute(
                """
                UPDATE team_projects
                SET assignee_usernames = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id,
                    code,
                    conversation_id,
                    name,
                    description,
                    owner_username,
                    assignee_usernames,
                    priority,
                    status,
                    due_date,
                    created_at
                """,
                (assignee_usernames, clean_project_id),
            )
            updated = cur.fetchone() or {}

            summary = ", ".join(assignee_usernames)
            assignment_message_id = str(uuid.uuid4())
            assignment_message_text = (
                f"[Project Assignment] {updated.get('code')}: assigned to {summary}."
            )
            cur.execute(
                """
                INSERT INTO team_messages (
                    id,
                    conversation_id,
                    sender_username,
                    sender_email,
                    content,
                    project_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, content, project_id, created_at
                """,
                (
                    assignment_message_id,
                    conversation_id,
                    username_key,
                    _normalize_text(user.email) or None,
                    assignment_message_text,
                    clean_project_id,
                ),
            )
            assignment_message_row = cur.fetchone() or {}

            cur.execute(
                """
                UPDATE team_conversations
                SET updated_at = NOW()
                WHERE id = %s
                """,
                (conversation_id,),
            )

    owner_username = _normalize_username(updated.get("owner_username"))
    owner_id = _member_id(owner_username, owner_username)
    assignee_member_ids = [
        _member_id(value, value)
        for value in (updated.get("assignee_usernames") or [])
        if _normalize_text(value)
    ]

    project_payload = {
        "id": str(updated.get("id") or clean_project_id),
        "code": str(updated.get("code") or ""),
        "name": str(updated.get("name") or ""),
        "description": str(updated.get("description") or ""),
        "conversationId": str(updated.get("conversation_id") or ""),
        "ownerId": owner_id,
        "assigneeIds": assignee_member_ids,
        "priority": str(updated.get("priority") or "medium"),
        "status": str(updated.get("status") or "planning"),
        "dueDate": updated.get("due_date").isoformat() if updated.get("due_date") else None,
        "createdAt": _to_ms(updated.get("created_at")),
    }

    message_payload = _message_payload_from_row(
        row=assignment_message_row,
        sender_username=username_key,
        sender_email=user.email,
    )
    message_payload["conversationId"] = project_payload["conversationId"]

    return {"project": project_payload, "message": message_payload}
