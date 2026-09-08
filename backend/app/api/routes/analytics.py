"""数据分析路由 /api/analytics：多试验关键指标对比（规格 7.3 AnalyticsPage）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import DbDep, get_current_user
from app.api.schemas import ok
from app.db.models import SamplePoint, TestSession
from app.services import report_service
from app.services.v2_run_recovery import V2RecoveryError, require_recovery_report_ready

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(get_current_user)])


@router.get("/compare")
async def compare(db: DbDep, test_ids: str = "", original_height_mm: float | None = None):
    """对比多个试验的关键指标。test_ids 为逗号分隔列表。权限：Observer+。"""
    ids = [t.strip() for t in test_ids.split(",") if t.strip()]
    out = []
    for tid in ids[:8]:  # 限制对比数量，避免一次拉取过多
        test = await db.scalar(select(TestSession).where(TestSession.test_id == tid))
        if test is None:
            continue
        sample_count = await db.scalar(select(func.count()).select_from(SamplePoint).where(SamplePoint.test_id == tid))
        height = test.original_height_mm if test.original_height_mm is not None else original_height_mm
        metrics = await report_service.compute_metrics_from_database(db, tid, height)
        out.append(
            {
                "test_id": tid,
                "operator_id": test.operator_id,
                "start_time": test.start_time,
                "discovered_at": test.discovered_at,
                "end_time": test.end_time,
                "sample_count": int(sample_count or 0),
                "metrics": metrics,
            }
        )
    return ok(out)


from pydantic import BaseModel, Field

from app.api.validation import TestId
from app.services.snapshot_data import json_object, object_value
from app.services.standard_metrics import evaluate_repeatability


class RepeatabilityRequest(BaseModel):
    test_ids: list[TestId] = Field(min_length=2, max_length=4)


@router.post("/repeatability")
async def repeatability(body: RepeatabilityRequest, db: DbDep):
    """附录B按实际顺序判定；样品/版本/有效性不满足时不返回拼凑的平均结果。"""
    errors = []
    if len(set(body.test_ids)) != len(body.test_ids):
        errors.append("duplicate_test_id")
    identities = []
    values = []
    for test_id in body.test_ids:
        test = await db.scalar(select(TestSession).where(TestSession.test_id == test_id))
        if test is None:
            errors.append(f"{test_id}:not_found")
            continue
        basis, _ = json_object(test.measurement_basis_json)
        recipe, _ = json_object(test.recipe_snapshot_json)
        batch = object_value(basis.get("sample_metadata")).get("batch")
        if not batch or not recipe.get("digest"):
            errors.append(f"{test_id}:missing_sample_or_recipe_identity")
        identities.append((batch, test.mode, recipe.get("digest")))
        if test.end_time is None or test.measurement_completed_at is None or test.data_integrity != "complete":
            errors.append(f"{test_id}:experiment_not_validated")
        try:
            await require_recovery_report_ready(db, test_id)
        except V2RecoveryError as exc:
            errors.append(f"{test_id}:{exc.code}")
        values.append(await report_service.compute_metrics_from_database(db, test_id, test.original_height_mm))
    if len(set(identities)) != 1:
        errors.append("sample_or_recipe_mismatch")
    results = []
    if not errors:
        for metric in ("t10", "t40", "ts", "td_drip_temp"):
            measurements = [item.get(metric) for item in values]
            try:
                results.append(evaluate_repeatability(metric, measurements))
            except ValueError:
                errors.append(f"{metric}:missing_valid_values")
    return ok(
        {
            "eligible": not errors,
            "errors": errors,
            "test_ids": body.test_ids,
            "results": results if not errors else [],
            "standard": "GB/T 34211-2017 附录B",
        }
    )
