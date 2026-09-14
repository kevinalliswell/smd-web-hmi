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
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import windows_powershell
from .bundle import sha256, verify_bundle
from .installation_paths import cleanup_stage, prepare_stage, space_check, validate_paths
from .installer_authorization import ConfirmationRequired, activity_unknown, authorize, environment
from .runtime import data_root
from .single_instance import single_instance
from .storage import atomic_json, atomic_text
from .uninstall import UninstallTransaction
from .upgrade import UpgradeTransaction
from .windows_platform import WindowsPlatform

MAINTENANCE_REQUIRED_EXIT_CODE = 20


class MaintenanceRequired(RuntimeError):
    """The verified upgrade target has not been prepared by the running application."""

    def __init__(self, target_version: str):
        super().__init__(f"安装 {target_version} 需要现场停机确认，请交互运行安装器；无需登录旧版或填写版本号。")


def _change_start_type(ws, service, start_type: int) -> None:
    ws.ChangeServiceConfig(
        service,
        ws.SERVICE_NO_CHANGE,
        start_type,
        ws.SERVICE_NO_CHANGE,
        None,
        None,
        0,
        None,
        None,
        None,
        None,
    )


def _set_start_type(start_type: int) -> None:
    import win32service as ws

    manager = ws.OpenSCManager(None, None, ws.SC_MANAGER_CONNECT)
    try:
        service = ws.OpenService(manager, "SmdHmi", ws.SERVICE_CHANGE_CONFIG)
        try:
            _change_start_type(ws, service, start_type)
        finally:
            ws.CloseServiceHandle(service)
    finally:
        ws.CloseServiceHandle(manager)


def initialize(package: Path, install: Path, data: Path, platform: WindowsPlatform):
    manifest = verify_bundle(package)
    from .reinstall import PreservedReinstall

    initial_path = data / "updates/install.json"
    env_file = data / "config/service.env"
    if (data / "installation.json").exists():
        raise RuntimeError("已有安装状态，禁止覆盖现场数据")
    preserved = None
    if (data / "uninstalled.json").exists():
        values, _ = environment(data) if env_file.exists() else ({}, None)
        preserved = PreservedReinstall(
            install,
            data,
            platform,
            target_version=manifest["version"],
            package_sha256=sha256(package / "manifest.json"),
            min_db_free_bytes=int(values.get("SMD_STORAGE_MIN_FREE_BYTES") or 1024**3),
        )
    if env_file.exists() and not initial_path.exists() and preserved is None:
        raise RuntimeError("发现不属于安装事务的配置，禁止覆盖")
    if initial_path.exists():
        pending = json.loads(initial_path.read_text(encoding="utf-8"))
        if pending["version"] != manifest["version"]:
            raise RuntimeError("首次安装未完成，请用同一版本继续恢复")
    target = install / "versions" / manifest["version"]
    if target.exists():
        if verify_bundle(target) != manifest:
            raise RuntimeError("残留版本内容不同")
    elif package.resolve().is_relative_to((install / ".staging").resolve()):
        target.parent.mkdir(parents=True, exist_ok=True)
        package.replace(target)
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
    service = None
    try:
        try:
            service = ws.CreateService(
                manager,
                "SmdHmi",
                "SMD Experiment Backend",
                ws.SERVICE_ALL_ACCESS,
                ws.SERVICE_WIN32_OWN_PROCESS,
                ws.SERVICE_DEMAND_START,
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
            service = ws.OpenService(manager, "SmdHmi", ws.SERVICE_QUERY_CONFIG | ws.SERVICE_CHANGE_CONFIG)
            if ws.QueryServiceConfig(service)[3] != executable:
                raise RuntimeError("已有同名服务路径不同，拒绝接管") from exc
            # A previously interrupted initializer may have enabled automatic
            # startup already. Re-establish the guarded initialization sequence.
            _change_start_type(ws, service, ws.SERVICE_DEMAND_START)
    finally:
        if service is not None:
            ws.CloseServiceHandle(service)
        ws.CloseServiceHandle(manager)
    subprocess.run(["sc.exe", "sidtype", "SmdHmi", "unrestricted"], check=True)
    acl_command = [
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(target / "configure-acl.ps1"),
        "-DataDir",
        str(data),
        "-InstallDir",
        str(install),
    ]
    windows_powershell.run(acl_command, check=True)  # 写任何口令之前收紧 ACL。
    initial = {
        "version": manifest["version"],
        "target": str(target),
        "phase": "prepared",
        "package_sha256": sha256(target / "manifest.json"),
    }
    atomic_json(initial_path, initial)
    password = data / "config/bootstrap-admin-password.txt"
    if preserved is None and not password.exists():
        atomic_text(password, secrets.token_urlsafe(24) + "\n")
    if preserved is None and not env_file.exists():
        env = {
            "SMD_HOST": "127.0.0.1",
            "SMD_PORT": "8000",
            "SMD_DB_PATH": str(data / "db/smd.db"),
            "SMD_JWT_SECRET": secrets.token_hex(48),
            "HOSTCOMM_MOCK": "false",
            "HOSTCOMM_HOST": "192.168.1.100",
            "HOSTCOMM_PORT": "34211",
            "PROTOCOL_VERSION": "2.0",
            "HOSTCOMM_DEVICE_ID": "",
            "HOSTCOMM_CONTROLLER_ID": "",
            "HOSTCOMM_CONTROLLER_EPOCH": "",
            "HOSTCOMM_PSK_FILE": "",
            "SMD_BOOTSTRAP_ADMIN_PASSWORD_FILE": str(password),
        }
        # dotenv handles quoted backslashes, but does not decode JSON's Unicode escapes.
        atomic_text(
            env_file, "\n".join(key + "=" + json.dumps(value, ensure_ascii=False) for key, value in env.items()) + "\n"
        )
    if not (data / "client.json").exists():
        atomic_json(data / "client.json", {"url": "http://127.0.0.1:8000"})
    windows_powershell.run(acl_command, check=True)
    platform.configure(target)
    try:
        if preserved is not None:
            if preserved.before_migrate():
                platform.migrate(target)
                preserved.mark_migrated()
        else:
            platform.stop()
            platform.migrate(target)
        # configure() has installed the recovery task. Only now are migrations
        # durable and (for preserved data) the blocking maintenance gate present.
        atomic_json(initial_path, {**initial, "phase": "migrated"})
        _set_start_type(ws.SERVICE_AUTO_START)
        platform.start()
        platform.healthy(manifest["version"])
    except Exception as error:
        try:
            _set_start_type(ws.SERVICE_DEMAND_START)
        finally:
            if preserved is not None:
                preserved.failed(error)
                windows_powershell.run(acl_command, check=True)
            else:
                platform.stop()
        raise
    atomic_json(
        data / "installation.json",
        {"version": manifest["version"], "manifest_sha256": sha256(target / "manifest.json")},
    )
    if preserved is not None:
        preserved.commit()
    initial_path.unlink()


def confirm_local(message: str, *, non_interactive: bool, confirmed: bool) -> bool:
    if confirmed:
        return True
    if non_interactive:
        return False
    # YES/NO + warning icon + default NO. Only the elevated installer calls this.
    return ctypes.windll.user32.MessageBoxW(None, message, "SMD HMI 安装维护", 0x4 | 0x30 | 0x100) == 6


def dispatch(args, data: Path, platform: WindowsPlatform):
    validate_paths(args.install, data)
    from .reinstall import finish_committed_reinstall

    finish_committed_reinstall(args.install, data)
    if args.preflight:
        if not args.stage_output or args.payload_bytes <= 0:
            raise RuntimeError("安装预检缺少离线包大小或暂存输出路径")
        stage = prepare_stage(args.install, data, args.payload_bytes)
        # NSIS FileRead uses the system ANSI codepage. Explicit UTF-16LE avoids
        # corrupting Chinese/custom paths when the system locale differs.
        args.stage_output.write_text(str(stage) + "\r\n", encoding="utf-16-le")
        return
    minimum = 1024**3
    if (data / "config/service.env").is_file():
        values, _ = environment(data)
        minimum = int(values.get("SMD_STORAGE_MIN_FREE_BYTES") or minimum)
    transaction = UpgradeTransaction(args.install, data, platform, min_db_free_bytes=minimum)
    uninstall = UninstallTransaction(args.install, data, platform)
    if args.uninstall:
        # Pending uninstall can only be continued by an explicit uninstall action.
        pointer = (
            json.loads((data / "installation.json").read_text(encoding="utf-8"))
            if (data / "installation.json").exists()
            else None
        )
        permit = None
        if not uninstall.journal_path.exists():
            if pointer is None:
                raise RuntimeError("缺少可核对的安装记录")
            package_hash = pointer.get("manifest_sha256") or sha256(
                args.install / "versions" / pointer["version"] / "manifest.json"
            )
            permit = obtain_permit(
                args, data, platform, pointer["version"], pointer["version"], package_hash, "uninstall"
            )
        uninstall.apply(permit=permit)
        return
    if uninstall.journal_path.exists():
        journal = json.loads(uninstall.journal_path.read_text(encoding="utf-8"))
        if journal.get("phase") != "committed":
            raise RuntimeError("上次卸载未完成。请重新运行卸载程序；安装器不会自动继续卸载。")
    if args.package is not None:
        manifest = verify_bundle(args.package)
        if not args.package.resolve().is_relative_to((args.install / ".staging").resolve()):
            raise RuntimeError("安装包必须由安装器暂存到程序磁盘，禁止重复复制完整运行环境")
        space_check(args.install, data)
    if transaction.journal_path.exists():
        journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
        legacy_failed = journal.get("schema_version") == 1 and journal.get("phase") == "rollback_failed"
        if legacy_failed and not confirm_local(
            "检测到旧版回退未完成。安装器将先额外备份当前数据库和配置，再恢复上次升级前的快照。"
            "\n快照之后新增的记录保留在额外备份中，不会自动合并。是否恢复后继续安装？",
            non_interactive=args.non_interactive,
            confirmed=args.confirm_recovery,
        ):
            raise RuntimeError("尚未确认旧版恢复，请交互运行安装器；现有数据和恢复记录已保留")
        transaction.recover(preserve_current=legacy_failed)
    else:
        transaction.recover()
    if args.recover:
        initial = data / "updates/install.json"
        if initial.exists() and not (data / "installation.json").exists():
            pending = json.loads(initial.read_text(encoding="utf-8"))
            initialize(Path(pending["target"]), args.install, data, platform)
        else:
            platform.start()
        return
    if args.package is None:
        raise RuntimeError("缺少已验证离线包")
    if not (data / "installation.json").exists():
        if (data / "uninstalled.json").exists():
            if (data / "config/service.env").is_file():
                values, database = environment(data)
                unknown = (
                    values.get("PROTOCOL_VERSION") != "2.0"
                    or bool(values.get("HOSTCOMM_DEVICE_ID"))
                    or activity_unknown(database)
                )
            else:
                unknown = True
            if unknown and not confirm_local(
                "即将保留原资料重新安装。请确认设备已在现场安全停机，且无需继续采集。是否继续？",
                non_interactive=args.non_interactive,
                confirmed=args.confirm_physical_shutdown,
            ):
                raise MaintenanceRequired(manifest["version"])
        initialize(args.package, args.install, data, platform)
    else:
        previous = json.loads((data / "installation.json").read_text(encoding="utf-8"))
        transaction.validate_target(args.package)
        operation = "repair" if previous["version"] == manifest["version"] else "upgrade"
        permit = obtain_permit(
            args,
            data,
            platform,
            previous["version"],
            manifest["version"],
            sha256(args.package / "manifest.json"),
            operation,
        )
        transaction.apply(args.package, permit)


def obtain_permit(args, data, platform, current, target, package_hash, operation):
    options = dict(
        current=current,
        target=target,
        package_sha256=package_hash,
        operation=operation,
        physical_shutdown_confirmed=args.confirm_physical_shutdown,
    )
    try:
        return authorize(data, platform, **options)
    except ConfirmationRequired as error:
        if not confirm_local(
            str(error) + "\n是否确认设备已在现场安全停机？",
            non_interactive=args.non_interactive,
            confirmed=args.confirm_physical_shutdown,
        ):
            raise MaintenanceRequired(target) from error
        options["physical_shutdown_confirmed"] = True
        return authorize(data, platform, **options)


def run():
    if "--self-check" in sys.argv:
        import win32service  # noqa: F401

        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--payload-bytes", type=int, default=0)
    parser.add_argument("--stage-output", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--confirm-physical-shutdown", action="store_true")
    parser.add_argument("--confirm-recovery", action="store_true")
    parser.add_argument("--pair-device", help="Offline HostComm 2.0 device UUID (32 lowercase hex)")
    parser.add_argument("--controller-id")
    parser.add_argument("--controller-epoch")
    parser.add_argument(
        "--import-psk", type=Path, help="Private file containing a 32-byte key encoded as 64 hex digits"
    )
    parser.add_argument("--replace-pairing", action="store_true")
    parser.add_argument("--recover-pairing", action="store_true")
    parser.add_argument("--reason", help="Offline pairing maintenance audit reason")
    args = parser.parse_args()
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("安装与恢复必须在 Windows 管理员终端执行")
    data = data_root()
    with single_instance("SmdHmi.Updater", data / "updater.lock"):
        platform = WindowsPlatform(data)
        if args.pair_device or args.recover_pairing:
            if args.package or args.recover or args.uninstall or (args.pair_device and args.recover_pairing):
                raise RuntimeError("配对与安装/升级/卸载/其他恢复必须分别执行")
            from .pairing import PairingTransaction

            pairing = PairingTransaction(data, platform)
            result = (
                pairing.recover()
                if args.recover_pairing
                else pairing.apply(
                    device_id=args.pair_device,
                    controller_id=args.controller_id,
                    controller_epoch=args.controller_epoch,
                    import_psk=args.import_psk,
                    replace=args.replace_pairing,
                    reason=args.reason or "",
                )
            )
            print(json.dumps(result, ensure_ascii=False))  # Identifiers and protected paths only; never the PSK.
            return
        if any((args.controller_id, args.controller_epoch, args.import_psk, args.replace_pairing, args.reason)):
            raise RuntimeError("配对参数必须与 --pair-device 一起使用")
        if sum(bool(value) for value in (args.package, args.recover, args.uninstall, args.preflight)) != 1:
            raise RuntimeError("安装、恢复、卸载和预检必须分别执行")
        try:
            dispatch(args, data, platform)
        finally:
            platform.release_backend_guard()
            if args.package is not None:
                try:
                    cleanup_stage(args.install, data, args.package)
                except (OSError, ValueError):
                    logging.getLogger(__name__).warning("unused_installer_stage_cleanup_failed")


def main():
    if "--self-check" in sys.argv:
        run()
        return
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("安装与恢复必须在 Windows 管理员终端执行")
    logs = data_root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "updater.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    try:
        run()
    except MaintenanceRequired as exc:
        logging.exception("Upgrade maintenance is required")
        raise SystemExit(MAINTENANCE_REQUIRED_EXIT_CODE) from exc
    except Exception:
        logging.exception("Installation or recovery failed")
        raise
