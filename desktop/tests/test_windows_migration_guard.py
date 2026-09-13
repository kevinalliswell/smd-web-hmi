"""SCM PID lifetime and inherited named-object identity are separate proofs."""

import ctypes
import sys
from types import SimpleNamespace

import pytest
from smd_desktop import single_instance as locks
from smd_desktop.windows_platform import WindowsPlatform


@pytest.fixture
def scm(monkeypatch):
    service = SimpleNamespace(
        SC_MANAGER_CONNECT=1,
        SERVICE_QUERY_STATUS=4,
        SERVICE_STOPPED=1,
        SERVICE_START_PENDING=2,
        SERVICE_STOP_PENDING=3,
        SERVICE_RUNNING=4,
        SERVICE_CONTINUE_PENDING=5,
        SERVICE_PAUSE_PENDING=6,
        SERVICE_PAUSED=7,
        QueryServiceStatusEx=object(),
    )
    monkeypatch.setitem(sys.modules, "win32service", service)
    return service


@pytest.mark.parametrize("state", [1, 2, 3])
def test_only_documented_valid_states_allow_opening_service_pid(tmp_path, monkeypatch, scm, state):
    def must_not_open(*args):
        pytest.fail("Opening a stale SCM PID can select an unrelated process")

    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=must_not_open))
    platform = WindowsPlatform(tmp_path)
    monkeypatch.setattr(platform, "_service", lambda *args: {"CurrentState": state, "ProcessId": 12345})
    assert platform._open_service_process() is None


@pytest.mark.parametrize("state", [4, 5, 6, 7])
def test_valid_service_state_captures_synchronize_handle(tmp_path, monkeypatch, scm, state):
    opened = []
    handle = object()

    def open_process(access, inherit, pid):
        opened.append((access, inherit, pid))
        return handle

    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=open_process))
    platform = WindowsPlatform(tmp_path)
    monkeypatch.setattr(platform, "_service", lambda *args: {"CurrentState": state, "ProcessId": 12345})
    assert platform._open_service_process() is handle
    assert opened == [(0x00100000, False, 12345)]


class NativeFunction:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


@pytest.fixture
def inherited(monkeypatch, scm):
    state = {"CurrentState": scm.SERVICE_STOPPED, "ProcessId": 12345}
    closed = []
    scm.OpenSCManager = lambda *args: "manager"
    scm.OpenService = lambda *args: "service"
    scm.QueryServiceStatusEx = lambda *args: state
    scm.CloseServiceHandle = lambda handle: closed.append(handle)
    kernel = SimpleNamespace(
        OpenMutexW=NativeFunction(lambda *args: 98),
        CloseHandle=NativeFunction(lambda handle: closed.append(handle)),
        CompareObjectHandles=NativeFunction(lambda inherited, expected: inherited == 42 and expected == 98),
    )
    monkeypatch.setattr(locks, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: True)),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    return state, closed


def test_inherited_migration_accepts_stopped_scm_with_stale_pid(inherited):
    state, closed = inherited
    with locks.inherited_migration_guard(42):
        assert closed == ["service", "manager"]
    assert closed == ["service", "manager", 98]


def test_inherited_migration_still_rejects_live_service(inherited):
    state, _ = inherited
    state["CurrentState"] = 4
    with pytest.raises(RuntimeError, match="完全停止"):
        with locks.inherited_migration_guard(42):
            pytest.fail("running service cannot permit migration")


def test_inherited_migration_still_rejects_wrong_named_object(inherited):
    with pytest.raises(RuntimeError, match="不属于"):
        with locks.inherited_migration_guard(99):
            pytest.fail("unrelated inherited handle cannot permit migration")
