"""Elevated local installer intent; no login, network maintenance token or second gateway."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing, nullcontext
from pathlib import Path

from dotenv import dotenv_values

from .single_instance import single_instance
from .storage import atomic_json


class ConfirmationRequired(RuntimeError):
    pass


def admin_sid() -> str:
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()


def regular_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise RuntimeError("安装维护路径不能经过符号链接或重解析目录")


def protected_json(path: Path, value: dict) -> None:
    """Protect the temporary file before publishing an administrator-owned request."""
    regular_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        temporary.touch(mode=0o600, exist_ok=False)
        if os.name == "nt":
            import win32security as security

            sid = security.ConvertSidToStringSid(security.LookupAccountName(None, "NT SERVICE\\SmdHmi")[0])
            descriptor = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f"O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;{sid})", 1
            )
            security.SetNamedSecurityInfo(
                str(temporary),
                security.SE_FILE_OBJECT,
                security.OWNER_SECURITY_INFORMATION
                | security.DACL_SECURITY_INFORMATION
                | security.PROTECTED_DACL_SECURITY_INFORMATION,
                descriptor.GetSecurityDescriptorOwner(),
                None,
                descriptor.GetSecurityDescriptorDacl(),
                None,
            )
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        if os.name == "nt":
            import win32file

            win32file.MoveFileEx(str(temporary), str(path), 0x1 | 0x8)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def environment(data: Path) -> tuple[dict, Path]:
    env_path = data / "config/service.env"
    regular_path(env_path)
    values = dotenv_values(env_path, interpolate=False, encoding="utf-8")
    database = Path(values.get("SMD_DB_PATH") or "")
    regular_path(database)
    if not database.is_absolute() or not database.is_file():
        raise RuntimeError("无法确认实际数据库位置，请保留数据并核对已有服务配置")
    return values, database.resolve()


def activity_unknown(database: Path) -> bool:
    """Read-only durable evidence. Missing schema/evidence never means safely idle."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("已有数据库完整性检查失败，禁止迁移")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"test_session", "v2_operation"} <= tables:
            return True
        if connection.execute("SELECT 1 FROM test_session WHERE end_time IS NULL LIMIT 1").fetchone():
            return True
        if (
            "operation" in tables
            and connection.execute(
                "SELECT 1 FROM operation WHERE status IN ('pending','sent','accepted','unknown') LIMIT 1"
            ).fetchone()
        ):
            return True
        return (
            connection.execute(
                "SELECT 1 FROM v2_operation WHERE reconciled=0 AND status IN "
                "('pending','sent','accepted','unknown','result_expired','not_found') LIMIT 1"
            ).fetchone()
            is not None
        )


def request_digest(request: dict) -> str:
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def authorize(
    data: Path,
    platform,
    *,
    current: str,
    target: str,
    package_sha256: str,
    operation: str = "upgrade",
    physical_shutdown_confirmed: bool = False,
    timeout: float = 20,
) -> dict:
    values, database = environment(data)
    request = {
        "schema_version": 1,
        "transaction_id": uuid.uuid4().hex,
        "operation": operation,
        "current_version": current,
        "target_version": target,
        "package_sha256": package_sha256,
        "admin_sid": admin_sid(),
        "physical_shutdown_confirmed": physical_shutdown_confirmed,
        "created_at": time.time(),
    }
    digest = request_digest(request)
    legacy = current in {"0.3.0-rc.4", "0.3.0-rc.5"}
    stopped_service = not legacy and platform.service_stopped()
    if legacy or stopped_service:
        # A damaged stopped service cannot reply. This fallback is selected from
        # a fresh SCM/process observation before writing the intent, never from a
        # live service timeout. stop() below reconfirms exit and owns Backend mutex.
        unknown = (
            values.get("PROTOCOL_VERSION") != "2.0"
            or bool(values.get("HOSTCOMM_DEVICE_ID"))
            or activity_unknown(database)
        )
        if unknown and not physical_shutdown_confirmed:
            raise ConfirmationRequired(
                "当前服务无法提供可信实时空闲状态。请确认设备已在现场停止加热、供气和运动，且无需继续采集。"
            )
        gate = {
            "schema_version": 2,
            "issuer": "windows_installer",
            "state": "prepared",
            "upgrade_id": request["transaction_id"],
            "operation": operation,
            "current_version": current,
            "target_version": target,
            "package_sha256": package_sha256,
            "admin_sid": request["admin_sid"],
            "physical_shutdown_confirmed": physical_shutdown_confirmed,
            "request_sha256": digest,
            "db_path": str(database),
            "created_at": request["created_at"],
        }
        existing = data / "maintenance.json"
        if existing.exists():
            previous = json.loads(existing.read_text(encoding="utf-8"))
            old_prepared = (
                legacy
                and previous.get("state") == "prepared"
                and previous.get("issuer") is None
                and previous.get("current_version") == current
                and Path(previous.get("db_path", "")).resolve() == database
                and isinstance(previous.get("upgrade_id"), str)
                and len(previous["upgrade_id"]) == 32
                and all(character in "0123456789abcdef" for character in previous["upgrade_id"])
            )
            if old_prepared:
                # Keep the old gate blocking commands until it is atomically replaced.
                protected_json(data / "updates" / ("superseded-" + previous["upgrade_id"] + ".json"), previous)
            elif previous.get("issuer") != "windows_installer" or any(
                previous.get(key) != gate[key]
                for key in ("current_version", "target_version", "operation", "package_sha256")
            ):
                raise RuntimeError("存在另一项维护记录，请先由安装器恢复该事务")
        protected_json(data / "maintenance-request.json", request)
        protected_json(existing, gate)  # rc.4/5 command_guard recognizes this blocking state.
        platform.stop()
        guard = (
            nullcontext()
            if hasattr(platform, "release_backend_guard")
            else single_instance("SmdHmi.Backend", data / "backend.lock")
        )
        with guard:
            _, stopped_database = environment(data)
            if stopped_database != database:
                raise RuntimeError("服务停止期间数据库配置发生变化")
            if activity_unknown(database) and not physical_shutdown_confirmed:
                raise ConfirmationRequired("服务停止后发现未闭合实验或命令；需要现场停机确认，历史记录将保持未知。")
            gate["state"] = "claimed"
            gate["authorization_source"] = "legacy_stopped_admin" if legacy else "stopped_service_admin"
            protected_json(existing, gate)
        return gate
    protected_json(data / "maintenance-request.json", request)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reply_path = data / "maintenance-reply.json"
        regular_path(reply_path)
        if reply_path.is_file():
            if reply_path.stat().st_size > 16384:
                raise RuntimeError("维护回复过大")
            reply = json.loads(reply_path.read_text(encoding="utf-8"))
            if reply.get("transaction_id") == request["transaction_id"]:
                if reply.get("request_sha256") != digest:
                    raise RuntimeError("维护回复与当前安装请求不匹配")
                if reply.get("state") == "confirmation_required":
                    raise ConfirmationRequired(reply.get("message", "设备状态未知，需要现场停机确认"))
                if reply.get("state") != "authorized":
                    raise RuntimeError(reply.get("message", "后台拒绝进入维护"))
                gate = reply.get("gate")
                stored = json.loads((data / "maintenance.json").read_text(encoding="utf-8"))
                if (
                    not isinstance(gate, dict)
                    or gate != stored
                    or any(
                        gate.get(key) != value
                        for key, value in {
                            "state": "claimed",
                            "issuer": "windows_installer",
                            "upgrade_id": request["transaction_id"],
                            "request_sha256": digest,
                            "operation": operation,
                            "current_version": current,
                            "target_version": target,
                            "package_sha256": package_sha256,
                            "db_path": str(database),
                            "schema_version": 2,
                            "admin_sid": request["admin_sid"],
                            "physical_shutdown_confirmed": physical_shutdown_confirmed,
                        }.items()
                    )
                ):
                    raise RuntimeError("维护回复和持久许可不一致")
                return gate
        time.sleep(0.1)
    raise RuntimeError("后台未在期限内响应安装维护请求；请重试安装器并查看服务日志")
