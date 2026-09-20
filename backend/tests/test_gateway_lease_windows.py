"""CI上以真实LocalService任务持锁，验证交互账户访问同一Global对象。"""

import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.services.gateway_lease import GatewayLease, GatewayLeaseError


def _windows_task_scheduler_admin() -> bool:
    """注册 LocalService 计划任务需要提升的管理员令牌;非管理员自托管 runner 实探为否。"""
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        return False
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


pytestmark = pytest.mark.skipif(
    not _windows_task_scheduler_admin(),
    reason="requires isolated Windows GitHub runner with Task Scheduler administrator rights",
)


def test_local_service_and_interactive_process_share_gateway_mutex():
    identity = uuid.uuid4().hex
    folder = Path(os.environ["PROGRAMDATA"]) / ("SmdHmi-gateway-ci-" + identity)
    task_name = "SmdHmi-Gateway-CI-" + identity
    folder.mkdir()
    ready, release = folder / "ready.txt", folder / "release.txt"
    port = 1024 + int(identity[:4], 16) % 64000
    source = Path(__file__).resolve().parents[1] / "app/services/gateway_lease.py"
    shutil.copyfile(source, folder / "gateway_lease_under_test.py")
    helper = folder / "holder.py"
    helper.write_text(
        "import time\nfrom pathlib import Path\nfrom gateway_lease_under_test import GatewayLease\n"
        + f"ready, release = Path({str(ready)!r}), Path({str(release)!r})\n"
        + "try:\n"
        + f"    with GatewayLease('192.0.2.40', {port}):\n"
        + "        ready.write_text('acquired')\n        deadline = time.monotonic() + 30\n"
        + "        while not release.exists() and time.monotonic() < deadline:\n            time.sleep(0.1)\n"
        + "except BaseException as error:\n    ready.write_text(type(error).__name__ + ':' + str(error))\n",
        encoding="utf-8",
    )
    register = folder / "register.ps1"
    register.write_text(
        "param([string]$TaskName,[string]$Python,[string]$Script)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$Action = New-ScheduledTaskAction -Execute $Python -Argument ('\"' + $Script + '\"')\n"
        "$Principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount\n"
        "$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1)\n"
        "Register-ScheduledTask -TaskName $TaskName -Action $Action -Principal $Principal -Settings $Settings -Force | Out-Null\n"
        "Start-ScheduledTask -TaskName $TaskName\n",
        encoding="utf-8",
    )
    cleanup = folder / "cleanup.ps1"
    cleanup.write_text(
        "param([string]$TaskName)\nStop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue\n"
        "Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue\n",
        encoding="utf-8",
    )
    try:
        subprocess.run(
            ["icacls.exe", str(folder), "/grant:r", "*S-1-5-19:(OI)(CI)M", "/T"], check=True, capture_output=True
        )
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(register),
                "-TaskName",
                task_name,
                "-Python",
                sys.executable,
                "-Script",
                str(helper),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ready.exists(), "LocalService scheduled task did not run the lease helper"
        assert ready.read_text() == "acquired"
        with pytest.raises(GatewayLeaseError, match="占用"):
            GatewayLease("::ffff:192.0.2.40", port).acquire()
    finally:
        release.write_text("release")
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(cleanup),
                "-TaskName",
                task_name,
            ],
            check=False,
            capture_output=True,
            timeout=20,
        )
        shutil.rmtree(folder, ignore_errors=True)
