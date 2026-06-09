"""WebSocket 连接管理：维护客户端集合并广播推送消息（规格第 4 节）。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger
from app.hostcomm.protocol import now_iso

logger = get_logger("ws.manager")


class ConnectionManager:
    """管理已认证的 WebSocket 连接，支持向所有客户端广播。"""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("ws.connected", total=len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        logger.info("ws.disconnected", total=len(self._connections))

    async def broadcast(self, msg_type: str, data: dict[str, Any]) -> None:
        """向所有连接推送 ``{type, ts, data}`` 消息。"""
        message = {"type": msg_type, "ts": now_iso(), "data": data}
        async with self._lock:
            targets = list(self._connections)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


# 进程级单例
ws_manager = ConnectionManager()
