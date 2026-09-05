"""控制权在维护/命令锁内校验；停机请求不被控制权阻止。"""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.control_models import ControlOwnership
from app.db.models import OperatorAction
from app.hostcomm.protocol import now_iso
from app.services.operations import OperationError


async def ownership_snapshot(db: AsyncSession) -> dict:
    row = await db.get(ControlOwnership, 1, populate_existing=True)
    return {"username": row.username if row else None, "changed_at": row.changed_at if row else None}


async def change_owner(
    db: AsyncSession, username: str, role: str, *, takeover: bool = False, reason: str = "", release: bool = False
) -> dict:
    """调用方必须持有维护 command_guard；release 的空闲校验由路由完成。"""
    if role not in {"operator", "admin", "maintainer"}:
        raise OperationError(403, "operator_permission_denied", "需要操作权限")
    row = await db.get(ControlOwnership, 1, populate_existing=True)
    previous = row.username if row else None
    if previous == username and not release:
        return await ownership_snapshot(db)
    if previous and previous != username:
        if not takeover or role not in {"admin", "maintainer"} or not reason.strip():
            raise OperationError(409, "control_owned", f"控制权由 {previous} 持有；管理员可注明原因接管")
    if row is None:
        row = ControlOwnership(id=1, changed_at=now_iso())
        db.add(row)
    row.username = None if release else username
    row.changed_at = now_iso()
    db.add(
        OperatorAction(
            ts=now_iso(),
            operator_id=username,
            operator_role=role,
            action_type="control_release" if release else "control_transfer",
            params_json=json.dumps({"from": previous, "to": row.username, "reason": reason}, ensure_ascii=False),
            result="verified",
        )
    )
    await db.commit()
    return await ownership_snapshot(db)


async def assert_control_owner(db: AsyncSession, username: str, role: str, action: str) -> None:
    if action == "stop_test":
        return  # 仍受 CommandService 权限、状态及二次确认审计约束。
    await change_owner(db, username, role, reason="首次有效控制请求自动取得控制权")
