"""A stopped SCM state must also have no surviving service process before fallback."""

import sys
from types import SimpleNamespace

import pytest
from smd_desktop.windows_platform import WindowsPlatform


@pytest.mark.parametrize(
    "state,pid,wait,expected", [(4, 12, 0, False), (1, 0, 0, True), (1, 12, 258, False), (1, 12, 0, True)]
)
def test_repair_fallback_observes_scm_and_actual_process_exit(tmp_path, monkeypatch, state, pid, wait, expected):
    closed = []
    queried = []
    process = SimpleNamespace(Close=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=lambda *args: process))
    monkeypatch.setitem(
        sys.modules, "win32event", SimpleNamespace(WaitForSingleObject=lambda *args: wait, WAIT_OBJECT_0=0)
    )
    monkeypatch.setitem(
        sys.modules,
        "win32service",
        SimpleNamespace(QueryServiceStatusEx=object(), SERVICE_QUERY_STATUS=4, SERVICE_STOPPED=1),
    )
    platform = WindowsPlatform(tmp_path)

    def query(*args):
        queried.append(args)
        return {"CurrentState": state, "ProcessId": pid}

    monkeypatch.setattr(platform, "_service", query)
    assert platform.service_stopped() is expected
    assert len(queried) == 1
    assert closed == ([True] if state == 1 and pid else [])


def test_inaccessible_stopped_process_never_counts_as_terminated(tmp_path, monkeypatch):
    def denied(*args):
        error = OSError("access denied")
        error.winerror = 5
        raise error

    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=denied))
    monkeypatch.setitem(
        sys.modules, "win32event", SimpleNamespace(WaitForSingleObject=lambda *args: 0, WAIT_OBJECT_0=0)
    )
    monkeypatch.setitem(
        sys.modules,
        "win32service",
        SimpleNamespace(QueryServiceStatusEx=object(), SERVICE_QUERY_STATUS=4, SERVICE_STOPPED=1),
    )
    platform = WindowsPlatform(tmp_path)
    monkeypatch.setattr(platform, "_service", lambda *args: {"CurrentState": 1, "ProcessId": 123})
    with pytest.raises(OSError, match="access denied"):
        platform.service_stopped()
