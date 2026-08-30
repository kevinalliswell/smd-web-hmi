"""实时快照持久化健康状态。

数据库不可写时不能依赖数据库自身记录报警，因此先在内存中统计连续失败，
由调用方通过 WebSocket 向操作员告警；REST 状态接口同时暴露当前健康状态。
"""

from __future__ import annotations


class SamplingPersistenceHealth:
    """跟踪连续写入失败，并返回报警/恢复的边沿触发信号。"""

    def __init__(self, *, failure_threshold: int = 3) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        self.failure_threshold = failure_threshold
        self.reset()

    def record_failure(self) -> bool:
        """记录一次失败；仅在首次达到阈值时返回 ``True``。"""
        self.consecutive_write_failures += 1
        if self.consecutive_write_failures >= self.failure_threshold and not self.alarm_active:
            self.alarm_active = True
            return True
        return False

    def record_success(self) -> bool:
        """记录一次成功；若需要发送恢复通知则返回 ``True``。"""
        recovered = self.alarm_active
        self.consecutive_write_failures = 0
        self.alarm_active = False
        return recovered

    def reset(self) -> None:
        self.consecutive_write_failures = 0
        self.alarm_active = False

    def snapshot(self) -> dict[str, int | bool | str]:
        return {
            "status": "degraded" if self.consecutive_write_failures else "ok",
            "consecutive_write_failures": self.consecutive_write_failures,
            "alarm_active": self.alarm_active,
        }


sampling_health = SamplingPersistenceHealth()
