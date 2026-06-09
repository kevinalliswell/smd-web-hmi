"""最新状态快照内存缓存（asyncio.Lock 保护）。

规格见开发规格说明书第 5.5 节：存储最近一次 status_snapshot，超过 5s 未更新
则 comm_quality 视为 degraded。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

DEGRADED_AFTER_S = 5.0


class StatusCache:
    """协程安全的最新状态缓存。"""

    def __init__(self, degraded_after_s: float = DEGRADED_AFTER_S) -> None:
        self._lock = asyncio.Lock()
        self._snapshot: dict[str, Any] = {}
        self._last_update_monotonic: float | None = None
        self._last_update_iso: str | None = None
        self._degraded_after_s = degraded_after_s

    async def update(self, snapshot: dict[str, Any], ts_iso: str | None = None) -> None:
        async with self._lock:
            self._snapshot = snapshot
            self._last_update_monotonic = time.monotonic()
            self._last_update_iso = ts_iso

    async def get_snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return dict(self._snapshot)

    def get_field(self, path: str) -> Any:
        """按点号路径读取字段，例如 ``system.current_state``。"""
        node: Any = self._snapshot
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    @property
    def last_update(self) -> str | None:
        return self._last_update_iso

    def comm_quality(self, link_online: bool) -> str:
        """结合链路状态与缓存新鲜度计算综合通信质量。"""
        if not link_online:
            return "offline"
        if self._last_update_monotonic is None:
            return "degraded"
        if (time.monotonic() - self._last_update_monotonic) > self._degraded_after_s:
            return "degraded"
        return "online"


# 进程级单例（FastAPI 应用与 HostComm 客户端共享）
status_cache = StatusCache()
