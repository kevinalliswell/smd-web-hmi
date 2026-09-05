"""报告生成：共用带质量标志、600 ℃ 基准和版本来源的国标计算器。"""

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
from app.services.standard_metrics import MetricAccumulator, number
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
    ("软熔开始温度 Ts", "ts", 1, " ℃"),
    ("熔落带厚度 ΔH", "delta_h_mm", 1, " mm"),
    ("T40 − T10", "t40_minus_t10", 1, " ℃"),
    ("Td − Ts", "td_minus_ts", 1, " ℃"),
    ("Td − T10", "td_minus_t10", 1, " ℃"),
    ("600 ℃ 位移基准", "reference_displacement_mm", 2, " mm"),
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
    return number(v)


def _fmt(v: Any, digits: int = 1, unit: str = "") -> str:
    n = _num(v)
    return "N/A" if n is None else f"{n:.{digits}f}{unit}"


def _xlsx_value(value: Any) -> Any:
    """阻止外部文本在电子表格中被解释为公式。"""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def compute_metrics(samples: list[SamplePoint], original_height_mm: float | None) -> dict[str, Any]:
    """内存样本与数据库流使用同一算法。缺失质量和参考值保持未知。"""
    reducer = MetricAccumulator(original_height_mm)
    for sample in samples:
        reducer.add(sample)
    return reducer.finish()


async def compute_metrics_from_database(
    session: AsyncSession,
    test_id: str,
    original_height_mm: float | None,
) -> dict[str, Any]:
    """分批流式读取，避免把完整实验曲线载入内存；按接收序号处理时钟回拨。"""
    test = await session.scalar(select(TestSession).where(TestSession.test_id == test_id))
    basis = json.loads(getattr(test, "measurement_basis_json", None) or "{}")
    reducer = MetricAccumulator(
        original_height_mm,
        measurement_complete=getattr(test, "measurement_completed_at", None) is not None,
        detector_verified=basis.get("detector_verified") is True,
        data_complete=getattr(test, "data_integrity", None) == "complete",
    )
    rows = await session.stream_scalars(
        select(SamplePoint)
        .where(SamplePoint.test_id == test_id)
        .order_by(SamplePoint.id)
        .execution_options(yield_per=512)
    )
    try:
        async for sample in rows:
            reducer.add(sample)
    finally:
        await rows.close()
    result = reducer.finish()
    result.update(
        standard="GB/T 34211-2017",
        mode=getattr(test, "mode", "custom"),
        data_integrity=getattr(test, "data_integrity", "unknown"),
        recipe_snapshot=json.loads(getattr(test, "recipe_snapshot_json", None) or "null"),
        compliance="not_certified",
    )
    return result


def _provenance_rows(test, metrics):
    return [
        ("参考标准", "GB/T 34211-2017"),
        ("实验模式", "标准模板" if metrics.get("mode") == "standard" else "非标 / 历史未标定"),
        ("算法版本", metrics.get("algorithm_version", "unknown")),
        ("数据完整性", metrics.get("data_integrity", "unknown")),
        ("600 ℃ 位移基准来源", metrics.get("reference_source") or "缺失"),
        ("结果限制", ", ".join(metrics.get("limitations", [])) or "无自动检出的数据缺口"),
        ("符合性", "未签发国标符合性结论；缺失数据、原文争议和现场条件须复核"),
    ]


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
 {row("原始料层高度 H", _fmt(metrics.get("original_height_mm"), 2, " mm"))}
 {row("备注", test.notes or "—")}
 {row("采样点数", str(sample_count))}
 {row("固件版本", (params.fw_version if params else None) or "—")}
 {row("参数 CRC", (params.param_crc if params else None) or "—")}
</table>

<h2>计算依据与结果限制</h2>
<table>{"".join(row(k, str(v)) for k, v in _provenance_rows(test, metrics))}</table>
<h2>结果指标（GB/T 34211-2017 §9）</h2>
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
        ["原始料层高度 H", _fmt(metrics.get("original_height_mm"), 2, " mm")],
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
    metadata.extend(
        [
            [key, Paragraph(html.escape(str(value)), styles["BodyText"])]
            for key, value in _provenance_rows(test, metrics)
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
        ("原始料层高度 H (mm)", metrics.get("original_height_mm")),
        ("备注", test.notes or "—"),
        ("采样点数", sample_count),
        ("固件版本", (params.fw_version if params else None) or "—"),
        ("参数 CRC", (params.param_crc if params else None) or "—"),
        ("生成时间", now_iso()),
    ):
        summary.append([_xlsx_value(value) for value in item])
    for item in _provenance_rows(test, metrics):
        summary.append([_xlsx_value(value) for value in item])
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
        alarm_sheet.append(
            [
                _xlsx_value(value)
                for value in (alarm.level, alarm.alarm_code, alarm.text or "", alarm.occur_time, alarm.clear_time)
            ]
        )
    for column, width in zip("ABCDE", (10, 24, 48, 28, 28), strict=True):
        alarm_sheet.column_dimensions[column].width = width

    param_sheet = workbook.create_sheet("试验开始参数")
    param_sheet.append(["参数快照 JSON"])
    param_sheet["A1"].font = Font(bold=True)
    param_sheet.append([_xlsx_value(params.params_json if params is not None else "无本试验参数快照")])
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
