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
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AlarmLog, ParameterSnapshot, ReportExport, SamplePoint, TestSession
from app.hostcomm.protocol import now_iso
from app.services.test_id import InvalidTestIdError, validate_test_id

SUPPORTED_FORMATS = {"html", "pdf", "xlsx"}

METRIC_ROWS = (
    ("炉温峰值", "furnace_pv_max", 1, " ℃"),
    ("料层温度峰值", "burden_temp_max", 1, " ℃"),
    ("最大压差 ΔPmax", "delta_p_max", 0, " Pa"),
    ("ΔPmax 对应料层温度", "delta_p_max_temp", 1, " ℃"),
    ("总滴落量", "drip_weight_total", 2, " g"),
    ("滴落温度 Td", "td_drip_temp", 1, " ℃"),
    ("最大位移", "displacement_max", 2, " mm"),
    ("T10（10% 收缩温度）", "t10", 1, " ℃"),
    ("T40（40% 收缩温度）", "t40", 1, " ℃"),
    ("收缩率 ΔH", "delta_h_pct", 1, " %"),
)


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
        row(
            label,
            _fmt(metrics[key], digits, unit) + (f" {h_note}" if key in {"t10", "t40"} and metrics[key] is None else ""),
        )
        for label, key, digits, unit in METRIC_ROWS
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
 {row("原始料层高度 H", _fmt(test.original_height_mm, 2, " mm"))}
 {row("备注", test.notes or "—")}
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


def _render_pdf(
    test: TestSession,
    metrics: dict[str, Any],
    sample_count: int,
    alarms: list[AlarmLog],
    params: ParameterSnapshot | None,
) -> bytes:
    """生成适合打印/归档的 Unicode PDF。"""
    buffer = BytesIO()
    font_name = "STSong-Light"
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Heading2", "BodyText"):
        styles[style_name].fontName = font_name
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"熔滴炉试验报告 {test.test_id}",
    )
    story = [Paragraph("熔滴炉试验报告", styles["Title"]), Spacer(1, 4 * mm)]

    metadata = [
        ["试验编号", test.test_id],
        ["操作员", test.operator_id],
        ["开始时间", test.start_time],
        ["结束时间", test.end_time or "进行中"],
        ["结束原因", test.end_reason or "—"],
        ["样品标识", test.sample_label or "—"],
        ["原始料层高度 H", _fmt(test.original_height_mm, 2, " mm")],
        ["备注", test.notes or "—"],
        ["采样点数", str(sample_count)],
        ["固件版本", (params.fw_version if params else None) or "—"],
        ["参数 CRC", (params.param_crc if params else None) or "—"],
    ]
    table_style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EFF6FF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]
    )
    meta_table = Table(metadata, colWidths=[43 * mm, 120 * mm], repeatRows=0)
    meta_table.setStyle(table_style)
    story.extend([meta_table, Spacer(1, 5 * mm), Paragraph("结果指标", styles["Heading2"])])
    metric_data = [["指标", "结果"]] + [
        [label, _fmt(metrics[key], digits, unit)] for label, key, digits, unit in METRIC_ROWS
    ]
    metrics_table = Table(metric_data, colWidths=[82 * mm, 81 * mm], repeatRows=1)
    metrics_table.setStyle(table_style)
    metrics_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DBEAFE")),
                ("FONTNAME", (0, 0), (-1, 0), font_name),
            ]
        )
    )
    story.extend([metrics_table, Spacer(1, 5 * mm), Paragraph("报警汇总", styles["Heading2"])])
    alarm_data = [["等级", "报警码", "说明", "发生", "消除"]]
    alarm_data.extend(
        [f"L{alarm.level}", alarm.alarm_code, alarm.text or "", alarm.occur_time, alarm.clear_time or "—"]
        for alarm in alarms[:50]
    )
    if len(alarm_data) == 1:
        alarm_data.append(["—", "无报警", "", "", ""])
    alarm_table = Table(alarm_data, colWidths=[12 * mm, 28 * mm, 51 * mm, 38 * mm, 38 * mm], repeatRows=1)
    alarm_table.setStyle(table_style)
    story.extend(
        [
            alarm_table,
            Spacer(1, 5 * mm),
            Paragraph(f"生成时间：{now_iso()} · smd-web-hmi 自动生成", styles["BodyText"]),
        ]
    )
    document.build(story)
    return buffer.getvalue()


def _render_xlsx(
    test: TestSession,
    metrics: dict[str, Any],
    sample_count: int,
    alarms: list[AlarmLog],
    params: ParameterSnapshot | None,
) -> bytes:
    """生成可继续统计加工的 XLSX 工作簿。"""
    workbook = Workbook()
    summary = workbook.active
    summary.title = "试验摘要"
    summary.append(["字段", "值"])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    for item in (
        ("试验编号", test.test_id),
        ("操作员", test.operator_id),
        ("开始时间", test.start_time),
        ("结束时间", test.end_time or "进行中"),
        ("结束原因", test.end_reason or "—"),
        ("样品标识", test.sample_label or "—"),
        ("原始料层高度 H (mm)", test.original_height_mm),
        ("备注", test.notes or "—"),
        ("采样点数", sample_count),
        ("固件版本", (params.fw_version if params else None) or "—"),
        ("参数 CRC", (params.param_crc if params else None) or "—"),
        ("生成时间", now_iso()),
    ):
        summary.append(item)
    summary.column_dimensions["A"].width = 28
    summary.column_dimensions["B"].width = 52

    metric_sheet = workbook.create_sheet("结果指标")
    metric_sheet.append(["指标", "原始值", "显示值"])
    for cell in metric_sheet[1]:
        cell.font = Font(bold=True)
    for label, key, digits, unit in METRIC_ROWS:
        metric_sheet.append([label, metrics[key], _fmt(metrics[key], digits, unit)])
    metric_sheet.column_dimensions["A"].width = 30
    metric_sheet.column_dimensions["B"].width = 16
    metric_sheet.column_dimensions["C"].width = 20

    alarm_sheet = workbook.create_sheet("报警汇总")
    alarm_sheet.append(["等级", "报警码", "说明", "发生时间", "消除时间"])
    for cell in alarm_sheet[1]:
        cell.font = Font(bold=True)
    for alarm in alarms[:50]:
        alarm_sheet.append([alarm.level, alarm.alarm_code, alarm.text or "", alarm.occur_time, alarm.clear_time])
    for column, width in zip("ABCDE", (10, 24, 48, 28, 28), strict=True):
        alarm_sheet.column_dimensions[column].width = width

    param_sheet = workbook.create_sheet("试验开始参数")
    param_sheet.append(["参数快照 JSON"])
    param_sheet["A1"].font = Font(bold=True)
    param_sheet.append([params.params_json if params is not None else "无本试验参数快照"])
    param_sheet.column_dimensions["A"].width = 100

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def generate_report(
    session: AsyncSession,
    test_id: str,
    *,
    operator_id: str,
    fmt: str = "html",
    options: dict[str, Any] | None = None,
) -> ReportExport:
    """生成并登记 HTML、PDF 或 XLSX 报告。"""
    test_id = validate_test_id(test_id)
    options = options or {}
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"不支持的报告格式: {fmt}")

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

    original_height_mm = (
        test.original_height_mm if test.original_height_mm is not None else _num(options.get("original_height_mm"))
    )
    metrics = await compute_metrics_from_database(session, test_id, original_height_mm)
    renderers = {"html": _render_html, "pdf": _render_pdf, "xlsx": _render_xlsx}
    content = renderers[fmt](test, metrics, int(sample_count or 0), alarms, params)

    settings = get_settings()
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    reports_dir = settings.reports_dir.resolve()
    path = (reports_dir / f"{test_id}-{stamp}-{uuid.uuid4().hex}.{fmt}").resolve()
    if not path.is_relative_to(reports_dir):
        # test_id 已有白名单；这里保留最终写入点的纵深防御。
        raise InvalidTestIdError("报告路径超出报告目录")
    await _write_report_file(path, content)
    size = (await asyncio.to_thread(path.stat)).st_size

    record = ReportExport(
        test_id=test_id,
        generated_at=now_iso(),
        operator_id=operator_id,
        format=fmt,
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
