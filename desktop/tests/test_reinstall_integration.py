"""Real initialize orchestration over retained SQLite/config; only Windows effects are fakes."""

import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from smd_desktop import updater
from smd_desktop.bundle import REQUIRED, build_manifest, sha256
from smd_desktop.reinstall import PreservedReinstall
from test_preserved_reinstall import existing, value

from app.services.maintenance_service import MaintenanceBlockedError, MaintenanceManager


def config_files(data):
    return {
        path.relative_to(data / "config").as_posix(): path.read_bytes()
        for path in (data / "config").rglob("*")
        if path.is_file()
    }


@pytest.fixture
def reinstall(existing, monkeypatch):
    install, data, database, _ = existing
    package = install / ".staging" / ("f" * 32) / "payload"
    for relative in REQUIRED:
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture")
    build_manifest(package, version="0.3.0", commit="a" * 40, webview2_version="135.0.1.2")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE accounts (username TEXT, password_hash TEXT)")
        connection.execute("INSERT INTO accounts VALUES ('existing-admin', 'fixture-password-hash')")
    original_config = config_files(data)
    original_client = (data / "client.json").read_bytes()
    events = []
    service = SimpleNamespace(
        **{
            name: index
            for index, name in enumerate(
                (
                    "SC_MANAGER_ALL_ACCESS",
                    "SERVICE_ALL_ACCESS",
                    "SERVICE_WIN32_OWN_PROCESS",
                    "SERVICE_AUTO_START",
                    "SERVICE_DEMAND_START",
                    "SERVICE_NO_CHANGE",
                    "SERVICE_CHANGE_CONFIG",
                    "SERVICE_QUERY_CONFIG",
                    "SC_MANAGER_CONNECT",
                    "SERVICE_ERROR_NORMAL",
                )
            )
        },
        OpenSCManager=lambda *args: "manager",
        CreateService=lambda *args: events.append("register") or "service",
        OpenService=lambda *args: "service",
        ChangeServiceConfig=lambda *args: None,
        CloseServiceHandle=lambda *args: None,
    )
    monkeypatch.setitem(sys.modules, "win32service", service)
    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(error=OSError))
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(updater.windows_powershell, "run", lambda *args, **kwargs: events.append("acl"))

    def unexpected_secret(*args):
        raise AssertionError("retained installation must never generate a new bootstrap password or key")

    monkeypatch.setattr(updater.secrets, "token_urlsafe", unexpected_secret)
    monkeypatch.setattr(updater.secrets, "token_hex", unexpected_secret)

    class Platform:
        running = False
        fail = None
        migrations = 0

        def configure(self, target):
            events.append("configure")

        def stop(self):
            self.running = False
            events.append("stop")

        def migrate(self, target):
            assert not self.running
            env = dotenv_values(data / "config/service.env", interpolate=False, encoding="utf-8")
            assert Path(env["SMD_DB_PATH"]) == database
            assert env["SMD_JWT_SECRET"] == "unchanged-secret"
            assert config_files(data) == original_config
            self.migrations += 1
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE IF NOT EXISTS migrated(value TEXT)")
                connection.execute("UPDATE sample SET value='migrated'")
            events.append("migrate")
            if self.fail == "migration":
                raise RuntimeError("fixture migration failure")

        def start(self):
            self.running = True
            events.append("start")

        def healthy(self, version):
            assert self.running and version == "0.3.0"
            events.append("health")
            if self.fail == "health":
                raise RuntimeError("fixture health failure")

    return SimpleNamespace(
        install=install,
        data=data,
        database=database,
        package=package,
        platform=Platform(),
        events=events,
        service=service,
        original_config=original_config,
        original_client=original_client,
    )


def assert_preserved(case):
    assert config_files(case.data) == case.original_config
    assert (case.data / "client.json").read_bytes() == case.original_client
    with closing(sqlite3.connect(case.database)) as connection:
        assert connection.execute("SELECT username,password_hash FROM accounts").fetchall() == [
            ("existing-admin", "fixture-password-hash")
        ]


def test_initialize_reinstalls_owned_data_without_bootstrap_or_configuration_changes(reinstall):
    case = reinstall
    updater.initialize(case.package, case.install, case.data, case.platform)
    assert_preserved(case)
    assert value(case.database) == "migrated"
    assert case.platform.running
    assert json.loads((case.data / "installation.json").read_text())["version"] == "0.3.0"
    assert not (case.data / "uninstalled.json").exists()
    assert not (case.data / "updates/uninstall.json").exists()


@pytest.mark.parametrize("failure", ["migration", "health"])
def test_initialize_failure_restores_actual_database_config_and_keeps_service_stopped(reinstall, failure):
    case = reinstall
    case.platform.fail = failure
    with pytest.raises(RuntimeError, match=f"fixture {failure} failure"):
        updater.initialize(case.package, case.install, case.data, case.platform)
    assert_preserved(case)
    assert value(case.database) == "original"
    with closing(sqlite3.connect(case.database)) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='migrated'").fetchall()
    assert not case.platform.running
    assert (case.data / "uninstalled.json").exists()
    assert not (case.data / "installation.json").exists()
    assert case.events[-1] == "acl"  # Restored config must regain restricted Windows ACLs.


def test_initialize_recovers_missing_config_from_verified_reinstall_backup(reinstall, monkeypatch):
    case = reinstall
    helper = PreservedReinstall(
        case.install,
        case.data,
        case.platform,
        target_version="0.3.0",
        package_sha256=sha256(case.package / "manifest.json"),
    )
    helper.before_migrate()
    original_replace = Path.replace

    def power_during_config_replace(source, target):
        if Path(target) == case.data / "config":
            raise SystemExit("power interrupted after removing old config")
        return original_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", power_during_config_replace)
        with pytest.raises(SystemExit):
            helper.failed(RuntimeError("trigger recovery"))
    assert not (case.data / "config/service.env").exists()
    updater.initialize(case.package, case.install, case.data, case.platform)
    assert_preserved(case)
    assert value(case.database) == "migrated"
    assert case.platform.running


def test_initialize_retries_only_health_after_completed_migration(reinstall):
    case = reinstall
    helper = PreservedReinstall(
        case.install,
        case.data,
        case.platform,
        target_version="0.3.0",
        package_sha256=sha256(case.package / "manifest.json"),
    )
    helper.before_migrate()
    case.platform.migrate(case.package)
    helper.mark_migrated()
    with closing(sqlite3.connect(case.database)) as connection, connection:
        connection.execute("UPDATE sample SET value='record after migration'")
    updater.initialize(case.package, case.install, case.data, case.platform)
    assert case.platform.migrations == 1
    assert value(case.database) == "record after migration"
    assert_preserved(case)


def test_initialize_blocks_both_command_channels_until_healthy_install_is_committed(reinstall):
    case = reinstall
    original_health = case.platform.healthy

    def health(version):
        original_health(version)
        manager = MaintenanceManager()  # A fresh process reads the persisted gate.
        manager.configure_upgrade(case.data / "maintenance.json")

        async def attempts():
            for priority in (False, True):
                with pytest.raises(MaintenanceBlockedError):
                    async with manager.command_guard(priority=priority):
                        pytest.fail("uncommitted reinstall accepted a control request")

        asyncio.run(attempts())

    case.platform.healthy = health
    updater.initialize(case.package, case.install, case.data, case.platform)
    manager = MaintenanceManager()
    manager.configure_upgrade(case.data / "maintenance.json")

    async def allowed_after_commit():
        async with manager.command_guard():
            assert manager.upgrade_state()["state"] == "idle"

    asyncio.run(allowed_after_commit())


def test_new_service_cannot_auto_start_before_reinstall_gate_and_migration_are_durable(reinstall):
    case = reinstall
    changes = []

    def register(*args):
        assert args[5] == case.service.SERVICE_DEMAND_START
        return "service"

    def change_start(*args):
        mode = args[2]
        changes.append(mode)
        if mode == case.service.SERVICE_AUTO_START:
            assert json.loads((case.data / "updates/install.json").read_text())["phase"] == "migrated"
            assert json.loads((case.data / "updates/reinstall.json").read_text())["phase"] == "awaiting_health"
            assert json.loads((case.data / "maintenance.json").read_text())["state"] == "prepared"
            assert "configure" in case.events

    case.service.CreateService = register
    case.service.ChangeServiceConfig = change_start
    updater.initialize(case.package, case.install, case.data, case.platform)
    assert changes[-1] == case.service.SERVICE_AUTO_START


def test_retry_exact_registered_service_is_reset_to_demand_before_configuration(reinstall, monkeypatch):
    case = reinstall
    modes = []

    class WindowsError(Exception):
        winerror = 1073

    def already_exists(*args):
        raise WindowsError()

    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(error=WindowsError))
    case.service.CreateService = already_exists
    case.service.QueryServiceConfig = lambda handle: (
        None,
        None,
        None,
        f'"{case.install / "versions/0.3.0/SmdService/SmdService.exe"}"',
    )
    case.service.ChangeServiceConfig = lambda *args: modes.append(args[2])
    configure = case.platform.configure

    def check_before_configure(target):
        assert modes and modes[0] == case.service.SERVICE_DEMAND_START
        configure(target)

    case.platform.configure = check_before_configure
    updater.initialize(case.package, case.install, case.data, case.platform)
    assert modes == [case.service.SERVICE_DEMAND_START, case.service.SERVICE_AUTO_START]


def test_interruption_before_reinstall_gate_does_not_leave_automatic_service(reinstall):
    case = reinstall
    created_modes = []
    case.service.CreateService = lambda *args: created_modes.append(args[5]) or "service"

    def power_before_backup(target):
        raise SystemExit("power before durable reinstall gate")

    case.platform.configure = power_before_backup
    with pytest.raises(SystemExit):
        updater.initialize(case.package, case.install, case.data, case.platform)
    assert created_modes == [case.service.SERVICE_DEMAND_START]
    assert not case.platform.running


@pytest.mark.parametrize("failure", ["migration", "health"])
def test_failed_initialization_returns_service_to_demand_start(reinstall, failure):
    case = reinstall
    modes = []
    case.platform.fail = failure
    case.service.ChangeServiceConfig = lambda *args: modes.append(args[2])
    with pytest.raises(RuntimeError, match="fixture"):
        updater.initialize(case.package, case.install, case.data, case.platform)
    assert modes[-1] == case.service.SERVICE_DEMAND_START
    assert not case.platform.running
    assert json.loads((case.data / "maintenance.json").read_text())["state"] == "prepared"


def test_existing_foreign_service_start_mode_is_never_changed(reinstall, monkeypatch):
    case = reinstall
    changes = []

    class WindowsError(Exception):
        winerror = 1073

    def exists(*args):
        raise WindowsError()

    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(error=WindowsError))
    case.service.CreateService = exists
    case.service.QueryServiceConfig = lambda handle: (None, None, None, '"C:\\Other\\Service.exe"')
    case.service.ChangeServiceConfig = lambda *args: changes.append(args)
    with pytest.raises(RuntimeError, match="路径不同"):
        updater.initialize(case.package, case.install, case.data, case.platform)
    assert not changes
