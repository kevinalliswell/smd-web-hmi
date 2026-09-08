"""Windows SCM 适配：状态查询不依赖本地化 sc.exe 输出。"""

import json
import logging
import os
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path

from . import windows_powershell


def _health_version(value: object) -> str:
    # This is a log allowlist, not a new version/installation gate.
    pattern = r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}(?:-(?:alpha|beta|rc)\.[0-9]{1,6})?"
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else "unknown"


def _health_choice(value: object, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _health_network_error(error: BaseException) -> str:
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    for kind, name in (
        (TimeoutError, "timeout"),
        (ConnectionRefusedError, "connection_refused"),
        (ConnectionResetError, "connection_reset"),
        (ssl.SSLError, "tls_error"),
        (socket.gaierror, "dns_error"),
        (HTTPException, "http_protocol_error"),
    ):
        if isinstance(cause, kind):
            return name
    return "network_error"


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
            pending = (
                exc.winerror == 1061
                and self._service(ws.QueryServiceStatus, ws.SERVICE_QUERY_STATUS)[1] == ws.SERVICE_STOP_PENDING
            )
            if exc.winerror != 1062 and not pending:  # ERROR_SERVICE_NOT_ACTIVE
                raise
        self._wait(ws.SERVICE_STOPPED)

    def remove_service(self):
        """DeleteService只标记删除；确认记录消失后才能完成卸载。"""
        import pywintypes
        import win32service as ws

        try:
            self.stop()
        except pywintypes.error as exc:
            if exc.winerror == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
                return
            if exc.winerror != 1072:  # ERROR_SERVICE_MARKED_FOR_DELETE
                raise
        try:
            self._service(ws.DeleteService, 0x00010000)  # DELETE access right
        except pywintypes.error as exc:
            if exc.winerror == 1060:
                return
            if exc.winerror != 1072:
                raise
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                self._service(ws.QueryServiceStatus, ws.SERVICE_QUERY_STATUS)
            except pywintypes.error as exc:
                if exc.winerror == 1060:
                    return
                if exc.winerror != 1072:
                    raise
            time.sleep(0.25)
        raise RuntimeError("服务仍被标记删除或持有句柄，请关闭服务管理器后重试卸载")

    def remove_recovery_task(self):
        """用HRESULT区分任务已删除和权限/调度器故障，不解析本地化命令输出。"""
        import pywintypes
        import win32com.client

        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        try:
            folder.DeleteTask("SmdHmi-Recover", 0)
        except pywintypes.com_error as exc:
            codes = [exc.hresult]
            if exc.excepinfo and exc.excepinfo[5] is not None:
                codes.append(exc.excepinfo[5])
            if not any(code & 0xFFFFFFFF == 0x80070002 for code in codes):  # ERROR_FILE_NOT_FOUND
                raise

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

        windows_powershell.run(
            [
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

        windows_powershell.run(
            [
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(version_dir / "configure-recovery.ps1"),
                "-VersionDir",
                str(version_dir),
                "-InstallDir",
                str(version_dir.parent.parent),
            ],
            check=True,
        )

        self.configure_presentation(version_dir)

    def configure_presentation(self, version_dir: Path):
        """服务版本切换与回退一并恢复入口，不能依赖安装器最后几步才更新。"""
        import winreg

        import win32com.client

        install = version_dir.parent.parent
        access = winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, r"Software\SmdHmi", 0, access) as key:
            for name, value in {"InstallDir": str(install), "Version": version_dir.name}.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        with winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi", 0, access
        ) as key:
            for name, value in {
                "DisplayName": "SMD HMI",
                "DisplayVersion": version_dir.name,
                "UninstallString": f'"{install / "Uninstall.exe"}"',
            }.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        menu = Path(os.environ["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs/SMD HMI"
        menu.mkdir(parents=True, exist_ok=True)
        # WSH的CreateShortcut/TargetPath/Save契约见Microsoft Learn的WSH快捷方式说明。
        shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(menu / "SMD HMI.lnk"))
        shortcut.TargetPath = str(version_dir / "SmdDesktop/SmdDesktop.exe")
        shortcut.WorkingDirectory = str(version_dir / "SmdDesktop")
        shortcut.Description = "SMD 软熔滴落实验"
        shortcut.Save()

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

    def healthy(self, version: str) -> None:
        deadline = time.monotonic() + self.timeout
        last = None
        diagnostic = {"phase": "client", "reason": "deadline_elapsed", "expected_version": _health_version(version)}
        while time.monotonic() < deadline:
            diagnostic = {
                "phase": "client",
                "reason": "invalid_client_config",
                "expected_version": _health_version(version),
            }
            try:
                client = json.loads((self.data / "client.json").read_text(encoding="utf-8"))
                request = urllib.request.Request(client["url"].rstrip("/") + "/api/system/health")
                diagnostic.update(phase="backend", reason="invalid_response")
                with self.opener.open(request, timeout=3) as response:
                    diagnostic["http_status"] = response.status
                    state = json.load(response)["data"]
                diagnostic.update(
                    observed_version=_health_version(state["version"]),
                    observed_status=_health_choice(state["status"], {"ready", "not_ready"}),
                )
                checks = state.get("checks", {})
                checks = checks if isinstance(checks, dict) else {}
                diagnostic["checks"] = {
                    name: _health_choice(checks.get(name), allowed)
                    for name, allowed in {
                        "database": {"ok", "error"},
                        "schema": {"ok", "outdated"},
                        "storage": {"ok", "low", "error"},
                        "backup": {"ok", "error"},
                        "hostcomm": {"online", "offline", "degraded", "good"},
                    }.items()
                }
                free_bytes = checks.get("storage_free_bytes")
                diagnostic["checks"]["storage_free_bytes"] = (
                    free_bytes if type(free_bytes) is int and 0 <= free_bytes <= 2**64 - 1 else "unknown"
                )
                if state["version"] != version:
                    diagnostic["reason"] = "version_mismatch"
                elif state["status"] != "ready":
                    diagnostic["reason"] = "backend_not_ready"
                else:
                    diagnostic.update(phase="client", reason="invalid_client_config")
                    diagnostic.pop("http_status", None)
                    client = json.loads((self.data / "client.json").read_text(encoding="utf-8"))
                    diagnostic.update(phase="frontend", reason="invalid_response")
                    with self.opener.open(client["url"], timeout=3) as page:
                        diagnostic["http_status"] = page.status
                        html = "text/html" in page.headers.get("Content-Type", "")
                        if page.status == 200 and html:
                            return
                        diagnostic["reason"] = "frontend_http_status" if page.status != 200 else "frontend_not_html"
            except urllib.error.HTTPError as exc:
                diagnostic.update(reason="http_status", http_status=exc.code)
                exc.close()
            except (OSError, HTTPException) as exc:
                if diagnostic["phase"] != "client":
                    diagnostic.update(reason="network_error", network_type=_health_network_error(exc))
            except (ValueError, KeyError, TypeError, AttributeError):
                # Never log response bodies, URLs, configuration or exception text.
                pass
            if diagnostic != last:
                logging.getLogger(__name__).warning("upgrade_health_probe %s", json.dumps(diagnostic, sort_keys=True))
                last = diagnostic
            time.sleep(0.5)
        detail = json.dumps(diagnostic, sort_keys=True)
        raise RuntimeError(
            f"版本 {_health_version(version)} 未通过数据库/schema/存储/备份及前端健康检查; health_diagnostic={detail}"
        )
