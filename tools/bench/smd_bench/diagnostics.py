"""Public failures contain code locations only; full diagnostics remain private."""

import os
import re
import shutil
import traceback
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from .ownership import assert_owned_path, check_claim

# Only shipped code/module names are public. Dynamic or unfamiliar filenames
# collapse to external.py; no exception text, source line, locals or parent path.
PUBLIC_FILES = frozenset(
    {
        "cli.py",
        "installation.py",
        "maintenance.py",
        "windows.py",
        "browser.py",
        "scenarios.py",
        "faults.py",
        "worker.py",
        "driver.py",
        "package.py",
        "ownership.py",
        "reporting.py",
        "contracts.py",
        "diagnostics.py",
        "entry.py",
        "storage.py",
        "windows_platform.py",
        "windows_powershell.py",
        "events.py",
        "tasks.py",
        "base_events.py",
        "runners.py",
        "subprocess.py",
        "sslproto.py",
        "streams.py",
        "threads.py",
        "thread.py",
        "futures.py",
        "pathlib.py",
        "shutil.py",
        "_generated.py",
        "_connection.py",
        "_errors.py",
        "_impl_to_api_mapping.py",
        "_frame.py",
        "_page.py",
        "_assertions.py",
        "_locator.py",
        "_sync_base.py",
        "_async_base.py",
        "_client.py",
        "_models.py",
        "default.py",
        "connection_pool.py",
        "connection.py",
        "__init__.py",
        "main.py",
        "base.py",
        "local.py",
        "v2_security.py",
    }
)
LOG_NAMES = (
    "service.log",
    "updater.log",
    *(f"service.log.{i}" for i in range(1, 11)),
    *(f"updater.log.{i}" for i in range(1, 6)),
)
MAX_LOG_BYTES = 256 * 1024 * 1024
SHUTDOWN_PHASES = {
    "INFO:uvicorn.error:Shutting down": "requested",
    "INFO:uvicorn.error:Waiting for connections to close. (CTRL+C to force quit)": "connections",
    "INFO:uvicorn.error:Waiting for background tasks to complete. (CTRL+C to force quit)": "background_tasks",
    "INFO:uvicorn.error:Waiting for application shutdown.": "application",
    "INFO:uvicorn.error:Application shutdown complete.": "application_complete",
}


def service_shutdown_evidence(data: Path, run_id: str) -> dict:
    """Read-only failure context, never proof of current SCM state or successful cleanup."""
    try:
        check_claim(data, run_id)
        path = data / "logs/service.log"
        assert_owned_path(path, path)
        with path.open("rb") as source:
            offset = max(0, source.seek(0, os.SEEK_END) - 512 * 1024)
            source.seek(offset)
            raw = source.read(512 * 1024)
        if offset:
            raw = raw.partition(b"\n")[2]  # Never interpret a partial first line.
        phases = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if re.fullmatch(r"INFO:uvicorn\.error:Started server process \[[0-9]{1,20}\]", line):
                phases.clear()
            phase = SHUTDOWN_PHASES.get(line)
            if re.fullmatch(
                r"ERROR:uvicorn\.error:Cancel [0-9]{1,20} running task\(s\), timeout graceful shutdown exceeded",
                line,
            ):
                phase = "requests_cancelled"
            if phase:
                if phase == "requested":
                    phases.clear()
                phases.append(phase)
        return {"collection": "ok", "source": "service_log_tail", "stages": phases[-16:]}
    except (OSError, ValueError, TypeError):
        return {"collection": "unavailable"}


def public_frames(error: BaseException) -> list[dict]:
    frames = []
    for frame, line in traceback.walk_tb(error.__traceback__):
        raw = frame.f_code.co_filename
        name = PureWindowsPath(raw).name if "\\" in raw else Path(raw).name
        known = name in PUBLIC_FILES
        function = frame.f_code.co_name.strip("<>")
        frames.append(
            {
                "file": name if known else "external.py",
                "function": (
                    function if known and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", function) else "external"
                ),
                "line": int(line),
            }
        )
    return frames[-32:]


def secure_private(private: Path, run_id: str) -> None:
    check_claim(private, run_id)
    if os.name == "nt":
        from .windows import secure_directory

        secure_directory(private)
    elif private.stat().st_mode & 0o077:
        raise ValueError("private diagnostics directory is not restricted")


def failure_details(error: BaseException, *, private: Path | None = None, run_id: str | None = None) -> dict:
    from .windows import WindowsOperationError

    result = {"type": type(error).__name__, "frames": public_frames(error), "private_trace_saved": False}
    if isinstance(error, WindowsOperationError):
        result["windows_operation"] = error.diagnostic
    if isinstance(error, OSError):
        result["os_error"] = {
            name: value for name in ("errno", "winerror") if type(value := getattr(error, name, None)) is int
        }
    if private is not None and run_id is not None:
        try:
            secure_private(private, run_id)
            target = private / f"failure-{uuid4().hex}.traceback.txt"
            with target.open("x", encoding="utf-8") as output:
                output.writelines(
                    traceback.TracebackException.from_exception(error, capture_locals=False).format(chain=True)
                )
            result["private_trace_saved"] = True
        except Exception as diagnostic_error:
            # This cannot change failed acceptance to passed or replace the original failure.
            result["private_trace_failure_type"] = type(diagnostic_error).__name__
            if isinstance(diagnostic_error, WindowsOperationError):
                result["private_trace_windows_operation"] = diagnostic_error.diagnostic
    return result


def archive_logs(private: Path, data: Path, run_id: str) -> Path | None:
    """Run only after the owned service has stopped, before deleting its data."""
    secure_private(private, run_id)
    check_claim(data, run_id)
    source = data / "logs"
    assert_owned_path(source, source, recursive=True)
    files = [source / name for name in LOG_NAMES if (source / name).is_file()]
    if not files:
        return None
    if sum(path.stat().st_size for path in files) > MAX_LOG_BYTES:
        raise ValueError("owned log archive exceeds its bounded copy budget; retain original data")
    destination = private / f"installation-logs-{uuid4().hex}"
    destination.mkdir(mode=0o700)
    for path in files:
        # copyfile inherits the destination's restricted ACL; never copy source ACLs.
        shutil.copyfile(path, destination / path.name)
    return destination
