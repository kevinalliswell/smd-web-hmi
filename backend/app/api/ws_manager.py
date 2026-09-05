"""WebSocket 连接管理：维护客户端集合并广播推送消息（规格第 4 节）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger
from app.hostcomm.protocol import now_iso

logger = get_logger("ws.manager")


@dataclass(frozen=True)
class ConnectionContext:
    """与一条 WebSocket 连接绑定的已验证用户上下文。"""

    username: str
    role: str


class ConnectionLimitExceeded(Exception):
    """单用户实时连接数达到配置上限。"""


class ConnectionManager:
    """管理已认证的 WebSocket 连接，支持向所有客户端广播。"""

    def __init__(self, *, send_timeout: float = 1.0) -> None:
        if send_timeout <= 0:
            raise ValueError("send_timeout must be positive")
        self._connections: dict[WebSocket, ConnectionContext] = {}
        self._pending_by_user: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._send_timeout = send_timeout

    async def connect(
        self,
        ws: WebSocket,
        context: ConnectionContext,
        *,
        max_connections_per_user: int,
        accept: bool = True,
    ) -> None:
        """在原子连接数检查后接纳连接，并保存当前用户上下文。"""
        async with self._lock:
            user_connections = sum(item.username == context.username for item in self._connections.values())
            pending_connections = self._pending_by_user.get(context.username, 0)
            if user_connections + pending_connections >= max_connections_per_user:
                raise ConnectionLimitExceeded(context.username)
            self._pending_by_user[context.username] = pending_connections + 1
        if accept:
            try:
                await ws.accept()
            except BaseException:
                async with self._lock:
                    self._release_pending(context.username)
                raise
        async with self._lock:
            self._release_pending(context.username)
            self._connections[ws] = context
        logger.info(
            "ws.connected",
            username=context.username,
            role=context.role,
            total=len(self._connections),
        )

    def _release_pending(self, username: str) -> None:
        remaining = self._pending_by_user.get(username, 0) - 1
        if remaining > 0:
            self._pending_by_user[username] = remaining
        else:
            self._pending_by_user.pop(username, None)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(ws, None)
        logger.info("ws.disconnected", total=len(self._connections))

    async def broadcast(self, msg_type: str, data: dict[str, Any]) -> None:
        """向所有连接推送 ``{type, ts, data}`` 消息。"""
        message = {"type": msg_type, "ts": now_iso(), "data": data}
        async with self._lock:
            targets = list(self._connections)

        async def send(ws: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=self._send_timeout)
                return None
            except Exception:  # noqa: BLE001
                return ws

        # 每个客户端独立发送；慢连接只消耗自己的超时窗口。
        dead = [ws for ws in await asyncio.gather(*(send(ws) for ws in targets)) if ws is not None]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.pop(ws, None)

    def context_for(self, ws: WebSocket) -> ConnectionContext | None:
        """返回连接对应的已验证用户上下文。"""
        return self._connections.get(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


# 进程级单例
ws_manager = ConnectionManager()
