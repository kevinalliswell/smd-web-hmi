"""异步数据库引擎与会话依赖。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """为每个 SQLite 连接启用读写并发和有界锁等待。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def _create_engine(db_url: str) -> AsyncEngine:
    engine = create_async_engine(db_url, echo=False, future=True)
    if engine.url.get_backend_name() == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine


def get_engine() -> AsyncEngine:
    """返回（惰性创建的）全局 AsyncEngine。"""
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = _create_engine(settings.db_url)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """返回全局 sessionmaker。"""
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：提供一个异步会话，结束时自动关闭。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def create_all() -> None:
    """开发/测试用：按 ORM 元数据直接建表（生产用 alembic upgrade head）。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """释放引擎连接池（应用关闭时调用）。"""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


@lru_cache(maxsize=1)
def get_expected_schema_head() -> str:
    """返回代码随附迁移链的唯一 head revision。"""
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    # alembic.ini uses a relative path for CLI portability.  Resolve it here
    # because the packaged service may be launched from any working directory.
    config.set_main_option("script_location", str(backend_dir / "app" / "db" / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head:
        raise RuntimeError("未找到 Alembic head revision")
    return head


async def get_schema_status(engine: AsyncEngine | None = None) -> dict[str, str | bool | None]:
    """返回数据库迁移版本状态，不泄露连接串或本地路径。"""
    target = engine or get_engine()
    expected = get_expected_schema_head()
    current: str | None = None
    try:
        async with target.connect() as connection:
            await connection.execute(text("SELECT 1"))
            current = await connection.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    except Exception:  # noqa: BLE001
        current = None
    return {"ok": current == expected, "current": current, "expected": expected}


async def assert_schema_current(engine: AsyncEngine | None = None) -> None:
    """生产启动门禁：数据库必须已由安装/升级流程迁移到当前 head。"""
    status = await get_schema_status(engine)
    if not status["ok"]:
        raise RuntimeError(
            "数据库 schema 未迁移到当前版本；请在启动服务前执行 alembic upgrade head "
            f"(current={status['current'] or 'missing'}, expected={status['expected']})"
        )
