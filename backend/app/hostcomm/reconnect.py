"""重连退避策略辅助。

重连主循环实现于 ``HostCommClient._reconnect_loop``。本模块提供纯函数式的
指数退避计算（1→2→4→8→…→上限），便于单测。
"""

from __future__ import annotations

from collections.abc import Iterator


def backoff_delays(base: float = 1.0, factor: float = 2.0, maximum: float = 30.0) -> Iterator[float]:
    """生成指数退避延迟序列（封顶 ``maximum``，无限迭代）。"""
    delay = base
    while True:
        yield delay
        delay = min(delay * factor, maximum)
