"""持久命令事务：写前提交、稳定身份、重试只读已有结果，绝不自动重发。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.operation_models import Operation
from app.hostcomm.protocol import new_msg_id, now_iso


@dataclass
class OperationError(Exception):
    status_code: int
    error_code: str
    message: str


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise OperationError(422, "invalid_operation_payload", "操作参数必须是有限的JSON值") from exc


def validate_operation_id(value: str | None) -> str:
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise OperationError(422, "invalid_operation_id", "operation_id格式无效")
    return value


async def get_operation(db: AsyncSession, operation_id: str) -> Operation | None:
    return await db.scalar(
        select(Operation).where(Operation.operation_id == operation_id).execution_options(populate_existing=True)
    )


def operation_payload(row: Operation) -> dict:
    """accepted仅代表设备受理；verified仅用于参数回读已完成。"""
    result = json.loads(row.result_json) if row.result_json else {"result": row.status}
    result.update({"operation_id": row.operation_id, "operation_status": row.status, "request_msg_id": row.msg_id})
    if row.reason_code:
        result["reason_code"] = row.reason_code
    if row.device_result_json:
        result["device_result"] = json.loads(row.device_result_json)
    return result


def device_result_status(result: dict) -> str:
    if result.get("result") == "timeout":
        return "unknown"
    return "accepted" if result.get("result") == "accepted" else "rejected"


class OperationExecution:
    def __init__(self, db: AsyncSession, row: Operation):
        self.db = db
        self.operation_id = row.operation_id
        self.msg_id = row.msg_id
        self.stage = row.status

    async def _record(self, status: str, **values) -> None:
        await self.db.execute(
            update(Operation)
            .where(Operation.operation_id == self.operation_id)
            .values(status=status, updated_at=now_iso(), **values)
        )
        await self.db.commit()
        self.stage = status

    async def mark_sent(self) -> None:
        """此提交成功前调用方不得向TCP写入任何命令字节。"""
        await self._record("sent")

    async def record_device_result(self, result: dict) -> None:
        state = device_result_status(result)
        await self._record(state, device_result_json=canonical_json(result), reason_code=result.get("reason_code"))


async def run_operation(
    db: AsyncSession | None,
    *,
    command: str,
    params: dict,
    operator_id: str,
    role: str,
    operation_id: str | None,
    client_ip: str | None,
    perform: Callable[[OperationExecution], Awaitable[dict]],
) -> dict:
    if db is None:
        raise OperationError(503, "database_unavailable", "设备写入前必须持久记录操作")
    identity = validate_operation_id(operation_id)
    request_hash = hashlib.sha256(canonical_json({"command": command, "params": params}).encode()).hexdigest()
    existing = await get_operation(db, identity)
    if existing is None:
        row = Operation(
            operation_id=identity,
            msg_id=new_msg_id("pc-cmd"),
            command=command,
            operator_id=operator_id,
            operator_role=role,
            request_hash=request_hash,
            params_json=canonical_json(params),
            status="pending",
            created_at=now_iso(),
            updated_at=now_iso(),
            client_ip=client_ip,
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await get_operation(db, identity)
            if existing is None:
                raise
    if existing is not None:
        if existing.operator_id != operator_id or existing.request_hash != request_hash:
            raise OperationError(409, "operation_conflict", "该operation_id已用于其他操作者或不同请求")
        return operation_payload(existing)

    execution = OperationExecution(db, row)
    try:
        result = await perform(execution)
        status = "verified" if result.get("readback_ok") else device_result_status(result)
        await execution._record(status, result_json=canonical_json(result), reason_code=result.get("reason_code"))
    except BaseException as exc:
        # rollback恢复可用事务；若数据库本身损坏，已提交的sent仍可在重启时识别unknown。
        try:
            await db.rollback()
            uncertain = execution.stage in {"sent", "accepted", "unknown"}
            reason = getattr(exc, "error_code", None) or ("outcome_unknown" if uncertain else "not_sent")
            await execution._record("unknown" if uncertain else "rejected", reason_code=reason)
        except Exception:
            pass
        try:
            exc.operation_id = identity
        except (AttributeError, TypeError):
            pass
        raise
    stored = await get_operation(db, identity)
    assert stored is not None
    return operation_payload(stored)


async def recover_interrupted_operations(db: AsyncSession) -> int:
    """仅在服务独占网关的启动阶段调用；恢复不会发送设备命令。"""
    result = await db.execute(
        update(Operation)
        .where(Operation.status.in_(["pending", "sent", "accepted"]), Operation.result_json.is_(None))
        .values(status="unknown", reason_code="backend_restart", updated_at=now_iso())
    )
    await db.commit()
    return result.rowcount
