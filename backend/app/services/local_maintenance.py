"""ACL-protected installer intent; no network authorization or device-control bypass.

The Windows installer creates the request with an administrator-only write ACL.
Only packaged Windows service lifespans enable the reader. Requests and replies
are separate files so the service never edits the administrator's attestation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import TestSession
from app.db.operation_models import Operation
from app.db.v2_models import V2Operation
from app.hostcomm.client import HostCommError
from app.hostcomm.v2_transport import V2TransportError
from app.services.cache import status_cache
from app.services.maintenance_service import MaintenanceBlockedError, MaintenanceManager
from app.services.state_policy import classify_state

logger = get_logger("service.local_maintenance")
_VERSION = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$"


class MaintenanceIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: int = Field(ge=1, le=1)
    transaction_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    operation: Literal["install", "upgrade", "repair", "uninstall", "recover"]
    current_version: str = Field(pattern=_VERSION)
    target_version: str = Field(pattern=_VERSION)
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    admin_sid: str = Field(pattern=r"^S-1-5-(?:\d+-)*\d+$", max_length=184)
    physical_shutdown_confirmed: bool
    created_at: int | float


def canonical_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


_ADMIN_SIDS = {"S-1-5-18", "S-1-5-32-544"}
_FILE_CHANGE_MASK = 0x500D0116  # Generic all/write, DELETE, WRITE_DAC/OWNER and file writes.
_PARENT_REPLACE_MASK = 0x100C0040  # Generic all, WRITE_DAC/OWNER and DELETE_CHILD.


def validate_request_permissions(owner: str, protected: bool, aces: list[tuple[int, str]], *, parent=False) -> None:
    """Evaluate effective allow ACEs conservatively; deny ACEs never grant authority."""
    if owner not in _ADMIN_SIDS or not protected:
        raise ValueError("maintenance request ownership or DACL is not protected")
    mask = _PARENT_REPLACE_MASK if parent else _FILE_CHANGE_MASK
    for allowed, sid in aces:
        if sid not in _ADMIN_SIDS and allowed & mask:
            raise ValueError("maintenance request can be replaced or modified by a non-administrator")


def verify_request_acl(path: Path) -> None:
    if os.name != "nt":
        return  # Reader is never enabled by a non-Windows application lifespan.
    import win32security as security

    for item in (path, *path.parents):
        metadata = item.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("maintenance request path cannot traverse a reparse point")
    for item in (path, path.parent):
        descriptor = security.GetFileSecurity(
            str(item), security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION
        )
        owner = security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
        protected = bool(descriptor.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED)
        dacl = descriptor.GetSecurityDescriptorDacl()
        if dacl is None:
            raise ValueError("maintenance request has a null DACL")
        aces = []
        for index in range(dacl.GetAceCount()):
            header, mask, sid = dacl.GetAce(index)
            if header[0] == security.ACCESS_ALLOWED_ACE_TYPE:
                aces.append((mask, security.ConvertSidToStringSid(sid)))
            elif header[0] != security.ACCESS_DENIED_ACE_TYPE:
                raise ValueError("unsupported maintenance request ACL entry")
        validate_request_permissions(owner, protected, aces, parent=item == path.parent)


def _read_json(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("maintenance file must be a regular non-reparse file")
    if info.st_size > 16384:
        raise ValueError("maintenance file exceeds 16 KiB")
    value = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("maintenance file must contain an object")
    return value


def write_protected_json(path: Path, value: dict) -> None:
    """Preserve a protected Windows DACL across atomic file replacement."""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".service.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            if os.name == "nt":
                # Service SIDs are deterministic even before service registration.
                import struct

                import win32security

                words = struct.unpack("<5I", hashlib.sha1("SMDHMI".encode("utf-16le")).digest())
                sid = "S-1-5-80-" + "-".join(str(word) for word in words)
                security = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                    f"D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})", win32security.SDDL_REVISION_1
                )
                win32security.SetFileSecurity(
                    str(temporary),
                    win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    security,
                )
            json.dump(value, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def assess_maintenance(client, db, *, cache=None) -> tuple[str, str]:
    """Only fresh live activity is 'busy'; incomplete archives remain unknown."""
    cache = status_cache if cache is None else cache
    live_idle = False
    unpaired = bool(client and getattr(client, "protocol_version", None) == "2.0" and not client.device_id)
    if client and client.is_online and not unpaired:
        try:
            async with asyncio.timeout(3):
                await client.get_status()
            if not client.is_online:
                raise ValueError("device disconnected after status")
            if getattr(client, "protocol_version", None) == "2.0":
                frame = client._status_frame
                transport = getattr(client, "transport", None)
                if transport and (
                    frame.get("session_id") != transport.session_id or frame.get("boot_id") != transport.boot_id
                ):
                    raise ValueError("stale device session")
                run = frame["payload"]["run"]
                if transport:
                    receipt = transport.receipt_metadata(frame["msg_id"])
                    age = time.monotonic() - receipt.get("received_monotonic", float("-inf"))
                    if not 0 <= age <= 5 or int(run["state_revision"]) < transport.leases.minimum_revision:
                        raise ValueError("stale device status")
                if classify_state(run["state"], protocol_version="2.0") == "running":
                    return "busy", "设备仍在实验、安全处置或冷却中，请等待完成后重新运行安装器"
                live_idle = run["state"] == "idle" and run["run_id"] is None
            elif getattr(cache, "is_fresh", False):
                state = classify_state(getattr(cache, "current_state", None))
                if state == "running":
                    return "busy", "设备仍在运行或冷却中，请等待完成后重新运行安装器"
                live_idle = state == "idle"
        except (HostCommError, V2TransportError, OSError, RuntimeError, ValueError, KeyError, TypeError, TimeoutError):
            live_idle = False
    open_archive = await db.scalar(select(TestSession.test_id).where(TestSession.end_time.is_(None)).limit(1))
    old_operation = await db.scalar(
        select(Operation.operation_id).where(Operation.status.in_(["pending", "sent", "accepted", "unknown"])).limit(1)
    )
    operation = await db.scalar(
        select(V2Operation.operation_id)
        .where(
            V2Operation.status.in_(["pending", "sent", "accepted", "unknown", "result_expired", "not_found"]),
            V2Operation.reconciled == 0,
        )
        .limit(1)
    )
    if open_archive or operation or old_operation:
        return "unknown", "存在未核查实验或操作；安装器需要管理员确认设备已物理停机，原记录将保留待核查"
    if live_idle or unpaired:
        return "idle", "设备已确认待机" if live_idle else "本机尚未配对设备"
    return "unknown", "无法确认设备当前状态；请在安装器中确认设备已物理停机"


class LocalMaintenanceBridge:
    def __init__(self, manager: MaintenanceManager, state_path: Path, db_path: Path, version: str, client, factory):
        self.manager, self.db_path, self.version = manager, db_path.resolve(), version
        self.state_path = state_path
        self.request_path = state_path.parent / "maintenance-request.json"
        self.reply_path = state_path.parent / "maintenance-reply.json"
        self.client, self.factory = client, factory
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None

    async def assess(self) -> tuple[str, str]:
        async with self.factory() as db:
            return await assess_maintenance(self.client, db)

    def _reply(self, request: dict, digest: str, state: str, code: str, message: str, *, gate=None) -> dict:
        result = {
            "schema_version": 1,
            "transaction_id": request["transaction_id"],
            "request_sha256": digest,
            **{key: request[key] for key in ("operation", "current_version", "target_version", "package_sha256")},
            "state": state,
            "reason_code": code,
            "message": message,
            "checked_at": time.time(),
        }
        if gate is not None:
            result["gate"] = gate
        write_protected_json(self.reply_path, result)
        return result

    async def process_once(self) -> dict | None:
        try:
            verify_request_acl(self.request_path)
            request = _read_json(self.request_path)
            MaintenanceIntent.model_validate(request)
            digest = canonical_digest(request)
        except FileNotFoundError:
            return None
        except (ValueError, OSError, ValidationError):
            # Do not echo malformed untrusted data or create a maintenance gate.
            return None
        async with self.manager.installer_guard():
            try:
                previous = _read_json(self.reply_path)
            except FileNotFoundError:
                previous = None
            except (ValueError, OSError):
                return self._reply(request, digest, "rejected", "reply_invalid", "维护回复损坏，请使用安装器恢复")
            try:
                gate = self.manager.upgrade_state()
            except MaintenanceBlockedError:
                return self._reply(request, digest, "blocked", "gate_invalid", "维护记录损坏，请使用安装器恢复")
            if previous and previous.get("transaction_id") == request["transaction_id"]:
                if previous.get("request_sha256") != digest:
                    return self._reply(request, digest, "rejected", "request_conflict", "维护编号已用于其他请求")
                if previous.get("state") == "authorized":
                    if gate == previous.get("gate") and gate.get("request_sha256") == digest:
                        return previous
                    return self._reply(
                        request, digest, "rejected", "authorization_consumed", "该维护请求已完成或被替换"
                    )
                return previous
            if request["current_version"] != self.version:
                return self._reply(
                    request, digest, "rejected", "version_mismatch", "安装器识别的当前版本与运行服务不一致"
                )
            if not -30 <= time.time() - request["created_at"] <= 600:
                return self._reply(request, digest, "rejected", "request_expired", "维护请求已过期，请重新运行安装器")
            if (
                previous is None
                and gate.get("schema_version") == 2
                and gate.get("issuer") == "windows_installer"
                and gate.get("upgrade_id") == request["transaction_id"]
                and gate.get("request_sha256") == digest
                and gate.get("state") == "claimed"
                and gate.get("db_path") == str(self.db_path)
                and all(
                    gate.get(key) == request[key]
                    for key in (
                        "operation",
                        "current_version",
                        "target_version",
                        "package_sha256",
                        "admin_sid",
                        "physical_shutdown_confirmed",
                        "created_at",
                    )
                )
            ):
                # Crash after gate fsync but before reply: device writes have stayed
                # blocked; recreate only the acknowledgement for this exact gate.
                return self._reply(
                    request, digest, "authorized", "maintenance_authorized", "已恢复本次维护回复", gate=gate
                )
            if gate["state"] != "idle":
                return self._reply(request, digest, "blocked", "maintenance_active", "已有维护操作，请由安装器先恢复")
            assessment, message = await self.assess()
            if assessment == "busy":
                return self._reply(request, digest, "blocked", "device_busy", message)
            if assessment != "idle" and not request["physical_shutdown_confirmed"]:
                return self._reply(request, digest, "confirmation_required", "physical_confirmation_required", message)
            gate = {
                "schema_version": 2,
                "state": "claimed",
                "upgrade_id": request["transaction_id"],
                "issuer": "windows_installer",
                "request_sha256": digest,
                **{
                    key: request[key]
                    for key in (
                        "operation",
                        "current_version",
                        "target_version",
                        "package_sha256",
                        "admin_sid",
                        "physical_shutdown_confirmed",
                        "created_at",
                    )
                },
                "db_path": str(self.db_path),
                "claimed_at": time.time(),
                "device_assessment": assessment,
            }
            # Gate is durable before acknowledging permission to stop the service.
            write_protected_json(self.state_path, gate)
            return self._reply(request, digest, "authorized", "maintenance_authorized", message, gate=gate)

    async def start(self) -> None:
        if self._task is not None:
            return

        async def loop() -> None:
            while True:
                try:
                    await self.process_once()
                    self._last_error = None
                except Exception as exc:  # Keep service alive, never grant maintenance on failure.
                    name = type(exc).__name__
                    if self._last_error != name:
                        logger.error("maintenance.intent_failed", error_type=name)
                        self._last_error = name
                await asyncio.sleep(0.25)

        self._task = asyncio.create_task(loop(), name="installer-maintenance-intents")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
