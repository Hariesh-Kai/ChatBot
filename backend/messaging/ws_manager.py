from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket


class TeamWSManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections_by_user: Dict[str, Set[WebSocket]] = {}
        self._connections_by_conversation: Dict[str, Set[WebSocket]] = {}
        self._subscriptions_by_socket: Dict[WebSocket, Set[str]] = {}

    async def connect(self, websocket: WebSocket, *, user_key: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections_by_user.setdefault(user_key, set()).add(websocket)
            self._subscriptions_by_socket.setdefault(websocket, set())

    async def disconnect(self, websocket: WebSocket, *, user_key: str) -> None:
        async with self._lock:
            user_sockets = self._connections_by_user.get(user_key)
            if user_sockets and websocket in user_sockets:
                user_sockets.remove(websocket)
                if not user_sockets:
                    self._connections_by_user.pop(user_key, None)

            subscribed = self._subscriptions_by_socket.pop(websocket, set())
            for conversation_id in subscribed:
                sockets = self._connections_by_conversation.get(conversation_id)
                if sockets and websocket in sockets:
                    sockets.remove(websocket)
                    if not sockets:
                        self._connections_by_conversation.pop(conversation_id, None)

    async def subscribe(self, websocket: WebSocket, *, conversation_id: str) -> None:
        clean_conversation_id = (conversation_id or "").strip()
        if not clean_conversation_id:
            return
        async with self._lock:
            self._connections_by_conversation.setdefault(clean_conversation_id, set()).add(websocket)
            self._subscriptions_by_socket.setdefault(websocket, set()).add(clean_conversation_id)

    async def unsubscribe(self, websocket: WebSocket, *, conversation_id: str) -> None:
        clean_conversation_id = (conversation_id or "").strip()
        if not clean_conversation_id:
            return
        async with self._lock:
            subscriptions = self._subscriptions_by_socket.get(websocket)
            if subscriptions and clean_conversation_id in subscriptions:
                subscriptions.remove(clean_conversation_id)
            sockets = self._connections_by_conversation.get(clean_conversation_id)
            if sockets and websocket in sockets:
                sockets.remove(websocket)
                if not sockets:
                    self._connections_by_conversation.pop(clean_conversation_id, None)

    async def emit(self, websocket: WebSocket, payload: Dict[str, Any]) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            return False

    async def broadcast_conversation(
        self,
        *,
        conversation_id: str,
        payload: Dict[str, Any],
        exclude_user_key: Optional[str] = None,
    ) -> None:
        clean_conversation_id = (conversation_id or "").strip()
        if not clean_conversation_id:
            return

        async with self._lock:
            recipients = set(self._connections_by_conversation.get(clean_conversation_id, set()))
            excluded_sockets: Set[WebSocket] = set()
            if exclude_user_key:
                excluded_sockets = set(self._connections_by_user.get(exclude_user_key, set()))
            recipients = {ws for ws in recipients if ws not in excluded_sockets}

        stale: Set[WebSocket] = set()
        for ws in recipients:
            ok = await self.emit(ws, payload)
            if not ok:
                stale.add(ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    for user_key, user_sockets in list(self._connections_by_user.items()):
                        if ws in user_sockets:
                            user_sockets.remove(ws)
                            if not user_sockets:
                                self._connections_by_user.pop(user_key, None)
                    subscriptions = self._subscriptions_by_socket.get(ws, set())
                    for conv_id in list(subscriptions):
                        conv_sockets = self._connections_by_conversation.get(conv_id)
                        if conv_sockets and ws in conv_sockets:
                            conv_sockets.remove(ws)
                            if not conv_sockets:
                                self._connections_by_conversation.pop(conv_id, None)
                    self._subscriptions_by_socket.pop(ws, None)


manager = TeamWSManager()
