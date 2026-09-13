"""Installed registration is authoritative; a new package cannot relocate live data."""

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from .installer_authorization import environment, regular_path
from .space import MIN_DB_FREE_BYTES, check_upgrade_space


def registered_paths() -> dict[str, str]:
    if os.name != "nt":
        return {}
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"Software\SmdHmi", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as key:
            result = {}
            for name in ("InstallDir", "DataDir"):
                try:
                    result[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    pass
            return result
    except FileNotFoundError:
        return {}


def validate_paths(install: Path, data: Path) -> None:
    for path in (install, data):
        if not path.is_absolute():
            raise RuntimeError("程序和数据目录必须是绝对路径")
        regular_path(path)
    registered = registered_paths()
    for key, value in (("InstallDir", install), ("DataDir", data)):
        if registered.get(key) and Path(registered[key]).resolve() != value.resolve():
            raise RuntimeError("已有安装必须保留注册的程序和数据目录，不能由新安装包改变")
    if install.resolve() == data.resolve() or data.resolve().is_relative_to(install.resolve()):
        raise RuntimeError("持久数据目录不能位于程序安装目录内")


def space_check(install: Path, data: Path, *, payload_bytes=0, preserve_current=False):
    if (data / "config/service.env").is_file():
        values, database = environment(data)
        minimum = int(values.get("SMD_STORAGE_MIN_FREE_BYTES") or MIN_DB_FREE_BYTES)
    else:
        database, minimum = data / "db/smd.db", MIN_DB_FREE_BYTES
    return check_upgrade_space(
        install,
        data,
        database,
        payload_bytes=payload_bytes,
        min_db_free_bytes=minimum,
        preserve_current=preserve_current,
    )


def prepare_stage(install: Path, data: Path, payload_bytes: int) -> Path:
    validate_paths(install, data)
    space_check(install, data, payload_bytes=payload_bytes)
    stage = install / ".staging" / uuid.uuid4().hex / "payload"
    stage.mkdir(parents=True, exist_ok=False)
    if os.name == "nt":
        import win32security as security

        descriptor = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FRFX;;;BU)", 1
        )
        for folder in (install, install / ".staging", stage.parent, stage):
            acl = descriptor.GetSecurityDescriptorDacl()
            if folder == install:
                # Preserve explicit denies and the existing WebView2 traversal
                # grants while the old installation is still running. Other
                # application-directory access remains limited to this policy.
                previous = security.GetNamedSecurityInfo(
                    str(folder), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
                ).GetSecurityDescriptorDacl()
                retained = security.ACL()
                if previous is not None:
                    for index in range(previous.GetAceCount()):
                        ace = previous.GetAce(index)
                        if ace[0][0] == security.ACCESS_DENIED_ACE_TYPE:
                            retained.AddAccessDeniedAceEx(security.ACL_REVISION_DS, ace[0][1], ace[1], ace[2])
                for index in range(acl.GetAceCount()):
                    ace = acl.GetAce(index)
                    retained.AddAccessAllowedAceEx(security.ACL_REVISION_DS, ace[0][1], ace[1], ace[2])
                for sid in ("S-1-15-2-1", "S-1-15-2-2"):
                    retained.AddAccessAllowedAceEx(
                        security.ACL_REVISION_DS, 0, 0x20, security.ConvertStringSidToSid(sid)
                    )
                acl = retained
            security.SetNamedSecurityInfo(
                str(folder),
                security.SE_FILE_OBJECT,
                security.OWNER_SECURITY_INFORMATION
                | security.DACL_SECURITY_INFORMATION
                | security.PROTECTED_DACL_SECURITY_INFORMATION,
                descriptor.GetSecurityDescriptorOwner(),
                None,
                acl,
                None,
            )
    return stage


def cleanup_stage(install: Path, data: Path, package: Path) -> None:
    """Discard only this unused payload, never transaction recovery files or backups."""
    regular_path(package)
    path = package.resolve()
    if (
        path.name != "payload"
        or path.parent.parent != (install / ".staging").resolve()
        or not re.fullmatch(r"[a-f0-9]{32}", path.parent.name)
    ):
        return
    active = data / "updates/active.json"
    if active.exists():
        journal = json.loads(active.read_text(encoding="utf-8"))
        if journal.get("phase") not in {"committed", "rolled_back"} and journal.get("staging_path") == str(path):
            return
    if path.is_dir():
        shutil.rmtree(path)
    if path.parent.is_dir() and not any(path.parent.iterdir()):
        path.parent.rmdir()
