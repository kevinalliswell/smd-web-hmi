"""试验运行时状态：跟踪当前进行中的试验 id。

单事件循环内的轻量状态。权威来源仍是数据库（test_session.end_time 为 NULL 即进行中）
与 STM32 状态快照（state_machine.test_id）；本注册表用于让实时采样写库快速取到 test_id。
"""

from __future__ import annotations


class TestRuntime:
    """进程级当前试验跟踪。"""

    def __init__(self) -> None:
        self._active_test_id: str | None = None

    @property
    def active_test_id(self) -> str | None:
        return self._active_test_id

    def start(self, test_id: str) -> None:
        self._active_test_id = test_id

    def stop(self) -> str | None:
        tid = self._active_test_id
        self._active_test_id = None
        return tid


# 进程级单例
active_test = TestRuntime()
