"""报告生成：共用带质量标志、600 ℃ 基准和版本来源的国标计算器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AlarmLog, ParameterSnapshot, ReportExport, SamplePoint, TestSession
from app.db.v2_models import V2LogCursor, V2LogGap, V2RunBinding, V2RunRecovery, V2SourceRecord
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.types import SampleRef
from app.services.recovery_queries import recovery_log_condition
from app.services.report_rendering import METRIC_ROWS, _num, _provenance_rows, _render_html, _render_pdf, _render_xlsx
from app.services.snapshot_data import json_object, object_value
from app.services.standard_metrics import MetricAccumulator, V2MetricAccumulator, number
from app.services.test_id import InvalidTestIdError, validate_test_id
from app.services.v2_run_recovery import V2RecoveryError, require_recovery_report_ready

SUPPORTED_FORMATS = {"html", "pdf", "xlsx"}


async def _write_report_file(path: Path, content: str | bytes) -> None:
    """以排他方式写报告；取消任务前先结束线程并清理本次生成的文件。"""

    def write_exclusive() -> None:
        if isinstance(content, bytes):
            with path.open("xb") as output:
                output.write(content)
        else:
            with path.open("x", encoding="utf-8") as output:
                output.write(content)

    worker = asyncio.create_task(asyncio.to_thread(write_exclusive))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        finally:
            path.unlink(missing_ok=True)
        raise


def compute_metrics(samples: list[SamplePoint], original_height_mm: float | None) -> dict[str, Any]:
    """内存样本与数据库流使用同一算法。缺失质量和参考值保持未知。"""
    reducer = MetricAccumulator(original_height_mm)
    for sample in samples:
        reducer.add(sample)
    return reducer.finish()


def _u64_at_most(column, value):
    return or_(func.length(column) < len(value), and_(func.length(column) == len(value), column <= value))


async def _v2_measurement_integrity(session, basis):
    """Derive read-only evidence from validated cuts, never from a user-editable completion flag."""
    if not isinstance(basis, dict):
        return "unknown", None
    if basis.get("outcome") in {"invalid", "aborted"}:
        return "incomplete", None
    try:
        start, end = (SampleRef.model_validate(basis.get(key)) for key in ("measurement_start", "measurement_end"))
    except (ValueError, TypeError):
        return "unknown", None
    if start.boot_id != end.boot_id or int(start.sample_seq) > int(end.sample_seq):
        return "incomplete", None
    boundaries = []
    for reference in (start, end):
        row = await session.scalar(
            select(V2SourceRecord).where(
                V2SourceRecord.device_id == basis.get("device_id"),
                V2SourceRecord.run_id == basis.get("run_id"),
                V2SourceRecord.record_type == "sample",
                V2SourceRecord.boot_id == reference.boot_id,
                V2SourceRecord.source_seq == reference.sample_seq,
            )
        )
        if row is None or row.record_bytes is None or row.log_id is None or row.record_seq is None:
            return "unknown", None
        boundaries.append(row)
    first, last = boundaries
    if first.log_id != last.log_id or int(first.record_seq) > int(last.record_seq):
        return "incomplete", None
    evidence = {
        "device_id": first.device_id,
        "log_id": first.log_id,
        "first_record_seq": first.record_seq,
        "last_record_seq": last.record_seq,
    }
    cursor = await session.get(V2LogCursor, (first.device_id, first.log_id))
    covered = (
        cursor is not None
        and cursor.verified_from_seq is not None
        and cursor.verified_through_seq is not None
        and int(cursor.verified_from_seq) <= int(first.record_seq)
        and int(cursor.verified_through_seq) >= int(last.record_seq)
    )
    if not covered:
        # An earlier reported gap remains audit evidence, but a later proven full
        # cut of the same immutable log can fill it without deleting its history.
        gap = await session.scalar(
            select(V2LogGap.id)
            .where(
                V2LogGap.device_id == first.device_id,
                V2LogGap.log_id == first.log_id,
                _u64_at_most(V2LogGap.first_record_seq, last.record_seq),
                (
                    ~_u64_at_most(V2LogGap.last_record_seq, str(int(first.record_seq) - 1))
                    if int(first.record_seq)
                    else True
                ),
            )
            .limit(1)
        )
        return "incomplete" if gap is not None else "unknown", evidence
    evidence.update(verified_from_seq=cursor.verified_from_seq, verified_through_seq=cursor.verified_through_seq)
    return "complete" if basis.get("measurement_complete") is True else "incomplete", evidence


async def compute_metrics_from_database(
    session: AsyncSession,
    test_id: str,
    original_height_mm: float | None,
) -> dict[str, Any]:
    """分批流式读取；v2 使用原始 sample 引用，v1 保留既有接收顺序。"""
    test = await session.scalar(select(TestSession).where(TestSession.test_id == test_id))
    basis, basis_valid = json_object(getattr(test, "measurement_basis_json", None))
    boundary = basis.get("measurement_end_sample_id")
    if boundary is not None and (type(boundary) is not int or boundary <= 0):
        basis_valid, boundary = False, None
    measurement_integrity = basis.get("measurement_data_integrity", getattr(test, "data_integrity", "unknown"))
    if not isinstance(measurement_integrity, str) or measurement_integrity not in {"unknown", "complete", "incomplete"}:
        basis_valid, measurement_integrity = False, "unknown"
    recipe_raw = getattr(test, "recipe_snapshot_json", None)
    recipe, recipe_valid = json_object(recipe_raw if recipe_raw != "null" else None)
    is_v2 = "v2" in basis
    source_log_evidence = None
    if is_v2:
        measurement_integrity, source_log_evidence = await _v2_measurement_integrity(session, basis.get("v2"))
    accumulator_type = V2MetricAccumulator if is_v2 else MetricAccumulator
    v2_options = {"v2_basis": basis.get("v2"), "profile_snapshot": recipe.get("safety_profile")} if is_v2 else {}
    reducer = accumulator_type(
        original_height_mm,
        **v2_options,
        measurement_complete=basis_valid and getattr(test, "measurement_completed_at", None) is not None,
        # V2's fixed event contract is checked against every raw detector quality below.
        detector_verified=is_v2 or basis.get("detector_verified") is True,
        data_complete=basis_valid and measurement_integrity == "complete",
        measurement_end_sample_id=boundary,
        test_id=test_id,
    )
    if not basis_valid:
        reducer.limitations.add("malformed_measurement_basis")
    if not recipe_valid:
        reducer.limitations.add("malformed_recipe_snapshot")
    # Decimal uint64 strings must not be cast to SQLite's signed 64-bit INTEGER.
    # Boot identity is a grouping key; only proven same-boot boundaries enter v2 metrics.
    ordering = (
        [
            SamplePoint.source_boot_id,
            func.length(SamplePoint.source_sequence),
            SamplePoint.source_sequence,
            SamplePoint.id,
        ]
        if is_v2
        else [SamplePoint.id]
    )
    rows = await session.stream_scalars(
        select(SamplePoint).where(SamplePoint.test_id == test_id).order_by(*ordering).execution_options(yield_per=512)
    )
    try:
        async for sample in rows:
            reducer.add(sample)
    finally:
        await rows.close()
    result = reducer.finish()
    if is_v2:
        if result.pop("source_measurement_invalid"):
            measurement_integrity = "incomplete"
        elif measurement_integrity == "complete" and "freshness_profile_missing_or_invalid" in result["limitations"]:
            measurement_integrity = "unknown"
        result["measurement_source_log"] = source_log_evidence
    result.update(
        standard="GB/T 34211-2017",
        mode=getattr(test, "mode", "custom"),
        data_integrity=getattr(test, "data_integrity", "unknown"),
        measurement_data_integrity=measurement_integrity,
        recipe_snapshot=recipe if recipe else None,
        compliance="not_certified",
    )
    return result


def _row_values(row, *, exclude=()):
    return (
        {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name not in exclude}
        if row is not None
        else None
    )


def _source_watermark():
    return (
        select(func.coalesce(func.max(V2SourceRecord.id), 0))
        .where(V2SourceRecord.device_id == V2RunRecovery.device_id, V2SourceRecord.run_id == V2RunRecovery.run_id)
        .correlate(V2RunRecovery)
        .scalar_subquery()
    )


async def _report_basis(session, test_id):
    await require_recovery_report_ready(session, test_id)
    test = await session.scalar(
        select(TestSession).where(TestSession.test_id == test_id).execution_options(populate_existing=True)
    )
    if test is None:
        raise ValueError(f"试验不存在: {test_id}")
    samples = (
        await session.execute(
            select(func.count(SamplePoint.id), func.max(SamplePoint.id)).where(SamplePoint.test_id == test_id)
        )
    ).one()
    alarms = (
        await session.scalars(
            select(AlarmLog)
            .where(recovery_log_condition(AlarmLog, test_id))
            .order_by(AlarmLog.level.desc(), AlarmLog.id.desc())
            .limit(50)
            .execution_options(populate_existing=True)
        )
    ).all()
    params = await session.scalar(
        select(ParameterSnapshot)
        .where(ParameterSnapshot.test_id == test_id, ParameterSnapshot.source == "test_start")
        .order_by(ParameterSnapshot.id.desc())
        .execution_options(populate_existing=True)
    )
    recovery = (
        await session.execute(
            select(
                V2RunRecovery.id,
                V2RunRecovery.review_revision,
                V2RunRecovery.replay_through_id,
                _source_watermark().label("source_watermark"),
            ).where(V2RunRecovery.test_id == test_id)
        )
    ).first()
    binding = await session.scalar(
        select(V2RunBinding).where(V2RunBinding.test_id == test_id).execution_options(populate_existing=True)
    )
    source = None
    if binding is not None:
        run_records = (V2SourceRecord.device_id == binding.device_id, V2SourceRecord.run_id == binding.run_id)
        watermark = (
            await session.execute(
                select(func.count(V2SourceRecord.id), func.max(V2SourceRecord.id)).where(*run_records)
            )
        ).one()
        logs = select(V2SourceRecord.log_id).where(*run_records, V2SourceRecord.log_id.is_not(None)).distinct()
        cursors = await session.scalars(
            select(V2LogCursor)
            .where(V2LogCursor.device_id == binding.device_id, V2LogCursor.log_id.in_(logs))
            .order_by(V2LogCursor.log_id)
            .execution_options(populate_existing=True)
        )
        gaps = (
            await session.execute(
                select(func.count(V2LogGap.id), func.max(V2LogGap.id)).where(
                    V2LogGap.device_id == binding.device_id, V2LogGap.log_id.in_(logs)
                )
            )
        ).one()
        measurement, _ = json_object(test.measurement_basis_json)
        integrity, evidence = await _v2_measurement_integrity(session, measurement.get("v2"))
        source = {
            "binding": _row_values(binding),
            "count": watermark[0],
            "highwater": watermark[1],
            "cursors": [_row_values(row) for row in cursors],
            "gap_count": gaps[0],
            "gap_highwater": gaps[1],
            "measurement_integrity": integrity,
            "measurement_evidence": evidence,
        }
    return {
        "test": _row_values(test, exclude=("report_path",)),
        "sample_count": samples[0],
        "sample_highwater": samples[1],
        "alarms": [_row_values(row) for row in alarms],
        "params": _row_values(params),
        "recovery": dict(recovery._mapping) if recovery else None,
        "source": source,
    }


async def generate_report(
    session: AsyncSession,
    test_id: str,
    *,
    operator_id: str,
    fmt: str = "html",
    options: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> ReportExport:
    """Freeze input, release the read transaction, render externally, then compare-and-archive."""
    from app.db.models import BackgroundJob
    from app.services.job_store import encode
    from app.services.render_client import RenderError, render_snapshot

    test_id = validate_test_id(test_id)
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"不支持的报告格式: {fmt}")
    basis = await _report_basis(session, test_id)
    height = basis["test"]["original_height_mm"]
    metrics = await compute_metrics_from_database(
        session, test_id, height if height is not None else _num((options or {}).get("original_height_mm"))
    )
    # A changing source while the snapshot is built must not silently mix revisions.
    if await _report_basis(session, test_id) != basis:
        await session.rollback()
        raise RenderError("source_changed_during_snapshot")
    snapshot = dict(basis, schema_version=1, format=fmt, metrics=metrics, generated_at=now_iso())
    snapshot["metrics"]["report_generated_at"] = snapshot["generated_at"]
    snapshot_json = encode(snapshot)
    digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    await session.commit()
    if task_id is not None:
        saved = await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.task_id == task_id, BackgroundJob.status == "running")
            .values(snapshot_json=snapshot_json)
        )
        if saved.rowcount != 1:
            await session.rollback()
            raise RenderError("job_no_longer_running")
        await session.commit()
    settings = get_settings()
    directory = settings.reports_dir.resolve()
    content = await render_snapshot(snapshot, directory, min_free_bytes=settings.smd_storage_min_free_bytes)
    path = directory / f"{test_id}-{uuid.uuid4().hex}.{fmt}"
    try:
        await _write_report_file(path, content)
        artifact_digest = hashlib.sha256(content).hexdigest()
        from app.services.artifact_integrity import verify

        await verify(path, artifact_digest)
        # Reserve the SQLite writer before re-reading versions; no render runs in this transaction.
        await session.execute(
            update(TestSession).where(TestSession.test_id == test_id).values(report_path=TestSession.report_path)
        )
        if await _report_basis(session, test_id) != basis:
            if basis["recovery"]:
                raise V2RecoveryError("recovery_changed_during_report", "恢复档案在生成报告期间发生变化，请重新生成")
            raise RenderError("source_changed_during_report")
        record = ReportExport(
            test_id=test_id,
            generated_at=snapshot["generated_at"],
            operator_id=operator_id,
            format=fmt,
            file_path=str(path),
            file_size_bytes=len(content),
            notes=json.dumps(
                {
                    "metrics": metrics,
                    "measurement_basis": json_object(basis["test"]["measurement_basis_json"])[0],
                    "start_time": basis["test"]["start_time"],
                    "discovered_at": basis["test"]["discovered_at"],
                    "recovery_snapshot": basis["recovery"],
                    "snapshot_sha256": digest,
                    "artifact_sha256": artifact_digest,
                    "snapshot_schema": 1,
                    "task_id": task_id,
                },
                ensure_ascii=False,
            ),
        )
        session.add(record)
        await session.execute(update(TestSession).where(TestSession.test_id == test_id).values(report_path=str(path)))
        await session.commit()
    except BaseException:
        await session.rollback()
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise
    # A post-commit refresh failure must never delete an already archived file.
    await session.refresh(record)
    return record
