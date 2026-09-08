"""Authenticated review and replay of durable device runs with unknown local ownership."""

from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import UserDep, get_current_user, require_role
from app.api.schemas import err, ok
from app.api.validation import TestId
from app.services.maintenance_service import maintenance_manager
from app.services.v2_run_recovery import V2RecoveryError

router = APIRouter(prefix="/api/run-recoveries", tags=["run recovery"], dependencies=[Depends(get_current_user)])


class RecoveryReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=2, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_review_revision: int = Field(ge=1, strict=True)


class RecoveryBindingRequest(RecoveryReviewRequest):
    target_test_id: TestId | None = None


class RecoverySummary(BaseModel):
    id: str = Field(json_schema_extra={"readOnly": True})
    device_id: str = Field(json_schema_extra={"readOnly": True})
    run_id: str = Field(json_schema_extra={"readOnly": True})
    first_seen_at: str
    last_seen_at: str
    review_revision: int
    review_state: Literal["unreviewed", "bound", "conflict"]
    test_id: str | None
    replay_status: Literal["not_bound", "pending", "running", "complete", "failed"]
    replay_through_id: int
    replay_error: str | None
    state: str | None
    evidence: dict[str, Any]


class RecoveryLogGap(BaseModel):
    log_id: str
    first_record_seq: str
    last_record_seq: str
    reason: str


class RecoveryAudit(BaseModel):
    actor: str
    role: str
    reason: str
    action: str
    created_at: str
    evidence: dict[str, Any]


class RecoveryDetail(RecoverySummary):
    start_time: str | None
    discovered_at: str | None
    source_count: int
    log_gaps: list[RecoveryLogGap]
    reviews: list[RecoveryAudit]


ResponseData = TypeVar("ResponseData")


class RecoveryResponse(BaseModel, Generic[ResponseData]):
    data: ResponseData
    ts: str


def _worker(request):
    worker = getattr(request.app.state, "run_recovery_worker", None)
    if worker is None:
        raise HTTPException(503, err("recovery_unavailable", "运行恢复服务尚未初始化"))
    return worker


async def _call(awaitable):
    try:
        return await awaitable
    except V2RecoveryError as exc:
        raise HTTPException(exc.status_code, err(exc.code, exc.message)) from exc


@router.get("", response_model=RecoveryResponse[list[RecoverySummary]])
async def list_recoveries(
    request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0
):
    return ok(await _call(_worker(request).service.list(limit=limit, offset=offset)))


@router.get("/{recovery_id}", response_model=RecoveryResponse[RecoveryDetail])
async def recovery_detail(recovery_id: str, request: Request):
    return ok(await _call(_worker(request).service.detail(recovery_id)))


@router.post(
    "/{recovery_id}/binding",
    dependencies=[Depends(require_role("admin"))],
    response_model=RecoveryResponse[RecoverySummary],
)
async def bind_recovery(recovery_id: str, body: RecoveryBindingRequest, request: Request, user: UserDep):
    async with maintenance_manager.command_guard():
        worker = _worker(request)
        result = await _call(worker.service.bind(recovery_id, actor=user.username, role=user.role, **body.model_dump()))
        worker.wake.set()
        return ok(result)


@router.post(
    "/{recovery_id}/replays",
    dependencies=[Depends(require_role("admin"))],
    response_model=RecoveryResponse[RecoverySummary],
)
async def replay_recovery(recovery_id: str, body: RecoveryReviewRequest, request: Request, user: UserDep):
    async with maintenance_manager.command_guard():
        worker = _worker(request)
        result = await _call(
            worker.service.request_replay(recovery_id, actor=user.username, role=user.role, **body.model_dump())
        )
        worker.wake.set()
        return ok(result)
