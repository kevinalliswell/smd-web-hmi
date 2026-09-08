"""An unprepared upgrade must explain the next step without touching the installation."""

import json
import logging
import sqlite3
import sys
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from smd_desktop import updater
from smd_desktop.bundle import REQUIRED, build_manifest
from smd_desktop.storage import atomic_json

TARGET = "0.3.0-rc.3"


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def installed(tmp_path, monkeypatch):
    install, data, package = (tmp_path / name for name in ("安装 程序", "现场 数据", "离线 包"))
    old_program = install / "versions/0.3.0-rc.2/SmdService/SmdService.exe"
    old_program.parent.mkdir(parents=True)
    old_program.write_bytes(b"existing installed service")
    (data / "config").mkdir(parents=True)
    database = data / "historical.sqlite"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE samples(value TEXT)")
        connection.execute("INSERT INTO samples VALUES (?)", ("既有试验记录",))
    (data / "config/service.env").write_text(f'SMD_DB_PATH="{database}"\n', encoding="utf-8")
    atomic_json(data / "installation.json", {"version": "0.3.0-rc.2"})
    for relative in REQUIRED:
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test bundle content")
    build_manifest(package, version=TARGET, commit="a" * 40, webview2_version="135.0.1.2")
    platform = Mock(spec=updater.WindowsPlatform)
    monkeypatch.setattr(updater, "WindowsPlatform", lambda root: platform)
    monkeypatch.setattr(updater, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        updater, "ctypes", SimpleNamespace(windll=SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: True)))
    )
    monkeypatch.setattr(updater, "single_instance", lambda *args: nullcontext())
    monkeypatch.setattr(updater, "data_root", lambda: data)
    monkeypatch.setattr(sys, "argv", ["SmdUpdate.exe", "--install", str(install), "--package", str(package)])
    return SimpleNamespace(install=install, data=data, package=package, platform=platform)


def wrong_target(data):
    atomic_json(
        data / "maintenance.json",
        {"state": "prepared", "target_version": "0.3.0-rc.1", "token": "private-ticket-value"},
    )


@pytest.mark.parametrize("gate", ["missing", "wrong_target"])
def test_unprepared_upgrade_identifies_target_and_preserves_existing_installation(installed, gate):
    if gate == "wrong_target":
        wrong_target(installed.data)
    before_data, before_install = files(installed.data), files(installed.install)
    with pytest.raises(RuntimeError, match=TARGET) as failure:
        updater.run()
    assert type(failure.value).__name__ == "MaintenanceRequired"
    assert "旧版→系统设置→离线升级" in str(failure.value)
    assert files(installed.data) == before_data
    assert files(installed.install) == before_install
    assert installed.platform.mock_calls == []  # No claim, SCM change, migration or health recovery.


@pytest.fixture
def updater_logger(monkeypatch):
    # Exercise the real rotating UTF-8 handler without replacing pytest's root handlers.
    logger = logging.Logger("isolated-updater", level=logging.INFO)

    def configure(*, handlers, level, force):
        assert force is True
        logger.setLevel(level)
        for handler in handlers:
            logger.addHandler(handler)

    monkeypatch.setattr(
        updater,
        "logging",
        SimpleNamespace(
            INFO=logging.INFO, Formatter=logging.Formatter, basicConfig=configure, exception=logger.exception
        ),
    )
    yield logger
    for handler in logger.handlers:
        handler.close()


@pytest.mark.parametrize("gate", ["missing", "wrong_target"])
def test_main_records_utf8_guidance_and_returns_exit_20_without_upgrading(installed, updater_logger, gate):
    if gate == "wrong_target":
        wrong_target(installed.data)
    before_data, before_install = files(installed.data), files(installed.install)
    with pytest.raises(SystemExit) as failure:
        updater.main()
    assert failure.value.code == 20
    log = (installed.data / "logs/updater.log").read_text(encoding="utf-8")
    assert datetime.fromisoformat(log.split(" ", 1)[0]).tzinfo is timezone.utc
    assert TARGET in log
    assert "旧版→系统设置→离线升级" in log
    assert "private-ticket-value" not in log
    after_data = {name: content for name, content in files(installed.data).items() if not name.startswith("logs/")}
    assert after_data == before_data
    assert files(installed.install) == before_install
    assert installed.platform.mock_calls == []


def test_corrupt_ticket_keeps_original_error_and_never_becomes_unprepared_exit(installed, updater_logger):
    (installed.data / "maintenance.json").write_text("{broken", encoding="utf-8")
    before_data, before_install = files(installed.data), files(installed.install)
    with pytest.raises(json.JSONDecodeError):
        updater.main()
    log = (installed.data / "logs/updater.log").read_text(encoding="utf-8")
    assert "Installation or recovery failed" in log
    assert "JSONDecodeError" in log
    after_data = {name: content for name, content in files(installed.data).items() if not name.startswith("logs/")}
    assert after_data == before_data
    assert files(installed.install) == before_install
    assert installed.platform.mock_calls == []
