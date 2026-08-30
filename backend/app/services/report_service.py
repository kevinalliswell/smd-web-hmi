"""报告生成服务（规格 3.8 / SOP §13 结果计算）。

从 sample_point / event_log / alarm_log / parameter_snapshot 汇总一次试验，
计算可由曲线稳健导出的指标，渲染为离线 HTML 报告并登记 report_export。

注：T10/T40/Ts 等需"原始料层高度 H"的指标，当前 schema 未存 H（见
docs/待确认事项与接口对齐清单 Q9）；若 options 提供 original_height_mm 则计算，
否则标注 N/A，不臆造数值。
"""

from __future__ import annotations

import asyncio
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AlarmLog, ParameterSnapshot, ReportExport, SamplePoint, TestSession
from app.hostcomm.protocol import now_iso
from app.services.test_id import InvalidTestIdError, validate_test_id


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(v: Any, digits: int = 1, unit: str = "") -> str:
    n = _num(v)
    return "N/A" if n is None else f"{n:.{digits}f}{unit}"


def compute_metrics(samples: list[SamplePoint], original_height_mm: float | None) -> dict[str, Any]:
    """从采样点计算结果指标。缺数据的项返回 None。"""
    temps = [_num(s.burden_temp) for s in samples]
    furnace = [_num(s.furnace_pv) for s in samples]
    dps = [(_num(s.delta_p), _num(s.burden_temp)) for s in samples]
    drips = [(_num(s.drip_weight), _num(s.burden_temp)) for s in samples]
    disps = [(_num(s.displacement), _num(s.burden_temp)) for s in samples]

    metrics: dict[str, Any] = {}
    metrics["furnace_pv_max"] = max([f for f in furnace if f is not None], default=None)
    metrics["burden_temp_max"] = max([t for t in temps if t is not None], default=None)

    # ΔPmax 及其对应料层温度
    dp_valid = [(dp, t) for dp, t in dps if dp is not None]
    if dp_valid:
        dp_max, dp_t = max(dp_valid, key=lambda x: x[0])
        metrics["delta_p_max"] = dp_max
        metrics["delta_p_max_temp"] = dp_t
    else:
        metrics["delta_p_max"] = None
        metrics["delta_p_max_temp"] = None

    # 滴落：总滴落量 + Td（首次滴落时料层温度，阈值 0.5 g）
    drip_vals = [d for d, _ in drips if d is not None]
    metrics["drip_weight_total"] = max(drip_vals, default=None)
    metrics["td_drip_temp"] = next((t for d, t in drips if d is not None and d > 0.5), None)

    # 收缩：ΔH 与 T10/T40（需原始料层高度 H）
    disp_vals = [d for d, _ in disps if d is not None]
    metrics["displacement_max"] = max(disp_vals, default=None)
    metrics["original_height_mm"] = original_height_mm
    if original_height_mm and original_height_mm > 0:

        def temp_at_shrink(pct: float) -> float | None:
            target = original_height_mm * pct
            return next((t for d, t in disps if d is not None and d >= target), None)

        metrics["t10"] = temp_at_shrink(0.10)
        metrics["t40"] = temp_at_shrink(0.40)
        metrics["delta_h_pct"] = (
            (metrics["displacement_max"] / original_height_mm * 100)
            if metrics["displacement_max"] is not None
            else None
        )
    else:
        metrics["t10"] = None
        metrics["t40"] = None
        metrics["delta_h_pct"] = None

    return metrics


async def compute_metrics_from_database(
    session: AsyncSession,
    test_id: str,
    original_height_mm: float | None,
) -> dict[str, Any]:
    """用常量内存的 SQL 聚合计算报告指标。"""
    aggregate = (
        await session.execute(
            select(
                func.max(SamplePoint.furnace_pv).label("furnace_pv_max"),
                func.max(SamplePoint.burden_temp).label("burden_temp_max"),
                func.max(SamplePoint.delta_p).label("delta_p_max"),
                func.max(SamplePoint.drip_weight).label("drip_weight_total"),
                func.max(SamplePoint.displacement).label("displacement_max"),
            ).where(SamplePoint.test_id == test_id)
        )
    ).one()

    delta_p_max_temp = await session.scalar(
        select(SamplePoint.burden_temp)
        .where(SamplePoint.test_id == test_id, SamplePoint.delta_p.is_not(None))
        .order_by(SamplePoint.delta_p.desc(), SamplePoint.id)
        .limit(1)
    )
    td_drip_temp = await session.scalar(
        select(SamplePoint.burden_temp)
        .where(SamplePoint.test_id == test_id, SamplePoint.drip_weight > 0.5)
        .order_by(SamplePoint.ts, SamplePoint.id)
        .limit(1)
    )

    metrics: dict[str, Any] = {
        "furnace_pv_max": aggregate.furnace_pv_max,
        "burden_temp_max": aggregate.burden_temp_max,
        "delta_p_max": aggregate.delta_p_max,
        "delta_p_max_temp": delta_p_max_temp,
        "drip_weight_total": aggregate.drip_weight_total,
        "td_drip_temp": td_drip_temp,
        "displacement_max": aggregate.displacement_max,
        "original_height_mm": original_height_mm,
    }
    if original_height_mm and original_height_mm > 0:

        async def temp_at_shrink(pct: float) -> float | None:
            return await session.scalar(
                select(SamplePoint.burden_temp)
                .where(
                    SamplePoint.test_id == test_id,
                    SamplePoint.displacement >= original_height_mm * pct,
                )
                .order_by(SamplePoint.ts, SamplePoint.id)
                .limit(1)
            )

        metrics["t10"] = await temp_at_shrink(0.10)
        metrics["t40"] = await temp_at_shrink(0.40)
        metrics["delta_h_pct"] = (
            aggregate.displacement_max / original_height_mm * 100 if aggregate.displacement_max is not None else None
        )
    else:
        metrics["t10"] = None
        metrics["t40"] = None
        metrics["delta_h_pct"] = None
    return metrics


def _render_html(
    test: TestSession,
    metrics: dict[str, Any],
    sample_count: int,
    alarms: list[AlarmLog],
    params: ParameterSnapshot | None,
) -> str:
    e = html.escape

    def row(k: str, v: str) -> str:
        return f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>"

    h_note = "" if metrics.get("original_height_mm") else "（未提供原始料层高度 H，无法计算）"
    metric_rows = "".join(
        [
            row("炉温峰值", _fmt(metrics["furnace_pv_max"], 1, " ℃")),
            row("料层温度峰值", _fmt(metrics["burden_temp_max"], 1, " ℃")),
            row("最大压差 ΔPmax", _fmt(metrics["delta_p_max"], 0, " Pa")),
            row("ΔPmax 对应料层温度", _fmt(metrics["delta_p_max_temp"], 1, " ℃")),
            row("总滴落量", _fmt(metrics["drip_weight_total"], 2, " g")),
            row("滴落温度 Td", _fmt(metrics["td_drip_temp"], 1, " ℃")),
            row("最大位移", _fmt(metrics["displacement_max"], 2, " mm")),
            row(
                "T10（10% 收缩温度）", _fmt(metrics["t10"], 1, " ℃") + (f" {h_note}" if metrics["t10"] is None else "")
            ),
            row(
                "T40（40% 收缩温度）", _fmt(metrics["t40"], 1, " ℃") + (f" {h_note}" if metrics["t40"] is None else "")
            ),
            row("收缩率 ΔH", _fmt(metrics["delta_h_pct"], 1, " %")),
        ]
    )

    alarm_rows = (
        "".join(
            f"<tr><td>L{a.level}</td><td>{e(a.alarm_code)}</td><td>{e(a.text or '')}</td>"
            f"<td>{e(a.occur_time)}</td><td>{e(a.clear_time or '—')}</td></tr>"
            for a in alarms[:50]
        )
        or "<tr><td colspan='5'>无报警</td></tr>"
    )

    params_summary = ""
    if params is not None:
        try:
            pv = json.loads(params.params_json)
            params_summary = f"<pre>{e(json.dumps(pv, ensure_ascii=False, indent=2))}</pre>"
        except (ValueError, TypeError):
            params_summary = "<p>参数快照解析失败</p>"
    else:
        params_summary = "<p>无本试验参数快照</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>熔滴炉试验报告 {e(test.test_id)}</title>
<style>
 body {{ font-family: 'Segoe UI','Microsoft YaHei',sans-serif; color:#1a2230; margin:32px; }}
 h1 {{ font-size:20px; border-bottom:2px solid #2563eb; padding-bottom:8px; }}
 h2 {{ font-size:15px; margin-top:24px; color:#2563eb; }}
 table {{ border-collapse:collapse; width:100%; margin-top:8px; font-size:13px; }}
 th,td {{ border:1px solid #d0d7e2; padding:6px 10px; text-align:left; }}
 th {{ background:#f1f5fb; width:220px; }}
 .meta th {{ width:160px; }}
 pre {{ background:#f7f9fc; border:1px solid #d0d7e2; padding:10px; font-size:12px; overflow:auto; }}
 .foot {{ margin-top:28px; color:#6b7686; font-size:11px; }}
</style></head><body>
<h1>熔滴炉试验报告</h1>
<table class="meta">
 {row("试验编号", test.test_id)}
 {row("操作员", test.operator_id)}
 {row("开始时间", test.start_time)}
 {row("结束时间", test.end_time or "进行中")}
 {row("结束原因", test.end_reason or "—")}
 {row("样品标识", test.sample_label or "—")}
 {row("采样点数", str(sample_count))}
 {row("固件版本", (params.fw_version if params else None) or "—")}
 {row("参数 CRC", (params.param_crc if params else None) or "—")}
</table>

<h2>结果指标（GB/T 34211 / SOP §13）</h2>
<table>{metric_rows}</table>

<h2>报警汇总</h2>
<table><tr><th>等级</th><th>报警码</th><th>说明</th><th>发生</th><th>消除</th></tr>{alarm_rows}</table>

<h2>试验开始参数快照</h2>
{params_summary}

<p class="foot">生成时间：{now_iso()} · 本报告由 smd-web-hmi 自动生成 · 原始曲线与事件日志见日志导出包。</p>
</body></html>"""


async def generate_report(
    session: AsyncSession,
    test_id: str,
    *,
    operator_id: str,
    fmt: str = "html",
    options: dict[str, Any] | None = None,
) -> ReportExport:
    """生成报告并登记 report_export。当前支持 html。"""
    test_id = validate_test_id(test_id)
    options = options or {}
    if fmt != "html":
        # 其它格式（pdf/xlsx）留待后续；先以 html 兜底并在 notes 注明
        fmt = "html"

    test = await session.scalar(select(TestSession).where(TestSession.test_id == test_id))
    if test is None:
        raise ValueError(f"试验不存在: {test_id}")

    sample_count = await session.scalar(
        select(func.count()).select_from(SamplePoint).where(SamplePoint.test_id == test_id)
    )
    alarms = list(
        (
            await session.execute(
                select(AlarmLog)
                .where(AlarmLog.test_id == test_id)
                .order_by(AlarmLog.level.desc(), AlarmLog.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    params = await session.scalar(
        select(ParameterSnapshot)
        .where(ParameterSnapshot.test_id == test_id, ParameterSnapshot.source == "test_start")
        .order_by(ParameterSnapshot.id.desc())
    )

    metrics = await compute_metrics_from_database(session, test_id, _num(options.get("original_height_mm")))
    content = _render_html(test, metrics, int(sample_count or 0), alarms, params)

    settings = get_settings()
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    reports_dir = settings.reports_dir.resolve()
    path = (reports_dir / f"{test_id}-{stamp}.html").resolve()
    if not path.is_relative_to(reports_dir):
        # test_id 已有白名单；这里保留最终写入点的纵深防御。
        raise InvalidTestIdError("报告路径超出报告目录")
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")
    size = (await asyncio.to_thread(path.stat)).st_size

    record = ReportExport(
        test_id=test_id,
        generated_at=now_iso(),
        operator_id=operator_id,
        format="html",
        file_path=str(path),
        file_size_bytes=size,
        notes=json.dumps({"metrics": metrics}, ensure_ascii=False),
    )
    session.add(record)
    # 试验会话登记报告路径
    test.report_path = str(path)
    await session.commit()
    await session.refresh(record)
    return record
