"""心跳相关常量与辅助。

心跳主循环实现于 ``HostCommClient._heartbeat_loop``。本模块集中心跳策略常量，
便于配置与测试引用（协议第 4.2 节：建议周期 2s，连续 3 次超时判异常）。
"""

from __future__ import annotations

DEFAULT_HEARTBEAT_INTERVAL = 2.0
DEFAULT_TIMEOUT_COUNT = 3


def is_stale(last_ack_monotonic: float | None, now_monotonic: float, interval: float, count: int) -> bool:
    """判断心跳是否已过期（超过 interval*count 未收到 ack）。"""
    if last_ack_monotonic is None:
        return False
    return (now_monotonic - last_ack_monotonic) > interval * count
