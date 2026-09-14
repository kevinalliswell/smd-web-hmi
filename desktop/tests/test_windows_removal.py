"""SCM/任务调度器故障用明确错误码建模，不能把访问拒绝视为对象不存在。"""

import sys
from types import ModuleType, SimpleNamespace

import pytest
from smd_desktop.windows_platform import WindowsPlatform


class WinError(Exception):
    def __init__(self, code):
        self.winerror = code


class ComError(Exception):
    def __init__(self, code, inner=None):
        self.hresult = code
        self.excepinfo = (None, None, None, None, None, inner) if inner else None


@pytest.fixture
def windows(monkeypatch):
    ws = ModuleType("win32service")
    ws.SERVICE_STOP, ws.SERVICE_QUERY_STATUS = 1, 2
    ws.SERVICE_CONTROL_STOP, ws.SERVICE_STOPPED, ws.SERVICE_STOP_PENDING = 3, 4, 5
    ws.QueryServiceStatus, ws.ControlService, ws.DeleteService = object(), object(), object()
    monkeypatch.setitem(sys.modules, "win32service", ws)
    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(error=WinError, com_error=ComError))
    return ws


@pytest.mark.parametrize("error", [1060, 5])
def test_remove_service_only_accepts_missing_service(tmp_path, monkeypatch, windows, error):
    platform = WindowsPlatform(tmp_path)

    def stop():
        raise WinError(error)

    monkeypatch.setattr(platform, "stop", stop)
    if error == 1060:
        platform.remove_service()
    else:
        with pytest.raises(WinError):
            platform.remove_service()


def test_remove_service_waits_until_marked_service_disappears(tmp_path, monkeypatch, windows):
    platform = WindowsPlatform(tmp_path)
    monkeypatch.setattr(platform, "stop", lambda: None)
    states = iter([1072, 1060])
    deletes = []

    def service(operation, access):
        if operation is windows.DeleteService:
            deletes.append(True)
            return
        raise WinError(next(states))

    monkeypatch.setattr(platform, "_service", service)
    monkeypatch.setattr("smd_desktop.windows_platform.time.sleep", lambda _: None)
    platform.remove_service()
    assert deletes == [True]
    assert list(states) == []


def test_pending_deletion_timeout_is_not_success(tmp_path, monkeypatch, windows):
    platform = WindowsPlatform(tmp_path, timeout=0)
    monkeypatch.setattr(platform, "stop", lambda: None)
    monkeypatch.setattr(platform, "_service", lambda *args: None)
    with pytest.raises(RuntimeError, match="持有句柄"):
        platform.remove_service()


def test_stop_pending_can_be_waited_during_retry(tmp_path, monkeypatch, windows):
    platform = WindowsPlatform(tmp_path)

    def service(operation, access):
        if operation is windows.QueryServiceStatus:
            return (0, windows.SERVICE_STOP_PENDING)
        raise WinError(1061)

    waited = []
    monkeypatch.setattr(platform, "_open_service_process", lambda: None)
    monkeypatch.setattr(platform, "_hold_backend", lambda: None)
    monkeypatch.setattr(platform, "_service", service)
    monkeypatch.setattr(platform, "_wait", lambda state: waited.append(state))
    platform.stop()
    assert waited == [windows.SERVICE_STOPPED]


def test_stopped_scm_with_live_process_never_grants_database_access(tmp_path, monkeypatch, windows):
    platform = WindowsPlatform(tmp_path, timeout=0)
    closed, acquired = [], []
    monkeypatch.setattr(platform, "_open_service_process", lambda: SimpleNamespace(Close=lambda: closed.append(True)))
    monkeypatch.setattr(platform, "_service", lambda *args: None)
    monkeypatch.setattr(platform, "_wait", lambda state: None)
    monkeypatch.setattr(platform, "_hold_backend", lambda: acquired.append(True))
    monkeypatch.setitem(
        sys.modules, "win32event", SimpleNamespace(WaitForSingleObject=lambda *args: 258, WAIT_OBJECT_0=0)
    )
    with pytest.raises(RuntimeError, match="进程未退出"):
        platform.stop()
    assert closed == [True]
    assert acquired == []


@pytest.mark.parametrize(
    "code,inner,missing", [(0x80070002, None, True), (0x80020009, 0x80070002, True), (0x80070005, None, False)]
)
def test_task_removal_only_ignores_file_not_found(tmp_path, monkeypatch, windows, code, inner, missing):
    def delete(name, flags):
        assert name == "SmdHmi-Recover" and flags == 0
        raise ComError(code, inner)

    client = ModuleType("win32com.client")
    client.Dispatch = lambda _: SimpleNamespace(
        Connect=lambda: None, GetFolder=lambda _: SimpleNamespace(DeleteTask=delete)
    )
    com = ModuleType("win32com")
    com.client = client
    monkeypatch.setitem(sys.modules, "win32com", com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    platform = WindowsPlatform(tmp_path)
    if missing:
        platform.remove_recovery_task()
    else:
        with pytest.raises(ComError):
            platform.remove_recovery_task()
