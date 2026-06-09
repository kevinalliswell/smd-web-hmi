"""消息分发辅助。

注：实时分发主逻辑内联在 ``HostCommClient._dispatch`` 中（按 type 路由）。
本模块提供一个独立可测的分发表抽象，便于后续将 handler 从 client 解耦。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]


class MessageDispatcher:
    """按报文 ``type`` 注册并分发 handler。"""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, msg_type: str, handler: Handler) -> None:
        self._handlers[msg_type] = handler

    def has(self, msg_type: str) -> bool:
        return msg_type in self._handlers

    async def dispatch(self, frame: dict[str, Any]) -> None:
        handler = self._handlers.get(frame.get("type", ""))
        if handler is None:
            return
        result = handler(frame)
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]
