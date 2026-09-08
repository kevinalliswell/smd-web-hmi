import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from smd_bench import windows


def test_new_job_uses_unique_unicode_name_and_rejects_existing_object(monkeypatch):
    calls = []
    error = {"value": 0}

    def create(attributes, name):
        assert attributes is None
        assert isinstance(name, str) and name.startswith("Local\\SmdBench-")
        calls.append(name)
        error["value"] = 183
        return 7

    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(
            SetLastError=lambda value: error.update(value=value),
            GetLastError=lambda: error["value"],
            CloseHandle=lambda handle: calls.append(handle),
        ),
    )
    monkeypatch.setitem(sys.modules, "win32job", SimpleNamespace(CreateJobObject=create))
    with pytest.raises(RuntimeError, match="already exists"):
        windows.create_kill_on_close_job()
    assert calls[-1] == 7  # Never configure or attach a process to the preexisting object.


def test_parent_job_is_not_closed_by_python_module_cleanup(monkeypatch):
    import gc

    events = []

    class JobHandle:
        detached = False

        def Detach(self):
            self.detached = True
            return 23

        def __del__(self):
            if not self.detached:
                events.append("kernel_close")

    monkeypatch.setattr(windows, "_job_handle", None)
    monkeypatch.setattr(windows, "create_kill_on_close_job", JobHandle)
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(GetCurrentProcess=lambda: 1))
    monkeypatch.setitem(sys.modules, "win32job", SimpleNamespace(AssignProcessToJobObject=lambda *_: None))
    windows.contain_child_processes()
    windows._job_handle = None  # Interpreter module cleanup must not kill its own still-exiting process.
    gc.collect()
    assert not events


@pytest.mark.skipif(os.name != "nt", reason="requires real locked pywin32 and Windows kernel job objects")
def test_real_job_can_be_created_configured_and_destroyed(monkeypatch):
    from uuid import uuid4

    import pywintypes
    import win32api
    import win32job

    identity = uuid4()
    monkeypatch.setattr(windows, "uuid4", lambda: identity)
    name = "Local\\SmdBench-" + identity.hex
    job = windows.create_kill_on_close_job()
    try:
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        assert info["BasicLimitInformation"]["LimitFlags"] & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    finally:
        win32api.CloseHandle(job)
    # CloseHandle invalidates PyHANDLE to NULL. QueryInformationJobObject(NULL)
    # queries the caller's current job, so reopen this exact unique name instead.
    with pytest.raises(pywintypes.error) as caught:
        win32job.OpenJobObject(win32job.JOB_OBJECT_QUERY, False, name)
    assert caught.value.winerror == 2  # ERROR_FILE_NOT_FOUND: the unassigned job was destroyed.


@pytest.mark.skipif(os.name != "nt", reason="requires real locked pywin32 and Windows process creation")
def test_real_checked_child_returns_exit_code_and_enforces_timeout(tmp_path):
    from smd_bench.installation import checked_process

    assert checked_process(Path(sys.executable), ["-c", "import sys;sys.exit(23)"], tmp_path, timeout=10) == 23
    with pytest.raises(TimeoutError, match="deadline"):
        checked_process(Path(sys.executable), ["-c", "import time;time.sleep(120)"], tmp_path, timeout=1)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows parent-death job cleanup")
@pytest.mark.parametrize("normal_exit", [False, True])
def test_real_parent_job_terminates_its_child_on_owner_exit(tmp_path, normal_exit):
    import win32api
    import win32con
    import win32event

    pid_file = tmp_path / "controlled-child.pid"
    script = (
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "from smd_bench.windows import contain_child_processes\n"
        "contain_child_processes()\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid),encoding='ascii')\n"
        "sys.stdin.buffer.read(1)\n"
    )
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(str(path) for path in sys.path if path)}
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(pid_file)],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_handle = None
    try:
        deadline = time.monotonic() + 15
        pid_text = ""
        while parent.poll() is None and time.monotonic() < deadline:
            pid_text = pid_file.read_text(encoding="ascii") if pid_file.exists() else ""
            if pid_text.isdigit():
                break
            time.sleep(0.05)
        assert pid_text.isdigit(), "controlled job owner failed before spawning its child"
        child_handle = win32api.OpenProcess(
            win32con.SYNCHRONIZE | win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_TERMINATE,
            False,
            int(pid_text),
        )
        assert win32event.WaitForSingleObject(child_handle, 0) == win32event.WAIT_TIMEOUT
        if normal_exit:
            parent.stdin.write(b"x")
            parent.stdin.flush()
        else:
            parent.terminate()
        parent.wait(timeout=10)
        if normal_exit:
            assert parent.returncode == 0
        assert win32event.WaitForSingleObject(child_handle, 10000) == win32event.WAIT_OBJECT_0
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        if child_handle:
            if win32event.WaitForSingleObject(child_handle, 0) != win32event.WAIT_OBJECT_0:
                win32api.TerminateProcess(child_handle, 1)
                win32event.WaitForSingleObject(child_handle, 10000)
            win32api.CloseHandle(child_handle)
        parent.communicate(timeout=5)


def test_windows_self_check_contains_process_before_opening_browser(tmp_path, monkeypatch):
    from smd_bench import cli

    events = []
    monkeypatch.setattr(cli, "tool_manifest", lambda: {})
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt"))

    def refused():
        events.append("containment")
        raise RuntimeError("injected containment refusal")

    monkeypatch.setattr(windows, "contain_child_processes", refused)
    monkeypatch.setattr("smd_bench.browser.Browser", lambda *_: pytest.fail("browser opened before containment"))
    assert cli.main(["self-check"]) == 1
    assert events == ["containment"]
