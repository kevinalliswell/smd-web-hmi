"""Maintenance status and source recovery; installation is owned by the Windows installer."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import __version__
from app.api.deps import get_hostcomm_client, require_role
from app.api.schemas import ok
from app.db.models import TestSession
from app.db.v2_models import V2Operation
from app.services.background_jobs import BackgroundJobCapacityError, background_jobs
from app.services.cache import status_cache
from app.services.maintenance_service import MaintenanceBlockedError, maintenance_manager
from app.services.state_policy import classify_state

router = APIRouter(prefix="/api/system/maintenance", tags=["maintenance"])


class RecoverSourceLogsRequest(BaseModel):
    first_record_seq: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]{0,19})$")


@router.post("/source-logs", dependencies=[Depends(require_role("admin"))])
async def recover_source_logs(body: RecoverSourceLogsRequest, request: Request):
    client = get_hostcomm_client(request)
    if not client or getattr(client, "protocol_version", None) != "2.0" or not client.is_online:
        raise HTTPException(503, "当前未连接 HostComm 2 设备")
    if body.first_record_seq is not None and int(body.first_record_seq) > 2**64 - 1:
        raise HTTPException(422, "源序号超出 uint64 范围")

    async def recover():
        await client.recover_logs(first_record_seq=body.first_record_seq)
        return {"device_id": client.device_id, "status": "source_scan_completed"}

    try:
        task_id = background_jobs.submit("v2_source_recovery", recover)
    except BackgroundJobCapacityError as exc:
        raise HTTPException(503, "后台任务容量已满") from exc
    return ok({"task_id": task_id, "status": "pending"})


@router.get("/source-logs/{task_id}", dependencies=[Depends(require_role("admin"))])
async def source_recovery_status(task_id: str):
    snapshot = background_jobs.snapshot(task_id)
    if snapshot is None or snapshot["kind"] != "v2_source_recovery":
        raise HTTPException(404, "源日志任务不存在")
    return ok(snapshot)


async def validate_upgrade_ready(request: Request, db, *, cache=None) -> None:
    cache = status_cache if cache is None else cache
    client = get_hostcomm_client(request)
    if await db.scalar(select(TestSession.test_id).where(TestSession.end_time.is_(None)).limit(1)):
        raise MaintenanceBlockedError("存在未闭合或待核查实验，禁止升级")
    if client and getattr(client, "protocol_version", None) == "2.0":
        if await db.scalar(
            select(V2Operation.operation_id)
            .where(
                V2Operation.status.in_(["pending", "sent", "accepted", "unknown", "result_expired", "not_found"]),
                V2Operation.reconciled == 0,
            )
            .limit(1)
        ):
            raise MaintenanceBlockedError("存在未核查的设备操作，禁止升级")
        if not client.device_id:
            return  # Explicitly unpaired new installation cannot control a board.
        if not client.is_online:
            raise MaintenanceBlockedError("已配对设备须在线确认待机后升级")
        await client.get_status()
        run = client._status_frame["payload"]["run"]
        if run["state"] != "idle" or run["run_id"] is not None:
            raise MaintenanceBlockedError("升级要求设备已确认结束并回到待机")
        return
    if not (
        client
        and client.is_online
        and getattr(cache, "is_fresh", False)
        and classify_state(getattr(cache, "current_state", None)) == "idle"
    ):
        raise MaintenanceBlockedError("升级要求设备在线、状态新鲜且明确空闲")


@router.get("", dependencies=[Depends(require_role("admin"))])
async def status():
    state = maintenance_manager.upgrade_state()
    # Explicit allowlist: request hashes, administrator identity and local paths
    # belong to the installer's local audit, not the browser API.
    public = {key: state[key] for key in ("state", "target_version", "upgrade_id", "operation") if key in state}
    return ok({**public, "current_version": __version__})


_INSTALLER_GUIDANCE = (
    "此维护入口已停用。请直接运行新版 Windows 安装器；安装器自动识别版本并保留配置和数据库，无需填写版本号。"
)


@router.post("/prepare", dependencies=[Depends(require_role("admin"))], deprecated=True)
async def prepare():
    raise HTTPException(410, _INSTALLER_GUIDANCE)


@router.delete("", dependencies=[Depends(require_role("admin"))], deprecated=True)
async def cancel():
    raise HTTPException(410, _INSTALLER_GUIDANCE)


@router.post("/claim", deprecated=True)
async def claim():
    raise HTTPException(410, _INSTALLER_GUIDANCE)
