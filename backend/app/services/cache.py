"""最新状态快照内存缓存（asyncio.Lock 保护）。

规格见开发规格说明书第 5.5 节：存储最近一次 status_snapshot，超过 5s 未更新
则 comm_quality 视为 degraded。
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from app.services.state_policy import snapshot_state

DEGRADED_AFTER_S = 5.0


class StatusCache:
    """协程安全的最新状态缓存。"""

    def __init__(self, degraded_after_s: float = DEGRADED_AFTER_S) -> None:
        self._lock = asyncio.Lock()
        self._snapshot: dict[str, Any] = {}
        self._last_update_monotonic: float | None = None
        self._last_update_iso: str | None = None
        self._invalidated_at = 0.0
        self._degraded_after_s = degraded_after_s

    async def update(self, snapshot: dict[str, Any], ts_iso: str | None = None) -> None:
        async with self._lock:
            receipt = snapshot.get("_hostcomm") or {}
            # 没有链路回执的快照是此刻本地构造的，不是"失效前排队的旧回调"，不受下面的判定约束。
            from_link = "received_monotonic" in receipt
            received = receipt.get("received_monotonic", time.monotonic())
            # 失效之后，只有失效时刻「之后」才从链路收到的快照才能重新授权控制。
            # 必须是 <= 而不是 <：time.monotonic() 的分辨率在 Windows 上约 15.6 ms，
            # 失效前最后一帧的回执时刻与 invalidate() 经常落在同一个 tick 上、读数完全相等；
            # 严格小于会把这种旧帧当作新帧接受，等于拿失效前的状态重新授权控制。
            # 代价是与失效同一 tick 到达的新帧也会被丢掉一帧，方向上是 fail closed。
            if from_link and isinstance(received, (int, float)) and received <= self._invalidated_at:
                return
            self._snapshot = snapshot
            self._last_update_monotonic = (
                min(received, time.monotonic())
                if isinstance(received, (int, float)) and math.isfinite(received) and received >= 0
                else None
            )
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
    def is_fresh(self) -> bool:
        return self._last_update_monotonic is not None and (
            time.monotonic() - self._last_update_monotonic <= self._degraded_after_s
        )

    @property
    def current_state(self) -> str | None:
        return snapshot_state(self._snapshot) if self.is_fresh else None

    def invalidate(self) -> None:
        """会话断开/数据积压时旧状态不能继续授权控制。"""
        self._last_update_monotonic = None
        self._invalidated_at = time.monotonic()

    @property
    def last_update(self) -> str | None:
        return self._last_update_iso

    def comm_quality(self, link_online: bool) -> str:
        """结合链路状态与缓存新鲜度计算综合通信质量。"""
        if not link_online:
            return "offline"
        return "online" if self.is_fresh else "degraded"


# 进程级单例（FastAPI 应用与 HostComm 客户端共享）
status_cache = StatusCache()
