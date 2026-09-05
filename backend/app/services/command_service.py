"""命令服务：权限校验、状态校验、二次确认、命令下发与操作日志。

规格见开发规格说明书第 3.3 / 6.2 / 6.3 节。

安全约束（CLAUDE 第 2 节）：
- 仅转发"请求"到 STM32，最终裁决在控制板与硬接线联锁。
- 不存在任何强制打开 CO 阀 / 绕过安全继电器 / 强制恢复加热许可的命令。
- CO 相关命令（start_test / stop_test）必须携带有效 confirm_token。
- set_parameters 须非运行态、CRC 校验通过后才下发。
- 每次命令结果写入 operator_action（只追加审计）。
"""

from __future__ import annotations

import asyncio
import binascii
import json
import time
import uuid
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.hostcomm.client import HostCommNotConnectedError, HostCommTimeoutError
from app.hostcomm.protocol import now_iso
from app.services import logging_service
from app.services.state_policy import parameter_changes_allowed
from app.services.test_id import InvalidTestIdError, validate_test_id

logger = get_logger("service.command")

# ---- 命令权限矩阵（命令 → 允许角色集合）规格 6.2 -------------------------
_ROLES_OPERATOR_UP = {"operator", "admin", "maintainer"}
_ROLES_ADMIN_UP = {"admin", "maintainer"}

COMMAND_PERMISSIONS: dict[str, set[str]] = {
    "start_test": _ROLES_OPERATOR_UP,
    "stop_test": _ROLES_OPERATOR_UP,
    "pause_hold": _ROLES_OPERATOR_UP,
    "resume_test": _ROLES_OPERATOR_UP,
    "ack_alarm": _ROLES_OPERATOR_UP,
    "reset_fault": _ROLES_OPERATOR_UP,
    "tare_balance": _ROLES_OPERATOR_UP,
    "export_log": _ROLES_OPERATOR_UP,
    "generate_report": _ROLES_OPERATOR_UP,
    "set_parameters": _ROLES_ADMIN_UP,
    "sync_time": _ROLES_ADMIN_UP,
}

# CO 相关命令：必须二次确认（规格 6.3）
CO_COMMANDS = {"start_test", "stop_test"}

# CommandService 按请求构造，因此待启动占位必须是进程级，防止两个并发请求在
# 任一会话落库前同时通过撞号检查并下发到控制板。
_start_reservation_lock = asyncio.Lock()
_pending_start_test_id: str | None = None


@dataclass
class CommandError(Exception):
    """命令被拒绝（映射为 HTTP 4xx）。"""

    status_code: int
    error_code: str
    message: str


@dataclass
class ConfirmTokenStore:
    """二次确认令牌：单次使用、60s 有效（规格 6.3）。"""

    ttl_seconds: float = 60.0
    _tokens: dict[str, float] = field(default_factory=dict)

    def issue(self) -> str:
        token = uuid.uuid4().hex
        self._tokens[token] = time.monotonic() + self.ttl_seconds
        return token

    def consume(self, token: str | None) -> bool:
        if not token:
            return False
        expiry = self._tokens.pop(token, None)
        if expiry is None:
            return False
        return expiry >= time.monotonic()


# 进程级令牌存储单例
confirm_tokens = ConfirmTokenStore()


def check_permission(command: str, role: str) -> None:
    """命令权限校验，不满足抛 403。"""
    allowed = COMMAND_PERMISSIONS.get(command)
    if allowed is None:
        raise CommandError(400, "unknown_command", f"未知命令: {command}")
    if role not in allowed:
        raise CommandError(403, "operator_permission_denied", f"角色 {role} 无权执行 {command}")


def check_confirm_token(command: str, token: str | None) -> None:
    """CO 相关命令的二次确认校验，无效令牌抛 400/403。"""
    if command not in CO_COMMANDS:
        return
    if not confirm_tokens.consume(token):
        raise CommandError(400, "confirm_token_required", f"{command} 需要有效的二次确认令牌")


def check_state(command: str, current_state: str | None) -> None:
    """状态限制校验。set_parameters 仅在明确非运行态放行（T09）。"""
    if command == "set_parameters" and not parameter_changes_allowed(current_state):
        raise CommandError(400, "state_not_allowed", f"当前状态({current_state or 'unknown'})不允许下发参数")


def check_parameter_crc(command: str, params: dict) -> None:
    """set_parameters 的 CRC 校验（T12）。

    约定：params 含 ``values``（参数本体）与 ``param_crc``（期望 CRC，十六进制）。
    计算 values 的 CRC32 与期望比对，不一致则拒绝且不下发。
    """
    if command != "set_parameters":
        return
    expected = params.get("param_crc")
    if expected is None:
        raise CommandError(400, "parameter_crc_error", "缺少 param_crc")
    values = params.get("values", {})
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    computed = format(binascii.crc32(raw.encode("utf-8")) & 0xFFFFFFFF, "08x")
    if str(expected).lower().removeprefix("0x") != computed:
        raise CommandError(400, "parameter_crc_error", "参数 CRC 校验失败，已拒绝下发")


def compute_param_crc(values: dict) -> str:
    """供前端/测试构造合法 param_crc 用。"""
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return format(binascii.crc32(raw.encode("utf-8")) & 0xFFFFFFFF, "08x")


async def audit_action(
    db_session,
    *,
    operator_id: str,
    role: str,
    action_type: str,
    params: dict | None,
    result: str,
    reason_code: str | None = None,
    test_id: str | None = None,
    client_ip: str | None = None,
) -> None:
    """向 operator_action 追加一条审计记录（只追加）。供命令与参数下发共用。"""
    if db_session is None:
        return
    from app.db.models import OperatorAction

    db_session.add(
        OperatorAction(
            ts=now_iso(),
            operator_id=operator_id,
            operator_role=role,
            action_type=action_type,
            test_id=test_id or (params.get("test_id") if isinstance(params, dict) else None),
            params_json=json.dumps(params, ensure_ascii=False) if params is not None else None,
            result=result,
            reason_code=reason_code,
            client_ip=client_ip,
        )
    )
    await db_session.commit()


class CommandService:
    """编排一次命令下发的完整流程。"""

    def __init__(self, hostcomm_client, status_cache) -> None:
        self._client = hostcomm_client
        self._cache = status_cache

    async def execute(
        self,
        command: str,
        params: dict | None,
        *,
        operator_id: str,
        role: str,
        confirm_token: str | None = None,
        client_ip: str | None = None,
        db_session=None,
    ) -> dict:
        """执行命令并返回 command_result payload。校验失败抛 CommandError。"""
        params = params or {}

        # 1. 权限校验
        check_permission(command, role)
        # 2. 状态校验
        current_state = self._cache.get_field("system.current_state")
        check_state(command, current_state)
        reserved_test_id = await self._reserve_start(command, params, db_session)
        try:
            # 3. CO 命令二次确认
            check_confirm_token(command, confirm_token)
            # 4. set_parameters CRC 校验
            check_parameter_crc(command, params)

            result_payload = await self._send_with_audit(
                command,
                params,
                operator_id=operator_id,
                role=role,
                confirm_token=confirm_token,
                client_ip=client_ip,
                db_session=db_session,
            )

            # 命令被控制板受理后，处理试验会话生命周期（建/收会话）
            if result_payload.get("result") == "accepted":
                await self._handle_lifecycle(
                    db_session,
                    command,
                    params,
                    operator_id,
                    role,
                    client_ip,
                    result_payload,
                )
            return result_payload
        finally:
            if reserved_test_id is not None:
                await self._release_start(reserved_test_id)

    async def _reserve_start(self, command: str, params: dict, db_session) -> str | None:
        if command != "start_test":
            return None
        raw_test_id = params.get("test_id")
        if raw_test_id is None or (isinstance(raw_test_id, str) and not raw_test_id.strip()):
            raise CommandError(400, "test_id_required", "启动试验必须提供试验编号")
        try:
            test_id = validate_test_id(raw_test_id)
        except InvalidTestIdError as exc:
            raise CommandError(400, "invalid_test_id", str(exc)) from exc
        if db_session is None:
            raise CommandError(503, "database_unavailable", "无法校验试验编号")

        global _pending_start_test_id
        async with _start_reservation_lock:
            if _pending_start_test_id is not None:
                raise CommandError(409, "test_start_in_progress", "另一个试验启动请求正在处理")

            from sqlalchemy import select

            from app.db.models import TestSession

            exists = await db_session.scalar(select(TestSession.id).where(TestSession.test_id == test_id))
            if exists is not None:
                raise CommandError(409, "test_id_exists", f"试验编号已存在: {test_id}")
            open_session = await db_session.scalar(
                select(TestSession.test_id).where(TestSession.end_time.is_(None)).order_by(TestSession.id.desc())
            )
            if open_session is not None:
                raise CommandError(409, "test_session_active", f"试验 {open_session} 尚未闭合")

            params["test_id"] = test_id
            _pending_start_test_id = test_id
            return test_id

    async def _release_start(self, test_id: str) -> None:
        global _pending_start_test_id
        async with _start_reservation_lock:
            if _pending_start_test_id == test_id:
                _pending_start_test_id = None

    async def _send_with_audit(
        self,
        command: str,
        params: dict,
        *,
        operator_id: str,
        role: str,
        confirm_token: str | None,
        client_ip: str | None,
        db_session,
    ) -> dict:
        result_label = "error"
        reason_code = None
        try:
            if self._client is None or not getattr(self._client, "is_online", False):
                raise CommandError(503, "device_comm_fault", "HostComm 未连接")
            device_params = {"test_id": params["test_id"]} if command == "start_test" else params
            result_payload = await self._client.send_command(
                command, device_params, operator_id=operator_id, role=role, confirm_token=confirm_token
            )
            result_label = result_payload.get("result", "error")
            reason_code = result_payload.get("reason_code")
            return result_payload
        except HostCommTimeoutError:
            reason_code = "device_comm_timeout"
            raise
        except HostCommNotConnectedError:
            reason_code = "device_comm_fault"
            raise
        finally:
            await self._write_audit(
                db_session,
                command=command,
                params=params,
                operator_id=operator_id,
                role=role,
                result=result_label,
                reason_code=reason_code,
                client_ip=client_ip,
            )

    async def _handle_lifecycle(
        self,
        db_session,
        command: str,
        params: dict,
        operator_id: str,
        role: str,
        client_ip: str | None,
        result_payload: dict,
    ) -> None:
        """start_test → 建 test_session 并标记进行中；stop_test → 收尾。"""
        if db_session is None:
            return
        from sqlalchemy import select

        from app.db.models import TestSession
        from app.services.test_runtime import active_test

        if command == "start_test":
            test_id = params.get("test_id")
            if not test_id:
                return
            db_session.add(
                TestSession(
                    test_id=test_id,
                    operator_id=operator_id,
                    start_time=now_iso(),
                    original_height_mm=params.get("original_height_mm"),
                    sample_label=params.get("sample_label") or None,
                    notes=params.get("notes") or None,
                )
            )
            await db_session.commit()
            active_test.start(test_id)
            await self._capture_start_parameters(
                db_session,
                test_id=test_id,
                operator_id=operator_id,
                role=role,
                client_ip=client_ip,
            )

        elif command == "stop_test":
            test_id = active_test.active_test_id
            row = None
            if test_id:
                row = await db_session.scalar(select(TestSession).where(TestSession.test_id == test_id))
            if row is None or row.end_time is not None:
                row = await db_session.scalar(
                    select(TestSession).where(TestSession.end_time.is_(None)).order_by(TestSession.id.desc())
                )
            if row is not None and row.end_time is None:
                row.stop_requested_at = now_iso()
                row.phase = "stopping"
                await db_session.commit()
                active_test.restore(row.test_id, needs_device_reconcile=True)

    async def _capture_start_parameters(
        self,
        db_session,
        *,
        test_id: str,
        operator_id: str,
        role: str,
        client_ip: str | None,
    ) -> None:
        """试验已实际启动后立即归档参数；失败只审计，不伪装成启动失败。"""
        reason_code = "parameter_snapshot_failed"
        try:
            readback = await self._client.get_parameters()
            await logging_service.append_parameter_snapshot(
                db_session,
                readback,
                test_id=test_id,
                operator_id=operator_id,
                source="test_start",
            )
            return
        except HostCommTimeoutError:
            reason_code = "device_comm_timeout"
        except HostCommNotConnectedError:
            reason_code = "device_comm_fault"
        except Exception as exc:  # noqa: BLE001
            logger.warning("parameter_snapshot.capture_failed", test_id=test_id, error=str(exc))

        try:
            await db_session.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.warning("parameter_snapshot.rollback_failed", test_id=test_id, error=str(exc))
            return
        try:
            await audit_action(
                db_session,
                operator_id=operator_id,
                role=role,
                action_type="capture_start_parameters",
                params={"test_id": test_id},
                result="error",
                reason_code=reason_code,
                test_id=test_id,
                client_ip=client_ip,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("parameter_snapshot.audit_failed", test_id=test_id, error=str(exc))

    async def _write_audit(
        self,
        db_session,
        *,
        command: str,
        params: dict,
        operator_id: str,
        role: str,
        result: str,
        reason_code: str | None,
        client_ip: str | None,
    ) -> None:
        """写入 operator_action（只追加）。T10。"""
        await audit_action(
            db_session,
            operator_id=operator_id,
            role=role,
            action_type=command,
            params=params,
            result=result,
            reason_code=reason_code,
            client_ip=client_ip,
        )
