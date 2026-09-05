from pathlib import Path

import pytest
from smd_desktop.runtime import load_environment, require_fixed_runtime
from smd_desktop.single_instance import AlreadyRunning, single_instance


def test_single_instance_blocks_second_owner_and_releases(tmp_path):
    lock = tmp_path / "service.lock"
    with single_instance("SmdHmi.Test", lock):
        with pytest.raises(AlreadyRunning):
            with single_instance("SmdHmi.Test", lock):
                pass
    with single_instance("SmdHmi.Test", lock):
        pass


def test_config_is_absolute_and_loaded_without_interpolation(tmp_path, monkeypatch):
    config = tmp_path / "config/service.env"
    config.parent.mkdir()
    config.write_text(
        'SMD_JWT_SECRET="literal${DO_NOT_EXPAND}"\nSMD_DB_PATH="' + str(tmp_path / "custom/db.sqlite") + '"\n'
    )
    monkeypatch.setenv("SMD_JWT_SECRET", "stale")
    for key in ("SMD_DB_PATH", "SMD_FRONTEND_DIST", "SMD_MAINTENANCE_FILE"):
        monkeypatch.setenv(key, "")
    load_environment(tmp_path, tmp_path / "versions/0.3.0")
    import os

    assert os.environ["SMD_JWT_SECRET"] == "literal${DO_NOT_EXPAND}"
    assert Path(os.environ["SMD_MAINTENANCE_FILE"]) == tmp_path / "maintenance.json"


def test_runtime_never_falls_back_to_system_webview(tmp_path):
    with pytest.raises(RuntimeError, match="WebView2"):
        require_fixed_runtime(tmp_path)


def test_lan_listener_requires_tls_pair(monkeypatch):
    from smd_desktop.runtime import tls_options

    monkeypatch.delenv("SMD_TLS_CERTFILE", raising=False)
    monkeypatch.delenv("SMD_TLS_KEYFILE", raising=False)
    assert tls_options("127.0.0.1") == {}
    with pytest.raises(RuntimeError, match="TLS"):
        tls_options("0.0.0.0")
    monkeypatch.setenv("SMD_TLS_CERTFILE", "cert.pem")
    with pytest.raises(RuntimeError, match="TLS"):
        tls_options("0.0.0.0")


def test_service_captures_structlog_print_stream(caplog):
    import logging

    from smd_desktop.log_stream import LogStream

    stream = LogStream(logging.getLogger("test-service"))
    with caplog.at_level(logging.INFO):
        stream.write('{"event":"device.offline"}')
        stream.write("\n")
        stream.flush()
    assert '{"event":"device.offline"}' in caplog.text


def test_shell_self_check_loads_frozen_edge_renderer(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from smd_desktop import shell

    loaded = []
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace())
    monkeypatch.setattr(sys, "argv", ["SmdDesktop.exe", "--self-check"])
    monkeypatch.setattr(shell, "version_root", lambda: tmp_path)
    monkeypatch.setattr(shell, "require_fixed_runtime", lambda root: root / "webview2")
    monkeypatch.setattr("importlib.import_module", lambda name: loaded.append(name))
    shell.main()
    assert loaded == ["webview.platforms.edgechromium"]


def test_shell_self_check_failure_never_opens_modal_dialog(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from smd_desktop import shell

    dialogs = []

    def missing_renderer(name):
        raise ImportError("missing CLR renderer")

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace())
    monkeypatch.setattr(sys, "argv", ["SmdDesktop.exe", "--self-check"])
    monkeypatch.setattr(shell, "version_root", lambda: tmp_path)
    monkeypatch.setattr(shell, "require_fixed_runtime", lambda root: root)
    monkeypatch.setattr(shell.os, "name", "nt")
    monkeypatch.setattr(
        shell.ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(MessageBoxW=lambda *args: dialogs.append(args))),
        raising=False,
    )
    monkeypatch.setattr("importlib.import_module", missing_renderer)
    with pytest.raises(SystemExit, match="missing CLR"):
        shell.main()
    assert dialogs == []


def test_service_self_check_loads_http_application_without_starting_gateway(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from smd_desktop import service

    loaded = []

    class Config:
        def __init__(self, application, **kwargs):
            loaded.append(application)

        def load(self):
            loaded.append("loaded")

    monkeypatch.setattr(sys, "argv", ["SmdService.exe", "--self-check"])
    for name in ("servicemanager", "win32service", "win32serviceutil"):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=Config))
    monkeypatch.setattr(service, "data_root", lambda: tmp_path)
    monkeypatch.setattr(service, "version_root", lambda: tmp_path)
    monkeypatch.setattr(service, "load_environment", lambda *args: None)
    service.main()
    assert loaded == ["app.main:app", "loaded"]
