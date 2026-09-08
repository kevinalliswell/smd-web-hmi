"""日志导出服务（规格 3.7）。

将上位机已归档的某次试验数据（sample_point / event_log / alarm_log /
parameter_snapshot）导出为 CSV 并打包为 zip，落盘到 exports 目录。

注：协议第 10 节描述的是从 STM32 分块导出"设备端"日志（CSV/Base64 分块）；
本服务导出的是**上位机数据库已归档**的数据，便于追溯归档。设备端分块导出
（log_request/log_chunk/log_result）留待后续接入 HostComm 客户端。
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import uuid
import zipfile
from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AlarmLog, EventLog, ParameterSnapshot, SamplePoint
from app.services.recovery_queries import recovery_log_condition
from app.services.test_id import validate_test_id

LOG_TYPES = ("sample", "event", "alarm", "parameter")
EXPORT_BATCH_SIZE = 1000
MAX_EXPORT_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
_TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ExportSizeLimitError(ValueError):
    """导出内容超过允许的未压缩体积。"""


async def _to_thread_without_abandon(func, /, *args, **kwargs):
    """取消调用方时先等线程结束，避免并发关闭其正在写入的 ZIP 句柄。"""
    worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        finally:
            raise


def _rows_to_csv(columns: Sequence[str], rows: Sequence[dict], *, include_header: bool = True) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    if include_header:
        writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _to_dict(obj, columns: Sequence[str]) -> dict:
    return {c: getattr(obj, c, None) for c in columns}


def _validate_task_id(task_id: str) -> str:
    if _TASK_ID_RE.fullmatch(task_id) is None:
        raise ValueError("invalid export task id")
    return task_id


async def _stream_csv_entry(
    session: AsyncSession,
    archive: zipfile.ZipFile,
    filename: str,
    columns: Sequence[str],
    statement,
    *,
    uncompressed_bytes: int,
    max_uncompressed_bytes: int,
) -> int:
    """流式查询 ORM 行，分批编码并压入单个 ZIP 条目。"""
    stream = await session.stream_scalars(statement.execution_options(yield_per=EXPORT_BATCH_SIZE))
    entry = archive.open(filename, "w")
    batch: list[object] = []
    include_header = True

    async def flush() -> None:
        nonlocal uncompressed_bytes, include_header
        encoded = await _to_thread_without_abandon(
            _rows_to_csv,
            columns,
            [_to_dict(row, columns) for row in batch],
            include_header=include_header,
        )
        data = encoded.encode("utf-8")
        uncompressed_bytes += len(data)
        if uncompressed_bytes > max_uncompressed_bytes:
            raise ExportSizeLimitError("导出内容超过 250 MiB 上限")
        await _to_thread_without_abandon(entry.write, data)
        include_header = False
        batch.clear()

    try:
        async for row in stream:
            batch.append(row)
            if len(batch) >= EXPORT_BATCH_SIZE:
                await flush()
        if batch or include_header:
            await flush()
    finally:
        await stream.close()
        await _to_thread_without_abandon(entry.close)
    return uncompressed_bytes


async def export_test_logs(
    session: AsyncSession,
    test_id: str,
    *,
    log_types: Sequence[str] | None = None,
    task_id: str | None = None,
    max_uncompressed_bytes: int = MAX_EXPORT_UNCOMPRESSED_BYTES,
) -> dict:
    """导出指定试验的日志为 zip，返回 {task_id, file_path, size, entries}。"""
    test_id = validate_test_id(test_id)
    types = [t for t in (log_types or LOG_TYPES) if t in LOG_TYPES]
    task_id = _validate_task_id(task_id or uuid.uuid4().hex)
    zip_path = export_path(task_id)

    entries: list[str] = []
    uncompressed_bytes = 0
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            definitions = [
                (
                    "sample",
                    SamplePoint,
                    "ts",
                    [
                        "id",
                        "test_id",
                        "ts",
                        "source",
                        "furnace_pv",
                        "furnace_sv",
                        "burden_temp",
                        "burden_temp_v",
                        "temp_output_pct",
                        "program_step",
                        "n2_sp",
                        "n2_pv",
                        "co_sp",
                        "co_pv",
                        "drip_weight",
                        "delta_p",
                        "delta_p_v",
                        "displacement",
                        "displacement_v",
                        "current_state",
                        "safety_relay",
                    ],
                    "sample_point.csv",
                ),
                (
                    "event",
                    EventLog,
                    "ts",
                    ["id", "test_id", "ts", "source", "event_code", "level", "text", "operator_id"],
                    "event_log.csv",
                ),
                (
                    "alarm",
                    AlarmLog,
                    "occur_time",
                    [
                        "id",
                        "test_id",
                        "alarm_code",
                        "level",
                        "occur_time",
                        "clear_time",
                        "ack_time",
                        "ack_operator",
                        "text",
                        "latched",
                    ],
                    "alarm_log.csv",
                ),
                (
                    "parameter",
                    ParameterSnapshot,
                    "id",
                    ["id", "test_id", "ts", "operator_id", "source", "fw_version", "param_crc", "params_json"],
                    "parameter_snapshot.csv",
                ),
            ]
            for log_type, model, order_column, columns, suffix in definitions:
                if log_type not in types:
                    continue
                condition = (
                    recovery_log_condition(model, test_id)
                    if model in (EventLog, AlarmLog)
                    else model.test_id == test_id
                )
                statement = select(model).where(condition).order_by(getattr(model, order_column), model.id)
                uncompressed_bytes = await _stream_csv_entry(
                    session,
                    archive,
                    f"{test_id}_{suffix}",
                    columns,
                    statement,
                    uncompressed_bytes=uncompressed_bytes,
                    max_uncompressed_bytes=max_uncompressed_bytes,
                )
                entries.append(suffix)
    except BaseException:
        zip_path.unlink(missing_ok=True)
        raise

    return {
        "task_id": task_id,
        "file_path": str(zip_path),
        "size": zip_path.stat().st_size,
        "entries": entries,
    }


def export_path(task_id: str) -> Path:
    """按 task_id 解析导出文件路径（task_id 为文件名，限制为十六进制防穿越）。"""
    safe = _validate_task_id(task_id)
    exports_dir = get_settings().exports_dir.resolve()
    path = (exports_dir / f"{safe}.zip").resolve()
    if not path.is_relative_to(exports_dir):
        raise ValueError("invalid export path")
    return path
