"""报警路由 /api/alarms（规格 3.5）。D3 骨架。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update

from app.api.deps import DbDep, UserDep, get_command_service, get_current_user, get_hostcomm_client
from app.api.operation_api import OPERATION_ERRORS, operation_http_error
from app.api.schemas import err, ok
from app.api.validation import EventLimit, Page, PageSize
from app.api.ws_manager import ws_manager
from app.db.models import AlarmLog
from app.db.v2_models import V2AlarmProjection
from app.hostcomm.protocol import now_iso
from app.services.command_service import audit_action

router = APIRouter(prefix="/api/alarms", tags=["alarms"])


@router.get("/active", dependencies=[Depends(get_current_user)])
async def active_alarms(db: DbDep, limit: EventLimit = 500):
    """当前活跃（未消除）报警。权限：Observer+。"""
    result = await db.execute(
        select(AlarmLog)
        .where(AlarmLog.clear_time.is_(None))
        .order_by(AlarmLog.level.desc(), AlarmLog.id.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return ok(await _rows(db, rows))


@router.get("/history", dependencies=[Depends(get_current_user)])
async def alarm_history(db: DbDep, page: Page = 1, size: PageSize = 50):
    """历史报警（分页）。权限：Observer+。"""
    offset = (page - 1) * size
    result = await db.execute(select(AlarmLog).order_by(AlarmLog.id.desc()).limit(size).offset(offset))
    rows = result.scalars().all()
    return ok(await _rows(db, rows))


@router.post("/{alarm_id}/ack")
async def ack_alarm(alarm_id: int, request: Request, user: UserDep, db: DbDep):
    """确认报警：经 HostComm 发送 ack_alarm 命令 + 写操作日志。权限：Operator+。

    安全：ack_alarm 仅确认显示/锁存状态，不绕过未消除故障（裁决在 STM32）。
    """
    if user.role == "observer":
        raise HTTPException(status_code=403, detail=err("operator_permission_denied", "无操作权限"))
    alarm = await db.get(AlarmLog, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=404, detail=err("not_found", "报警不存在"))
    mapping = await db.scalar(select(V2AlarmProjection).where(V2AlarmProjection.alarm_log_id == alarm_id))
    if mapping is not None:
        return await _ack_v2(alarm, mapping, request, user, db)
    if alarm.ack_time is not None:
        return ok(
            {
                "alarm_id": alarm_id,
                "ack_operator": alarm.ack_operator,
                "ack_time": alarm.ack_time,
                "command": "already_acked",
            }
        )

    # 经 HostComm 发送 ack_alarm（尽力而为：离线时仍记录本地确认）
    client = get_hostcomm_client(request)
    cmd_result, reason = "skipped", None
    if client is not None and getattr(client, "is_online", False):
        try:
            r = await client.send_command(
                "ack_alarm",
                {"alarm_id": alarm_id, "alarm_code": alarm.alarm_code},
                operator_id=user.username,
                role=user.role,
            )
            cmd_result, reason = r.get("result", "error"), r.get("reason_code")
        except Exception:  # noqa: BLE001
            cmd_result, reason = "error", "device_comm_fault"

    # 首次确认采用原子 compare-and-set，避免并发请求覆盖确认人与时间。
    ack_time = now_iso()
    update_result = await db.execute(
        update(AlarmLog)
        .where(AlarmLog.id == alarm_id, AlarmLog.ack_time.is_(None))
        .values(ack_time=ack_time, ack_operator=user.username)
    )
    await db.commit()
    await db.refresh(alarm)
    first_ack = update_result.rowcount == 1
    ack_operator = alarm.ack_operator
    ack_time = alarm.ack_time

    await audit_action(
        db,
        operator_id=user.username,
        role=user.role,
        action_type="ack_alarm",
        params={"alarm_id": alarm_id, "alarm_code": alarm.alarm_code},
        result=cmd_result,
        reason_code=reason,
        client_ip=request.client.host if request.client else None,
    )
    if first_ack:
        await ws_manager.broadcast(
            "alarm_ack",
            {"alarm_id": alarm_id, "ack_operator": ack_operator, "ack_time": ack_time},
        )
    return ok(
        {
            "alarm_id": alarm_id,
            "ack_operator": ack_operator,
            "ack_time": ack_time,
            "command": cmd_result if first_ack else "already_acked",
        }
    )


def _row(r: AlarmLog) -> dict:
    return {
        "id": r.id,
        "alarm_code": r.alarm_code,
        "level": r.level,
        "occur_time": r.occur_time,
        "clear_time": r.clear_time,
        "ack_time": r.ack_time,
        "ack_operator": r.ack_operator,
        "text": r.text,
    }


async def _rows(db, rows):
    ids = [row.id for row in rows]
    bindings = (
        {
            row.alarm_log_id: row
            for row in await db.scalars(select(V2AlarmProjection).where(V2AlarmProjection.alarm_log_id.in_(ids)))
        }
        if ids
        else {}
    )
    result = []
    for row in rows:
        data = _row(row)
        if row.id in bindings:
            binding = bindings[row.id]
            data.update(
                wire_alarm_id=binding.alarm_id,
                occurrence_seq=binding.occurrence_seq,
                device_id=binding.device_id,
                protocol_version="2.0",
            )
        result.append(data)
    return result


async def _ack_v2(alarm, mapping, request, user, db):
    client = get_hostcomm_client(request)
    if not client or getattr(client, "protocol_version", None) != "2.0" or client.device_id != mapping.device_id:
        raise HTTPException(status_code=503, detail=err("v2_device_unavailable", "当前连接与报警来源设备不匹配"))
    if mapping.acknowledged:
        return ok(
            {
                "alarm_id": alarm.id,
                "ack_time": alarm.ack_time,
                "ack_operator": alarm.ack_operator,
                "command": "already_acked",
                "device_confirmed": True,
            }
        )
    try:
        result = await get_command_service(request).execute(
            "ack_alarm",
            {"alarm_id": mapping.alarm_id, "occurrence_seq": mapping.occurrence_seq},
            operator_id=user.username,
            role=user.role,
            db_session=db,
            client_ip=request.client.host if request.client else None,
            operation_id=f"alarm-{alarm.id}-{user.username}",
        )
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc
    if result.get("wire_status") != "applied":
        return ok({"alarm_id": alarm.id, "command": result.get("result"), "device_confirmed": False, **result})
    await db.refresh(alarm)
    alarm.ack_time = alarm.ack_time or now_iso()
    alarm.ack_operator = alarm.ack_operator or user.username
    await db.commit()
    await ws_manager.broadcast(
        "alarm_ack", {"alarm_id": alarm.id, "ack_time": alarm.ack_time, "ack_operator": alarm.ack_operator}
    )
    return ok(
        {
            "alarm_id": alarm.id,
            "ack_time": alarm.ack_time,
            "ack_operator": alarm.ack_operator,
            "command": "accepted",
            "device_confirmed": True,
            **result,
        }
    )
