"""参数下发服务：set_parameters 完整链路（规格 3.6 / 安全红线 8）。

流程：非运行态校验 → 请求体 CRC 校验 → 下发 set_parameters → 等待受理 →
回读确认（get_parameters 比对）→ 写 parameter_snapshot → 全程 operator_action 审计。

安全约束：
- 必须在非运行状态下发送（运行态拒绝）。
- 发送前校验 CRC，发送后回读确认，整个流程记录入 operator_action。
- 不存在任何绕过校验/强制下发的旁路。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.hostcomm.client import HostCommNotConnectedError, HostCommTimeoutError
from app.services import logging_service
from app.services.command_service import CommandError, audit_action, check_parameter_crc, check_permission, check_state
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
    ) -> dict[str, Any]:
        """下发参数并回读确认。任一步失败抛 CommandError，并写审计。"""
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

        # 4. 下发命令
        try:
            result = await self._client.send_command(
                "set_parameters",
                {"values": values, "param_crc": param_crc},
                operator_id=operator_id,
                role=role,
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
