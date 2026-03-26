from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.auth.deps import AUTH_COOKIE_NAME, require_user
from backend.auth.tokens import verify_token
from backend.auth.user_store import User, get_user_for_token
from backend.messaging.store import (
    create_conversation,
    create_message,
    create_project,
    get_user_conversation_ids,
    get_workspace,
    mark_read,
    update_project_assignees,
    update_project_status,
    user_has_conversation,
)
from backend.messaging.ws_manager import manager


router = APIRouter(prefix="/team", tags=["Team Messaging"])


def _normalize_username(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _get_auth_secret() -> str:
    return os.getenv("CHAT_UI_AUTH_SECRET", "").strip()


def _resolve_ws_user(websocket: WebSocket) -> Optional[User]:
    secret = _get_auth_secret()
    if not secret:
        return None

    token = websocket.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None

    payload = verify_token(token, secret=secret)
    if not payload:
        return None

    sub = payload.get("sub")
    email = payload.get("email")
    user = get_user_for_token(sub, email)
    if not user:
        return None

    return user


class CreateConversationRequest(BaseModel):
    name: str
    participant_ids: List[str] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    conversation_id: str
    content: str
    project_id: Optional[str] = None


class MarkReadRequest(BaseModel):
    conversation_id: str


class CreateProjectRequest(BaseModel):
    conversation_id: str
    name: str
    description: Optional[str] = None
    assignee_ids: List[str] = Field(default_factory=list)
    priority: str = "medium"
    due_date: Optional[str] = None


class UpdateProjectStatusRequest(BaseModel):
    status: str


class UpdateProjectAssigneesRequest(BaseModel):
    assignee_ids: List[str] = Field(default_factory=list)


@router.get("/workspace")
def team_workspace(user: User = Depends(require_user)):
    try:
        return get_workspace(user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load workspace: {exc}")


@router.post("/conversations")
def team_create_conversation(
    payload: CreateConversationRequest,
    user: User = Depends(require_user),
):
    try:
        conversation_id = create_conversation(
            user,
            name=payload.name,
            participant_ids=payload.participant_ids,
        )
        event = {
            "type": "CONVERSATION_CREATED",
            "conversationId": conversation_id,
            "createdBy": _normalize_username(user.username),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {exc}")

    # Broadcast based on newly assigned members by subscribing all user sockets on refresh requests.
    # For immediate visibility, sending a lightweight global conversation event to active sockets
    # of existing conversation members is not possible without extra user-channel mapping.
    # Clients refresh workspace after receiving this event.
    # Since this endpoint is user-initiated, also return latest workspace snapshot directly.
    return {"ok": True, "event": event, "workspace": get_workspace(user)}


@router.post("/messages")
async def team_send_message(
    payload: SendMessageRequest,
    user: User = Depends(require_user),
):
    try:
        message = create_message(
            user,
            conversation_id=payload.conversation_id,
            content=payload.content,
            project_id=payload.project_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {exc}")

    event = {
        "type": "MESSAGE_CREATED",
        "conversationId": message["conversationId"],
        "message": message,
    }
    await manager.broadcast_conversation(
        conversation_id=message["conversationId"],
        payload=event,
    )
    return {"ok": True, "message": message}


@router.post("/read")
async def team_mark_read(
    payload: MarkReadRequest,
    user: User = Depends(require_user),
):
    try:
        read_event = mark_read(user, conversation_id=payload.conversation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to mark read: {exc}")

    event = {"type": "READ_UPDATED", **read_event}
    await manager.broadcast_conversation(
        conversation_id=read_event["conversationId"],
        payload=event,
    )
    return {"ok": True, **read_event}


@router.post("/projects")
async def team_create_project(
    payload: CreateProjectRequest,
    user: User = Depends(require_user),
):
    try:
        result = create_project(
            user,
            conversation_id=payload.conversation_id,
            name=payload.name,
            description=payload.description,
            assignee_ids=payload.assignee_ids,
            priority=payload.priority,
            due_date=payload.due_date,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create project: {exc}")

    event = {
        "type": "PROJECT_CREATED",
        "conversationId": result["project"]["conversationId"],
        "project": result["project"],
        "message": result["message"],
        "projectCounter": result["projectCounter"],
    }
    await manager.broadcast_conversation(
        conversation_id=result["project"]["conversationId"],
        payload=event,
    )
    return {"ok": True, **result}


@router.patch("/projects/{project_id}/status")
async def team_update_project_status(
    project_id: str,
    payload: UpdateProjectStatusRequest,
    user: User = Depends(require_user),
):
    try:
        result = update_project_status(user, project_id=project_id, status=payload.status)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update project: {exc}")

    event = {
        "type": "PROJECT_UPDATED",
        "conversationId": result["project"]["conversationId"],
        "project": result["project"],
        "message": result["message"],
    }
    await manager.broadcast_conversation(
        conversation_id=result["project"]["conversationId"],
        payload=event,
    )
    return {"ok": True, **result}


@router.patch("/projects/{project_id}/assignees")
async def team_update_project_assignees(
    project_id: str,
    payload: UpdateProjectAssigneesRequest,
    user: User = Depends(require_user),
):
    try:
        result = update_project_assignees(
            user,
            project_id=project_id,
            assignee_ids=payload.assignee_ids,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update project assignees: {exc}")

    event = {
        "type": "PROJECT_UPDATED",
        "conversationId": result["project"]["conversationId"],
        "project": result["project"],
        "message": result["message"],
    }
    await manager.broadcast_conversation(
        conversation_id=result["project"]["conversationId"],
        payload=event,
    )
    return {"ok": True, **result}


@router.websocket("/ws")
async def team_ws(websocket: WebSocket):
    user = _resolve_ws_user(websocket)
    if not user:
        await websocket.close(code=4401)
        return

    user_key = _normalize_username(user.username)
    if not user_key:
        await websocket.close(code=4401)
        return

    await manager.connect(websocket, user_key=user_key)

    try:
        conversation_ids = get_user_conversation_ids(user)
        for conversation_id in conversation_ids:
            await manager.subscribe(websocket, conversation_id=conversation_id)

        await manager.emit(
            websocket,
            {
                "type": "CONNECTED",
                "username": user_key,
                "conversationIds": conversation_ids,
                "serverTs": int(time.time() * 1000),
            },
        )

        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.emit(
                    websocket,
                    {"type": "ERROR", "message": "Invalid JSON payload"},
                )
                continue

            event_type = str(data.get("type") or "").strip().upper()

            if event_type == "PING":
                await manager.emit(
                    websocket,
                    {"type": "PONG", "serverTs": int(time.time() * 1000)},
                )
                continue

            if event_type == "SUBSCRIBE":
                conversation_id = str(data.get("conversationId") or "").strip()
                if not conversation_id:
                    await manager.emit(
                        websocket,
                        {"type": "ERROR", "message": "conversationId is required"},
                    )
                    continue
                if not user_has_conversation(user, conversation_id):
                    await manager.emit(
                        websocket,
                        {"type": "ERROR", "message": "Access denied for this conversation"},
                    )
                    continue
                await manager.subscribe(websocket, conversation_id=conversation_id)
                await manager.emit(
                    websocket,
                    {"type": "SUBSCRIBED", "conversationId": conversation_id},
                )
                continue

            if event_type == "UNSUBSCRIBE":
                conversation_id = str(data.get("conversationId") or "").strip()
                if conversation_id:
                    await manager.unsubscribe(websocket, conversation_id=conversation_id)
                await manager.emit(
                    websocket,
                    {"type": "UNSUBSCRIBED", "conversationId": conversation_id},
                )
                continue

            if event_type == "SEND_MESSAGE":
                try:
                    message = create_message(
                        user,
                        conversation_id=str(data.get("conversationId") or ""),
                        content=str(data.get("content") or ""),
                        project_id=str(data.get("projectId") or "") or None,
                    )
                except PermissionError as exc:
                    await manager.emit(websocket, {"type": "ERROR", "message": str(exc)})
                    continue
                except ValueError as exc:
                    await manager.emit(websocket, {"type": "ERROR", "message": str(exc)})
                    continue
                await manager.broadcast_conversation(
                    conversation_id=message["conversationId"],
                    payload={
                        "type": "MESSAGE_CREATED",
                        "conversationId": message["conversationId"],
                        "message": message,
                    },
                )
                continue

            if event_type == "MARK_READ":
                try:
                    read_event = mark_read(
                        user,
                        conversation_id=str(data.get("conversationId") or ""),
                    )
                except PermissionError as exc:
                    await manager.emit(websocket, {"type": "ERROR", "message": str(exc)})
                    continue
                except ValueError as exc:
                    await manager.emit(websocket, {"type": "ERROR", "message": str(exc)})
                    continue
                await manager.broadcast_conversation(
                    conversation_id=read_event["conversationId"],
                    payload={"type": "READ_UPDATED", **read_event},
                )
                continue

            if event_type == "REFRESH_WORKSPACE":
                snapshot = get_workspace(user)
                await manager.emit(websocket, {"type": "WORKSPACE_SNAPSHOT", "workspace": snapshot})
                continue

            await manager.emit(
                websocket,
                {"type": "ERROR", "message": f"Unsupported event type: {event_type or 'UNKNOWN'}"},
            )

    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, user_key=user_key)
