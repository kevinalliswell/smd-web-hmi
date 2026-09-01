"""数据库 schema 门禁、在线备份与产物清理测试。"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.routes import system as system_route
from app.db import database
from app.services.maintenance_service import MaintenanceManager


async def test_schema_status_detects_missing_and_current_revision(tmp_path) -> None:
    engine = database._create_engine(f"sqlite+aiosqlite:///{tmp_path}/schema.db")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))

        missing = await database.get_schema_status(engine)
        assert missing["ok"] is False
        assert missing["expected"] == "d74293c580aa"

        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": missing["expected"]},
            )
        current = await database.get_schema_status(engine)
        assert current == {
            "ok": True,
            "current": "d74293c580aa",
            "expected": "d74293c580aa",
        }
    finally:
        await engine.dispose()


async def test_assert_schema_current_fails_closed_for_unmigrated_database(tmp_path) -> None:
    engine = database._create_engine(f"sqlite+aiosqlite:///{tmp_path}/unmigrated.db")
    try:
        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            await database.assert_schema_current(engine)
    finally:
        await engine.dispose()


async def test_online_backup_is_valid_and_cleanup_respects_retention(tmp_path) -> None:
    source = tmp_path / "smd.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO samples(value) VALUES ('kept')")

    backup_dir = tmp_path / "backups"
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    old_export = export_dir / "old.zip"
    recent_export = export_dir / "recent.zip"
    old_export.write_bytes(b"old")
    recent_export.write_bytes(b"recent")
    old_time = time.time() - 10 * 86400
    os.utime(old_export, (old_time, old_time))

    manager = MaintenanceManager()
    backup = await manager.create_backup(source, backup_dir)
    removed = await manager.cleanup_files(export_dir, retention_days=7)

    assert backup.exists()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM samples").fetchone()[0] == "kept"
    assert removed == 1
    assert not old_export.exists()
    assert recent_export.exists()
    assert manager.snapshot()["last_backup_path"] == str(backup)


async def test_readiness_explains_low_storage(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        data_dir=tmp_path,
        hostcomm_mock=False,
        smd_storage_min_free_bytes=100,
    )

    async def schema_status():
        return {"ok": True, "current": "d74293c580aa", "expected": "d74293c580aa"}

    monkeypatch.setattr(system_route, "get_settings", lambda: settings)
    monkeypatch.setattr(system_route, "get_schema_status", schema_status)
    monkeypatch.setattr(system_route.os, "access", lambda *_args: True)
    monkeypatch.setattr(system_route.shutil, "disk_usage", lambda _path: SimpleNamespace(free=50))
    monkeypatch.setattr(
        system_route.maintenance_manager,
        "snapshot",
        lambda: {"last_backup_at": "2026-09-01T00:00:00+00:00", "last_error": None},
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(hostcomm_client=SimpleNamespace(comm_quality="online")))
    )

    response = await system_route.health(request)

    assert response["data"]["status"] == "not_ready"
    assert response["data"]["checks"]["storage"] == "low"
    assert response["data"]["checks"]["storage_free_bytes"] == 50


async def test_restart_recovers_recent_verified_backup_state(tmp_path) -> None:
    source = tmp_path / "smd.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    backup_dir = tmp_path / "backups"
    exports_dir = tmp_path / "exports"
    first_manager = MaintenanceManager()
    existing = await first_manager.create_backup(source, backup_dir)
    settings = SimpleNamespace(
        backups_dir=backup_dir,
        exports_dir=exports_dir,
        db_path_resolved=source,
        smd_backup_interval_hours=24,
        smd_backup_retention_days=30,
        smd_export_retention_days=7,
    )

    restarted_manager = MaintenanceManager()
    await restarted_manager.run_once(settings)

    assert restarted_manager.snapshot()["last_backup_path"] == str(existing)
    assert restarted_manager.snapshot()["last_backup_at"] is not None
    assert list(backup_dir.glob("smd-*.db")) == [existing]
