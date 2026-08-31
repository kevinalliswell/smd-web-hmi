"""试验运行时状态：跟踪当前进行中的试验 id。

单事件循环内的轻量状态。权威来源仍是数据库（test_session.end_time 为 NULL 即进行中）
与 STM32 状态快照（state_machine.test_id）；本注册表用于让实时采样写库快速取到 test_id。
"""

from __future__ import annotations


class TestRuntime:
    """进程级当前试验跟踪。"""

    def __init__(self) -> None:
        self._active_test_id: str | None = None
        self._needs_device_reconcile = False

    @property
    def active_test_id(self) -> str | None:
        return self._active_test_id

    @property
    def needs_device_reconcile(self) -> bool:
        return self._needs_device_reconcile

    def start(self, test_id: str) -> None:
        self._active_test_id = test_id
        self._needs_device_reconcile = False

    def restore(self, test_id: str, *, needs_device_reconcile: bool) -> None:
        self._active_test_id = test_id
        self._needs_device_reconcile = needs_device_reconcile

    def stop(self) -> str | None:
        tid = self._active_test_id
        self._active_test_id = None
        self._needs_device_reconcile = False
        return tid


# 进程级单例
active_test = TestRuntime()
