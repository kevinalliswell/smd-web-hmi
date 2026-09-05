"""共享后台控制权管理。"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import DbDep, UserDep, require_role
from app.api.routes.maintenance import validate_upgrade_ready
from app.api.schemas import ok
from app.services.control_ownership import change_owner, ownership_snapshot
from app.services.maintenance_service import maintenance_manager

router = APIRouter(prefix="/api/control", tags=["control"], dependencies=[Depends(require_role("operator"))])


class ClaimRequest(BaseModel):
    takeover: bool = False
    reason: str = Field(default="", max_length=1000)


@router.get("")
async def status(db: DbDep):
    return ok(await ownership_snapshot(db))


@router.post("/claim")
async def claim(body: ClaimRequest, user: UserDep, db: DbDep):
    async with maintenance_manager.command_guard():
        return ok(await change_owner(db, user.username, user.role, takeover=body.takeover, reason=body.reason))


@router.delete("")
async def release(request: Request, user: UserDep, db: DbDep):
    async with maintenance_manager.command_guard():
        await validate_upgrade_ready(request, db)
        return ok(await change_owner(db, user.username, user.role, release=True, reason="空闲时释放控制权"))
