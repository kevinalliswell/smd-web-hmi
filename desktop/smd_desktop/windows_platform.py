"""Windows SCM 适配：状态查询不依赖本地化 sc.exe 输出。"""

import json
import subprocess
import time
import urllib.request
from pathlib import Path


class WindowsPlatform:
    SERVICE = "SmdHmi"

    def __init__(self, data: Path, *, timeout: float = 60):
        self.data = data
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _service(self, operation, access):
        import win32service as ws

        manager = ws.OpenSCManager(None, None, ws.SC_MANAGER_CONNECT)
        try:
            service = ws.OpenService(manager, self.SERVICE, access)
            try:
                return operation(service)
            finally:
                ws.CloseServiceHandle(service)
        finally:
            ws.CloseServiceHandle(manager)

    def _wait(self, state):
        import win32service as ws

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            actual = self._service(ws.QueryServiceStatus, ws.SERVICE_QUERY_STATUS)[1]
            if actual == state:
                return
            time.sleep(0.25)
        raise RuntimeError("服务未在期限内进入要求的状态，禁止强制终止或继续升级")

    def stop(self):
        import pywintypes
        import win32service as ws

        try:
            self._service(lambda service: ws.ControlService(service, ws.SERVICE_CONTROL_STOP), ws.SERVICE_STOP)
        except pywintypes.error as exc:
            if exc.winerror != 1062:  # ERROR_SERVICE_NOT_ACTIVE
                raise
        self._wait(ws.SERVICE_STOPPED)

    def configure(self, version_dir: Path):
        import win32service as ws

        executable = version_dir / "SmdService/SmdService.exe"
        if not executable.is_file():
            raise RuntimeError("服务版本目录不存在")
        self._service(
            lambda service: ws.ChangeServiceConfig(
                service,
                ws.SERVICE_NO_CHANGE,
                ws.SERVICE_NO_CHANGE,
                ws.SERVICE_NO_CHANGE,
                f'"{executable}"',
                None,
                0,
                None,
                None,
                None,
                None,
            ),
            ws.SERVICE_CHANGE_CONFIG,
        )

        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(version_dir / "configure-acl.ps1"),
                "-DataDir",
                str(self.data),
                "-InstallDir",
                str(version_dir.parent.parent),
            ],
            check=True,
        )

    def migrate(self, version_dir: Path):
        subprocess.run([str(version_dir / "SmdService/SmdService.exe"), "--migrate"], check=True, timeout=300)

    def start(self):
        import pywintypes
        import win32service as ws

        try:
            self._service(lambda service: ws.StartService(service, None), ws.SERVICE_START)
        except pywintypes.error as exc:
            if exc.winerror != 1056:  # already running
                raise
        self._wait(ws.SERVICE_RUNNING)

    def request(self, path: str, *, token=None):
        client = json.loads((self.data / "client.json").read_text(encoding="utf-8"))
        request = urllib.request.Request(
            client["url"].rstrip("/") + path,
            data=b"" if token else None,
            headers={"X-Smd-Upgrade-Token": token} if token else {},
        )
        with self.opener.open(request, timeout=3) as response:
            return json.load(response)["data"]

    def healthy(self, version: str):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                state = self.request("/api/system/health")
                if state["version"] == version and state["status"] == "ready":
                    client = json.loads((self.data / "client.json").read_text(encoding="utf-8"))
                    with self.opener.open(client["url"], timeout=3) as page:
                        if page.status == 200 and "text/html" in page.headers.get("Content-Type", ""):
                            return
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.5)
        raise RuntimeError(f"版本 {version} 未通过数据库/schema/存储/备份及前端健康检查")
