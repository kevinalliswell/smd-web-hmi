"""参数下发服务：set_parameters 完整链路（规格 3.6 / 安全红线 8）。

流程：非运行态校验 → 请求体 CRC 校验 → 下发 set_parameters → 等待受理 →
回读确认（get_parameters 比对）→ 写 parameter_snapshot → 全程 operator_action 审计。

安全约束：
- 必须在非运行状态下发送（运行态拒绝）。
- 发送前校验 CRC，发送后回读确认，整个流程记录入 operator_action。
- 不存在任何绕过校验/强制下发的旁路。
"""

from __future__ import annotations

import copy
import json
from typing import Any

from app.core.logging import get_logger
from app.hostcomm.client import HostCommNotConnectedError, HostCommTimeoutError
from app.services import logging_service
from app.services.command_service import (
    CommandError,
    audit_action,
    check_parameter_crc,
    check_permission,
    check_state,
    compute_param_crc,
)
from app.services.control_ownership import assert_control_owner
from app.services.maintenance_service import maintenance_manager
from app.services.operations import OperationExecution, run_operation
from app.services.test_runtime import active_test

logger = get_logger("service.parameter")


def _normalize(values: dict[str, Any]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _values_equal(sent: dict[str, Any], readback: dict[str, Any]) -> bool:
    """下发值与回读值的顺序无关深比对。"""
    return _normalize(sent) == _normalize(readback)


class ParameterService:
    """编排一次参数下发的完整链路。"""

    def __init__(self, hostcomm_client, status_cache) -> None:
        self._client = hostcomm_client
        self._cache = status_cache

    async def get_parameters(self) -> dict[str, Any]:
        """读取当前参数快照（发 get_parameters）。"""
        if self._client is None or not getattr(self._client, "is_online", False):
            raise CommandError(503, "device_comm_fault", "HostComm 未连接")
        return await self._client.get_parameters()

    async def set_parameters(
        self,
        values: dict[str, Any],
        param_crc: str | None,
        *,
        operator_id: str,
        role: str,
        client_ip: str | None = None,
        db_session=None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """下发参数并回读确认。任一步失败抛 CommandError，并写审计。"""
        check_permission("set_parameters", role)
        values = copy.deepcopy(values)

        async def perform(operation: OperationExecution) -> dict:
            return await self._set_parameters(
                values,
                param_crc,
                operator_id=operator_id,
                role=role,
                client_ip=client_ip,
                db_session=db_session,
                operation=operation,
            )

        async with maintenance_manager.command_guard():
            return await run_operation(
                db_session,
                command="set_parameters",
                params={"values": values, "param_crc": param_crc},
                operator_id=operator_id,
                role=role,
                operation_id=operation_id,
                client_ip=client_ip,
                perform=perform,
            )

    async def patch_parameters(
        self,
        patch: dict[str, Any],
        *,
        operator_id: str,
        role: str,
        client_ip: str | None = None,
        db_session=None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """同一维护锁内读取、替换顶层字段、下发和回读；重试按原patch查重。

        配方服务可传入完整recipe字段而保留其他参数。调用者不得预先持有command_guard。
        """
        check_permission("set_parameters", role)
        patch = copy.deepcopy(patch)

        async def perform(operation: OperationExecution) -> dict:
            check_state("set_parameters", self._cache.current_state)
            snapshot = await self.get_parameters()
            current = snapshot.get("params", snapshot.get("values"))
            if not isinstance(current, dict):
                raise CommandError(502, "invalid_parameter_snapshot", "参数快照缺少有效参数对象")
            values = {**copy.deepcopy(current), **patch}
            return await self._set_parameters(
                values,
                compute_param_crc(values),
                operator_id=operator_id,
                role=role,
                client_ip=client_ip,
                db_session=db_session,
                operation=operation,
                current_snapshot=snapshot,
            )

        async with maintenance_manager.command_guard():
            return await run_operation(
                db_session,
                command="set_parameters",
                params={"patch": patch},
                operator_id=operator_id,
                role=role,
                operation_id=operation_id,
                client_ip=client_ip,
                perform=perform,
            )

    async def _set_parameters(
        self,
        values: dict[str, Any],
        param_crc: str | None,
        *,
        operator_id: str,
        role: str,
        client_ip: str | None,
        db_session,
        operation: OperationExecution,
        current_snapshot: dict | None = None,
    ) -> dict[str, Any]:
        # 1. 非运行态校验（安全红线 8）
        check_permission("set_parameters", role)
        current_state = self._cache.current_state
        check_state("set_parameters", current_state)

        # 2. 请求体 CRC 完整性校验（前端→后端传输完整性）
        check_parameter_crc("set_parameters", {"values": values, "param_crc": param_crc})

        # 3. 连接校验
        if self._client is None or not getattr(self._client, "is_online", False):
            await audit_action(
                db_session,
                operator_id=operator_id,
                role=role,
                action_type="set_parameters",
                params={"values": values},
                result="error",
                reason_code="device_comm_fault",
                client_ip=client_ip,
            )
            raise CommandError(503, "device_comm_fault", "HostComm 未连接")

        current_snapshot = await self._validate_recipe_write(values, db_session, current_snapshot)
        await assert_control_owner(db_session, operator_id, role, "set_parameters")

        # 4. 下发命令
        try:
            check_state("set_parameters", self._cache.current_state)
            await operation.mark_sent()
            result = await self._client.send_command(
                "set_parameters",
                {"values": values, "param_crc": param_crc},
                operator_id=operator_id,
                role=role,
                msg_id=operation.msg_id,
            )
        except (HostCommTimeoutError, HostCommNotConnectedError) as exc:
            await self._audit_comm_failure(
                exc,
                values=values,
                operator_id=operator_id,
                role=role,
                client_ip=client_ip,
                db_session=db_session,
            )
            raise
        await operation.record_device_result(result)
        result_label = result.get("result", "error")
        reason_code = result.get("reason_code")

        await audit_action(
            db_session,
            operator_id=operator_id,
            role=role,
            action_type="set_parameters",
            params={"values": values},
            result=result_label,
            reason_code=reason_code,
            client_ip=client_ip,
        )

        if result_label != "accepted":
            raise CommandError(
                400,
                reason_code or "rejected",
                result.get("reason_text") or "控制板拒绝参数下发",
            )

        # 5. 回读确认
        try:
            readback = await self._client.get_parameters()
        except (HostCommTimeoutError, HostCommNotConnectedError) as exc:
            await self._audit_comm_failure(
                exc,
                values=values,
                operator_id=operator_id,
                role=role,
                client_ip=client_ip,
                db_session=db_session,
            )
            raise
        if current_snapshot is not None and current_snapshot.get("safety_profile") != readback.get("safety_profile"):
            raise CommandError(409, "safety_profile_changed", "参数验证期间设备安全配置变化，必须重新核对")
        rb_values = readback.get("params") or readback.get("values") or {}
        if not _values_equal(values, rb_values):
            await audit_action(
                db_session,
                operator_id=operator_id,
                role=role,
                action_type="set_parameters",
                params={"values": values},
                result="error",
                reason_code="parameter_readback_mismatch",
                client_ip=client_ip,
            )
            raise CommandError(409, "parameter_readback_mismatch", "参数回读与下发不一致，已中止")

        # 6. 写参数快照（只追加）
        if db_session is not None:
            await logging_service.append_parameter_snapshot(
                db_session,
                readback,
                test_id=active_test.active_test_id,
                operator_id=operator_id,
                source="set_by_hmi",
            )

        return {
            "result": "accepted",
            "readback_ok": True,
            "parameter_crc": readback.get("parameter_crc"),
            "params": rb_values,
        }

    async def _validate_recipe_write(self, values: dict, db_session, snapshot: dict | None) -> dict | None:
        from app.services.recipe_service import RecipeService

        if "safety_profile" in values:
            raise CommandError(403, "safety_profile_read_only", "板端安全配置只读，禁止通过参数入口修改")
        caps = list(getattr(self._client, "capabilities", []))
        if "recipe" in values and "recipe_v1" not in caps:
            raise CommandError(409, "device_missing_recipe_v1", "设备未声明配方执行能力")
        if "recipe" not in values and "recipe_v1" not in caps:
            return None
        snapshot = snapshot if snapshot is not None else await self.get_parameters()
        current = snapshot.get("params", snapshot.get("values"))
        if not isinstance(current, dict):
            raise CommandError(502, "invalid_parameter_snapshot", "设备参数快照无效")
        if "recipe" in current and "recipe" not in values:
            raise CommandError(409, "recipe_required", "参数更新必须保留并验证当前配方")
        if "recipe" in values:
            await RecipeService(db_session, self._client, self._cache).validate_bundle(values["recipe"], snapshot)
        return snapshot

    async def _audit_comm_failure(
        self,
        exc: HostCommTimeoutError | HostCommNotConnectedError,
        *,
        values: dict[str, Any],
        operator_id: str,
        role: str,
        client_ip: str | None,
        db_session,
    ) -> None:
        reason_code = "device_comm_timeout" if isinstance(exc, HostCommTimeoutError) else "device_comm_fault"
        await audit_action(
            db_session,
            operator_id=operator_id,
            role=role,
            action_type="set_parameters",
            params={"values": values},
            result="error",
            reason_code=reason_code,
            client_ip=client_ip,
        )
