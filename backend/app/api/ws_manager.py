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

    def __init__(self, *, send_timeout: float = 1.0) -> None:
        if send_timeout <= 0:
            raise ValueError("send_timeout must be positive")
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._send_timeout = send_timeout

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
                    self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


# 进程级单例
ws_manager = ConnectionManager()
