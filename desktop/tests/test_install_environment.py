"""首次安装真实写入的dotenv配置必须能访问原始中文数据目录。"""

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from smd_desktop import updater
from smd_desktop.bundle import REQUIRED, build_manifest
from smd_desktop.runtime import data_root, load_environment


@pytest.mark.parametrize("directory", ["ascii data", "试验 数据 #1 ${LITERAL}"])
def test_initialized_environment_round_trips_custom_paths_and_opens_real_files(tmp_path, monkeypatch, directory):
    install = tmp_path / "安装 程序"
    data = tmp_path / directory
    package = tmp_path / "package"
    for relative in REQUIRED:
        source = package / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"fixture")
    build_manifest(package, version="0.3.0-rc.2", commit="a" * 40, webview2_version="135.0.1.2")
    # Only operating-system service/ACL effects are replaced. Configuration,
    # dotenv parsing, password file access and SQLite opening remain real.
    modes = []
    ws = SimpleNamespace(
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
                    "SC_MANAGER_CONNECT",
                    "SERVICE_ERROR_NORMAL",
                )
            )
        },
        OpenSCManager=lambda *args: "manager",
        CreateService=lambda *args: "service",
        OpenService=lambda *args: "service",
        ChangeServiceConfig=lambda *args: modes.append(args[2]),
        CloseServiceHandle=lambda handle: None,
    )
    monkeypatch.setitem(sys.modules, "win32service", ws)
    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(error=OSError))
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(updater.windows_powershell, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("SMD_DATA_ROOT", str(data))
    monkeypatch.setenv("LITERAL", "must-not-expand")
    for key in (
        "SMD_HOST",
        "SMD_PORT",
        "SMD_DB_PATH",
        "SMD_JWT_SECRET",
        "HOSTCOMM_MOCK",
        "HOSTCOMM_HOST",
        "HOSTCOMM_PORT",
        "PROTOCOL_VERSION",
        "HOSTCOMM_DEVICE_ID",
        "HOSTCOMM_CONTROLLER_ID",
        "HOSTCOMM_CONTROLLER_EPOCH",
        "HOSTCOMM_PSK_FILE",
        "SMD_BOOTSTRAP_ADMIN_PASSWORD_FILE",
        "SMD_FRONTEND_DIST",
        "SMD_MAINTENANCE_FILE",
    ):
        monkeypatch.setenv(key, "")

    def migrate(version):
        load_environment(data_root(), version)
        db = Path(os.environ["SMD_DB_PATH"])
        password = Path(os.environ["SMD_BOOTSTRAP_ADMIN_PASSWORD_FILE"])
        assert db == data / "db/smd.db"
        assert password == data / "config/bootstrap-admin-password.txt"
        assert password.read_text(encoding="utf-8").strip()
        assert Path(os.environ["SMD_FRONTEND_DIST"]) == version / "frontend"
        assert os.environ["PROTOCOL_VERSION"] == "2.0"
        assert os.environ["HOSTCOMM_MOCK"] == "false"
        assert all(
            os.environ[key] == ""
            for key in (
                "HOSTCOMM_DEVICE_ID",
                "HOSTCOMM_CONTROLLER_ID",
                "HOSTCOMM_CONTROLLER_EPOCH",
                "HOSTCOMM_PSK_FILE",
            )
        )
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("CREATE TABLE roundtrip(value TEXT)")
        assert db.is_file()

    platform = SimpleNamespace(
        configure=lambda version: None,
        stop=lambda: None,
        migrate=migrate,
        start=lambda: None,
        healthy=lambda version: None,
    )
    updater.initialize(package, install, data_root(), platform)
    assert (data / "installation.json").is_file()
    assert modes[-1] == ws.SERVICE_AUTO_START
