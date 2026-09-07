"""实验条件补录与未闭合会话核查，所有修订都保留操作者和前值。"""

from __future__ import annotations

import json
import math

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update

from app.api.deps import DbDep, UserDep, get_hostcomm_client, require_role
from app.api.schemas import err, ok
from app.api.validation import TestIdPath
from app.db.cancellation import finish_db_work
from app.db.models import EventLog, TestSession
from app.db.v2_models import V2RunBinding
from app.hostcomm.protocol import now_iso
from app.services.cache import status_cache
from app.services.experiment_metadata import ReportContext, SpecimenMetadata
from app.services.maintenance_service import maintenance_manager
from app.services.state_policy import classify_state
from app.services.test_runtime import active_test

router = APIRouter(prefix="/api/tests", tags=["experiment review"])


class MetadataRequest(BaseModel):
    sample_metadata: SpecimenMetadata | None = None
    report_context: ReportContext | None = None
    reason: str = Field(min_length=2, max_length=1000)


async def _get_test(db, test_id):
    row = await db.scalar(
        select(TestSession).where(TestSession.test_id == test_id).execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, err("test_not_found", "实验不存在"))
    return row


def _basis(row):
    try:
        value = json.loads(row.measurement_basis_json or "{}")
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise HTTPException(409, err("basis_invalid", "原记录结构损坏，请从备份核查，禁止覆盖"))
    if any(key in value and not isinstance(value[key], dict) for key in ("sample_metadata", "report_context")):
        raise HTTPException(409, err("basis_invalid", "旧条件记录结构损坏，请先核查备份"))
    revision = value.get("metadata_revision", 0)
    if type(revision) is not int or revision < 0:
        raise HTTPException(409, err("basis_invalid", "旧条件记录版本损坏，请先核查备份"))
    return value


@router.patch("/{test_id}/metadata", dependencies=[Depends(require_role("operator"))])
async def update_metadata(test_id: TestIdPath, body: MetadataRequest, user: UserDep, db: DbDep):
    async with maintenance_manager.command_guard():
        return await _commit_metadata(test_id, body, user, db)


@finish_db_work
async def _commit_metadata(test_id, body, user, db):
    try:
        # Reserve the same SQLite writer used by source projection before reading
        # the whole basis JSON; cached evidence must not replace a newer archive.
        await db.execute(
            update(TestSession)
            .where(TestSession.test_id == test_id)
            .values(measurement_basis_json=TestSession.measurement_basis_json)
            .execution_options(synchronize_session=False)
        )
        row = await _get_test(db, test_id)
        basis = _basis(row)
        before = row.measurement_basis_json
        old_height = row.original_height_mm
        for key in ("sample_metadata", "report_context"):
            value = getattr(body, key)
            if value is not None:
                incoming = value.model_dump(exclude_none=True, mode="json")
                basis[key] = {**basis.get(key, {}), **incoming}
        try:
            specimen = SpecimenMetadata.model_validate(basis.get("sample_metadata", {}))
            ReportContext.model_validate(basis.get("report_context", {}))
        except ValidationError as exc:
            raise HTTPException(422, err("metadata_conflict", "合并后的实验条件不一致或数值无效")) from exc
        height = specimen.original_height
        if height is not None:
            if old_height is not None and not math.isclose(height, old_height, abs_tol=0.001):
                raise HTTPException(409, err("height_conflict", "H1-H2与已记录原始高度不一致，不能静默替换"))
            row.original_height_mm = height
        basis["metadata_revision"] = basis.get("metadata_revision", 0) + 1
        row.measurement_basis_json = json.dumps(basis, ensure_ascii=False)
        db.add(
            EventLog(
                test_id=test_id,
                ts=now_iso(),
                source="hmi_review",
                event_code="METADATA_REVISED",
                operator_id=user.username,
                level=0,
                text=body.reason,
                detail_json=json.dumps(
                    {"before": before, "after": basis, "old_height": old_height, "new_height": row.original_height_mm},
                    ensure_ascii=False,
                ),
            )
        )
        await db.commit()
        return ok({"test_id": test_id, "original_height_mm": row.original_height_mm, "measurement_basis": basis})
    except BaseException:
        await db.rollback()
        raise


class CloseReviewRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)
    physical_safety_confirmed: bool


@router.post("/{test_id}/review-close", dependencies=[Depends(require_role("admin"))])
async def close_review(test_id: TestIdPath, body: CloseReviewRequest, request: Request, user: UserDep, db: DbDep):
    async with maintenance_manager.command_guard():
        row = await _get_test(db, test_id)
        if row.end_time is not None:
            return ok({"test_id": test_id, "phase": row.phase, "data_integrity": row.data_integrity})
        basis = _basis(row)
        binding = await db.scalar(select(V2RunBinding).where(V2RunBinding.test_id == test_id))
        if binding is not None or "v2" in basis or "recovery" in basis:
            # A manual checkbox or an unrelated current idle snapshot cannot establish
            # the historical run's safe terminal boundary.
            v2 = basis.get("v2", {})
            if not (
                binding
                and v2.get("device_id") == binding.device_id
                and v2.get("run_id") == binding.run_id
                and v2.get("safe_complete") is True
                and v2.get("safe_boundary")
                and row.safety_completed_at
            ):
                raise HTTPException(
                    409, err("v2_safe_evidence_required", "须恢复该运行的板端安全完成边界，人工确认不能替代原始证据")
                )
            row.end_time = row.safety_completed_at
            row.end_reason, row.phase, row.data_integrity = (
                "reviewed_safe_incomplete",
                "archived_incomplete",
                "incomplete",
            )
            db.add(
                EventLog(
                    test_id=test_id,
                    ts=now_iso(),
                    source="hmi_review",
                    event_code="INCOMPLETE_ARCHIVED",
                    operator_id=user.username,
                    level=1,
                    text=body.reason,
                    detail_json=json.dumps({"v2_safe_evidence": v2}, ensure_ascii=False),
                )
            )
            await db.commit()
            return ok({"test_id": test_id, "phase": row.phase, "data_integrity": row.data_integrity})
        snapshot = await status_cache.get_snapshot()
        measurement = snapshot.get("measurement") or {}
        temp = measurement.get("burden_temp_deg_c")
        client = get_hostcomm_client(request)
        if not (
            body.physical_safety_confirmed
            and client
            and client.is_online
            and status_cache.is_fresh
            and classify_state(status_cache.current_state) == "idle"
            and measurement.get("burden_temp_valid") is True
            and not isinstance(temp, bool)
            and isinstance(temp, (int, float))
            and math.isfinite(temp)
            and temp < 200
        ):
            raise HTTPException(409, err("review_not_safe", "须确认现场安全、设备在线空闲且有效料层温度低于200℃"))
        row.end_time = now_iso()
        row.end_reason = "manual_review_incomplete"
        row.phase = "archived_incomplete"
        row.data_integrity = "incomplete"
        row.state_at_end = status_cache.current_state
        db.add(
            EventLog(
                test_id=test_id,
                ts=now_iso(),
                source="hmi_review",
                event_code="INCOMPLETE_ARCHIVED",
                operator_id=user.username,
                level=1,
                text=body.reason,
                detail_json=json.dumps({"basis": snapshot, "physical_safety_confirmed": True}, ensure_ascii=False),
            )
        )
        await db.commit()
        if active_test.active_test_id == test_id:
            active_test.stop()
        return ok({"test_id": test_id, "phase": row.phase, "data_integrity": row.data_integrity})
