"""升级准备由 Admin 授权，领取票据仅允许本机受限安装器。"""

import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import __version__
from app.api.deps import DbDep, UserDep, get_hostcomm_client, require_role
from app.api.schemas import ok
from app.core.config import get_settings
from app.db.models import TestSession
from app.services.cache import status_cache
from app.services.maintenance_service import MaintenanceBlockedError, maintenance_manager
from app.services.state_policy import classify_state

router = APIRouter(prefix="/api/system/maintenance", tags=["maintenance"])


class PrepareRequest(BaseModel):
    target_version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


async def validate_upgrade_ready(request: Request, db, *, cache=None) -> None:
    cache = status_cache if cache is None else cache
    client = get_hostcomm_client(request)
    if not (
        client
        and client.is_online
        and getattr(cache, "is_fresh", False)
        and classify_state(getattr(cache, "current_state", None)) == "idle"
    ):
        raise MaintenanceBlockedError("升级要求设备在线、状态新鲜且明确空闲")
    if await db.scalar(select(TestSession.test_id).where(TestSession.end_time.is_(None)).limit(1)):
        raise MaintenanceBlockedError("存在未闭合或待核查实验，禁止升级")


@router.get("", dependencies=[Depends(require_role("admin"))])
async def status():
    state = maintenance_manager.upgrade_state()
    return ok({key: value for key, value in state.items() if key not in {"token", "db_path"}})


@router.post("/prepare", dependencies=[Depends(require_role("admin"))])
async def prepare(body: PrepareRequest, request: Request, user: UserDep, db: DbDep):
    settings = get_settings()
    state = await maintenance_manager.prepare_upgrade(
        target_version=body.target_version,
        current_version=__version__,
        db_path=settings.db_path_resolved,
        operator_id=user.username,
        validate=lambda: validate_upgrade_ready(request, db),
    )
    return ok({key: value for key, value in state.items() if key not in {"token", "db_path"}})


@router.delete("", dependencies=[Depends(require_role("admin"))])
async def cancel():
    await maintenance_manager.cancel_upgrade()
    return ok({"state": "idle"})


@router.post("/claim")
async def claim(request: Request, db: DbDep, x_smd_upgrade_token: Annotated[str, Header(min_length=64, max_length=64)]):
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "维护票据仅允许本机安装器领取")
    state = await maintenance_manager.claim_upgrade(
        x_smd_upgrade_token, validate=lambda: validate_upgrade_ready(request, db)
    )
    return ok({key: value for key, value in state.items() if key != "token"})
