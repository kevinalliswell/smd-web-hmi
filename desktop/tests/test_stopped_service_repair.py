"""STOPPED eligibility does not interpret the undefined SCM ProcessId field."""

import sys
from types import SimpleNamespace

import pytest
from smd_desktop.windows_platform import WindowsPlatform


@pytest.mark.parametrize("state,pid,expected", [(4, 12, False), (1, 0, True), (1, 12, True)])
def test_repair_fallback_uses_scm_state_without_opening_undefined_pid(tmp_path, monkeypatch, state, pid, expected):
    def must_not_open(*args):
        pytest.fail("ProcessId is undefined when SERVICE_STOPPED")

    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=must_not_open))
    monkeypatch.setitem(sys.modules, "win32event", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "win32service",
        SimpleNamespace(QueryServiceStatusEx=object(), SERVICE_QUERY_STATUS=4, SERVICE_STOPPED=1),
    )
    platform = WindowsPlatform(tmp_path)
    monkeypatch.setattr(platform, "_service", lambda *args: {"CurrentState": state, "ProcessId": pid})
    assert platform.service_stopped() is expected


def test_inaccessible_scm_never_counts_as_stopped(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "win32event", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "win32service",
        SimpleNamespace(QueryServiceStatusEx=object(), SERVICE_QUERY_STATUS=4, SERVICE_STOPPED=1),
    )
    platform = WindowsPlatform(tmp_path)

    def denied(*args):
        raise OSError("access denied")

    monkeypatch.setattr(platform, "_service", denied)
    with pytest.raises(OSError, match="access denied"):
        platform.service_stopped()
