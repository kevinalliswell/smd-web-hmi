"""由提升权限的 NSIS 调用；普通桌面进程从不导入此入口。"""

import argparse
import ctypes
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .bundle import verify_bundle
from .runtime import data_root
from .single_instance import single_instance
from .storage import atomic_json, atomic_text
from .upgrade import UpgradeTransaction
from .windows_platform import WindowsPlatform


def initialize(package: Path, install: Path, data: Path, platform: WindowsPlatform):
    manifest = verify_bundle(package)
    initial_path = data / "updates/install.json"
    env_file = data / "config/service.env"
    if (data / "installation.json").exists():
        raise RuntimeError("已有安装状态，禁止覆盖现场数据")
    if env_file.exists() and not initial_path.exists():
        raise RuntimeError("发现不属于安装事务的配置，禁止覆盖")
    if initial_path.exists():
        pending = json.loads(initial_path.read_text(encoding="utf-8"))
        if pending["version"] != manifest["version"]:
            raise RuntimeError("首次安装未完成，请用同一版本继续恢复")
    target = install / "versions" / manifest["version"]
    if target.exists():
        if verify_bundle(target) != manifest:
            raise RuntimeError("残留版本内容不同")
    else:
        staged = target.with_name("." + target.name + ".installing")
        if staged.exists():
            shutil.rmtree(staged)
        shutil.copytree(package, staged)
        verify_bundle(staged)
        staged.replace(target)
    for folder in ["config", "logs", "db", "updates"]:
        (data / folder).mkdir(parents=True, exist_ok=True)
    import pywintypes
    import win32service as ws

    manager = ws.OpenSCManager(None, None, ws.SC_MANAGER_ALL_ACCESS)
    executable = f'"{target / "SmdService/SmdService.exe"}"'
    try:
        try:
            service = ws.CreateService(
                manager,
                "SmdHmi",
                "SMD Experiment Backend",
                ws.SERVICE_ALL_ACCESS,
                ws.SERVICE_WIN32_OWN_PROCESS,
                ws.SERVICE_AUTO_START,
                ws.SERVICE_ERROR_NORMAL,
                executable,
                None,
                0,
                None,
                "NT AUTHORITY\\LocalService",
                None,
            )
        except pywintypes.error as exc:
            if exc.winerror != 1073:
                raise
            service = ws.OpenService(manager, "SmdHmi", ws.SERVICE_QUERY_CONFIG)
            if ws.QueryServiceConfig(service)[3] != executable:
                ws.CloseServiceHandle(service)
                raise RuntimeError("已有同名服务路径不同，拒绝接管") from exc
        ws.CloseServiceHandle(service)
    finally:
        ws.CloseServiceHandle(manager)
    subprocess.run(["sc.exe", "sidtype", "SmdHmi", "unrestricted"], check=True)
    acl_command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(target / "configure-acl.ps1"),
        "-DataDir",
        str(data),
        "-InstallDir",
        str(install),
    ]
    subprocess.run(acl_command, check=True)  # 写任何口令之前收紧 ACL。
    atomic_json(initial_path, {"version": manifest["version"], "target": str(target)})
    password = data / "config/bootstrap-admin-password.txt"
    if not password.exists():
        atomic_text(password, secrets.token_urlsafe(24) + "\n")
    if not env_file.exists():
        env = {
            "SMD_HOST": "127.0.0.1",
            "SMD_PORT": "8000",
            "SMD_DB_PATH": str(data / "db/smd.db"),
            "SMD_JWT_SECRET": secrets.token_hex(48),
            "HOSTCOMM_MOCK": "false",
            "HOSTCOMM_HOST": "192.168.1.100",
            "HOSTCOMM_PORT": "34211",
            "SMD_BOOTSTRAP_ADMIN_PASSWORD_FILE": str(password),
        }
        atomic_text(env_file, "\n".join(key + "=" + json.dumps(value) for key, value in env.items()) + "\n")
    atomic_json(data / "client.json", {"url": "http://127.0.0.1:8000"})
    subprocess.run(acl_command, check=True)
    platform.configure(target)
    platform.stop()
    platform.migrate(target)
    platform.start()
    platform.healthy(manifest["version"])
    atomic_json(data / "installation.json", {"version": manifest["version"]})
    initial_path.unlink()


def run():
    if "--self-check" in sys.argv:
        import win32service  # noqa: F401

        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("安装与恢复必须在 Windows 管理员终端执行")
    data = data_root()
    with single_instance("SmdHmi.Updater", data / "updater.lock"):
        platform = WindowsPlatform(data)
        transaction = UpgradeTransaction(args.install, data, platform)
        transaction.recover()
        if args.recover:
            initial = data / "updates/install.json"
            if initial.exists() and not (data / "installation.json").exists():
                pending = json.loads(initial.read_text(encoding="utf-8"))
                initialize(Path(pending["target"]), args.install, data, platform)
            else:
                platform.start()
            return
        if args.uninstall:
            current = json.loads((data / "installation.json").read_text(encoding="utf-8"))
            gate = json.loads((data / "maintenance.json").read_text(encoding="utf-8"))
            if gate.get("target_version") != current["version"]:
                raise RuntimeError("卸载前先在系统维护中准备当前版本，要求无未闭合实验")
            platform.request("/api/system/maintenance/claim", token=gate["token"])
            platform.stop()
            subprocess.run(["schtasks.exe", "/Delete", "/TN", "SmdHmi-Recover", "/F"], check=True)
            subprocess.run(["sc.exe", "delete", "SmdHmi"], check=True)
            # 数据与备份保留，禁止卸载程序递归删除 ProgramData。
            (data / "installation.json").replace(data / "uninstalled.json")
            (data / "maintenance.json").unlink(missing_ok=True)
            return
        if args.package is None:
            raise RuntimeError("缺少已验证离线包")
        if not (data / "installation.json").exists():
            initialize(args.package, args.install, data, platform)
        else:
            manifest = verify_bundle(args.package)
            gate = json.loads((data / "maintenance.json").read_text(encoding="utf-8"))
            if gate.get("target_version") != manifest["version"]:
                raise RuntimeError("先以 Admin 在系统维护中准备这个目标版本")
            permit = platform.request("/api/system/maintenance/claim", token=gate["token"])
            transaction.apply(args.package, permit)


def main():
    if "--self-check" in sys.argv:
        run()
        return
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("安装与恢复必须在 Windows 管理员终端执行")
    logs = data_root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "updater.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    try:
        run()
    except Exception:
        logging.exception("Installation or recovery failed")
        raise
