"""日志导出服务（规格 3.7）。

将上位机已归档的某次试验数据（sample_point / event_log / alarm_log /
parameter_snapshot）导出为 CSV 并打包为 zip，落盘到 exports 目录。

注：协议第 10 节描述的是从 STM32 分块导出"设备端"日志（CSV/Base64 分块）；
本服务导出的是**上位机数据库已归档**的数据，便于追溯归档。设备端分块导出
（log_request/log_chunk/log_result）留待后续接入 HostComm 客户端。
"""

from __future__ import annotations

import csv
import io
import uuid
import zipfile
from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AlarmLog, EventLog, ParameterSnapshot, SamplePoint

LOG_TYPES = ("sample", "event", "alarm", "parameter")


def _rows_to_csv(columns: Sequence[str], rows: Sequence[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _to_dict(obj, columns: Sequence[str]) -> dict:
    return {c: getattr(obj, c, None) for c in columns}


async def export_test_logs(
    session: AsyncSession,
    test_id: str,
    *,
    log_types: Sequence[str] | None = None,
) -> dict:
    """导出指定试验的日志为 zip，返回 {task_id, file_path, size, entries}。"""
    types = [t for t in (log_types or LOG_TYPES) if t in LOG_TYPES]
    settings = get_settings()
    task_id = uuid.uuid4().hex
    zip_path: Path = settings.exports_dir / f"{task_id}.zip"

    entries: list[str] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if "sample" in types:
            cols = [
                "id", "test_id", "ts", "source", "furnace_pv", "furnace_sv", "burden_temp",
                "burden_temp_v", "temp_output_pct", "program_step", "n2_sp", "n2_pv", "co_sp",
                "co_pv", "drip_weight", "delta_p", "delta_p_v", "displacement", "displacement_v",
                "current_state", "safety_relay",
            ]
            rows = (await session.execute(
                select(SamplePoint).where(SamplePoint.test_id == test_id).order_by(SamplePoint.ts)
            )).scalars().all()
            zf.writestr(f"{test_id}_sample_point.csv", _rows_to_csv(cols, [_to_dict(r, cols) for r in rows]))
            entries.append("sample_point.csv")

        if "event" in types:
            cols = ["id", "test_id", "ts", "source", "event_code", "level", "text", "operator_id"]
            rows = (await session.execute(
                select(EventLog).where(EventLog.test_id == test_id).order_by(EventLog.ts)
            )).scalars().all()
            zf.writestr(f"{test_id}_event_log.csv", _rows_to_csv(cols, [_to_dict(r, cols) for r in rows]))
            entries.append("event_log.csv")

        if "alarm" in types:
            cols = ["id", "test_id", "alarm_code", "level", "occur_time", "clear_time", "ack_time", "ack_operator", "text", "latched"]
            rows = (await session.execute(
                select(AlarmLog).where(AlarmLog.test_id == test_id).order_by(AlarmLog.occur_time)
            )).scalars().all()
            zf.writestr(f"{test_id}_alarm_log.csv", _rows_to_csv(cols, [_to_dict(r, cols) for r in rows]))
            entries.append("alarm_log.csv")

        if "parameter" in types:
            cols = ["id", "test_id", "ts", "operator_id", "source", "fw_version", "param_crc", "params_json"]
            rows = (await session.execute(
                select(ParameterSnapshot).where(ParameterSnapshot.test_id == test_id).order_by(ParameterSnapshot.id)
            )).scalars().all()
            zf.writestr(f"{test_id}_parameter_snapshot.csv", _rows_to_csv(cols, [_to_dict(r, cols) for r in rows]))
            entries.append("parameter_snapshot.csv")

    return {
        "task_id": task_id,
        "file_path": str(zip_path),
        "size": zip_path.stat().st_size,
        "entries": entries,
    }


def export_path(task_id: str) -> Path:
    """按 task_id 解析导出文件路径（task_id 为文件名，限制为十六进制防穿越）。"""
    safe = "".join(ch for ch in task_id if ch in "0123456789abcdef")
    return get_settings().exports_dir / f"{safe}.zip"
