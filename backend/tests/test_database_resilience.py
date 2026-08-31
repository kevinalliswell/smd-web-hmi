"""SQLite 并发配置与采样持久化健康状态测试。"""

from __future__ import annotations

from sqlalchemy import text

from app.db import database
from app.services.sampling_health import SamplingPersistenceHealth


async def test_sqlite_engine_enables_wal_and_busy_timeout(tmp_path):
    """应用创建的 SQLite 连接必须允许读写并发并等待短暂锁冲突。"""
    engine = database._create_engine(f"sqlite+aiosqlite:///{tmp_path}/wal.db")
    try:
        async with engine.connect() as connection:
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
            busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))
        assert str(journal_mode).lower() == "wal"
        assert busy_timeout == 5000
    finally:
        await engine.dispose()


def test_sampling_health_raises_once_and_clears_on_recovery():
    """连续失败达到阈值只触发一次报警，成功写入后触发一次恢复。"""
    health = SamplingPersistenceHealth(failure_threshold=3)

    assert health.record_failure() is False
    assert health.record_failure() is False
    assert health.record_failure() is True
    assert health.record_failure() is False
    assert health.snapshot() == {
        "status": "degraded",
        "consecutive_write_failures": 4,
        "alarm_active": True,
    }

    assert health.record_success() is True
    assert health.record_success() is False
    assert health.snapshot() == {
        "status": "ok",
        "consecutive_write_failures": 0,
        "alarm_active": False,
    }
