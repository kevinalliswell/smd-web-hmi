"""pytest 公共夹具与路径配置。"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

# 确保 backend/ 在 sys.path（使 `import app` 可用）
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.models import Base  # noqa: E402


def free_port() -> int:
    """返回一个本机空闲端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
async def mock_server():
    """启动一个 HostComm Mock Server（关闭周期推送以保证测试确定性）。"""
    from app.hostcomm.mock_server import MockHostCommServer

    srv = MockHostCommServer(port=free_port(), status_interval=None)
    await srv.start()
    try:
        yield srv
    finally:
        await srv.stop()


@pytest.fixture
async def db_session(tmp_path):
    """提供一个建好全表的异步数据库会话（临时文件 SQLite）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


async def make_client(srv, **kwargs):
    """构造一个指向给定 Mock Server 的 HostCommClient（短超时，便于测试）。"""
    from app.hostcomm.client import HostCommClient

    defaults = dict(
        heartbeat_interval=0.2,
        timeout_count=3,
        command_timeout=1.0,
        reconnect_base=0.2,
        reconnect_max=1.0,
    )
    defaults.update(kwargs)
    return HostCommClient("127.0.0.1", srv.port, **defaults)
