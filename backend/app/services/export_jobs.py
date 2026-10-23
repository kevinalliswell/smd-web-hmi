"""The only retryable task kinds are report generation and local log export."""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.database import get_sessionmaker
from app.services import log_export_service, report_service
from app.services.background_jobs import BackgroundJobManager, background_jobs


async def submit_export(
    kind: str,
    inputs: dict[str, Any],
    operator_id: str,
    *,
    retry_of: str | None = None,
    manager: BackgroundJobManager = background_jobs,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> str:
    if kind not in {"report", "log_export"}:
        raise ValueError("task kind is not retryable")
    factory = factory or get_sessionmaker()
    task_id = uuid.uuid4().hex

    async def run() -> dict[str, Any]:
        import asyncio
        import shutil

        from app.core.config import get_settings
        from app.services.render_client import RenderError

        settings = get_settings()
        directory = settings.reports_dir if kind == "report" else settings.exports_dir
        if (await asyncio.to_thread(shutil.disk_usage, directory)).free < settings.smd_storage_min_free_bytes:
            raise RenderError("insufficient_disk_space")
        async with factory() as db:
            if kind == "report":
                record = await report_service.generate_report(
                    db,
                    inputs["test_id"],
                    operator_id=operator_id,
                    fmt=inputs["format"],
                    options=inputs.get("options"),
                    task_id=task_id,
                )
                notes = json.loads(record.notes)
                return {
                    "id": record.id,
                    "test_id": record.test_id,
                    "generated_at": record.generated_at,
                    "format": record.format,
                    "file_size_bytes": record.file_size_bytes,
                    "sha256": notes["artifact_sha256"],
                    "snapshot_sha256": notes["snapshot_sha256"],
                    "download_url": f"/api/reports/{record.id}/download",
                }
            result = await log_export_service.export_test_logs(
                db, inputs["test_id"], log_types=inputs.get("log_types"), task_id=task_id
            )
        # Hash in a thread, after the streaming read transaction is closed.
        from pathlib import Path

        def digest() -> str:
            with Path(result["file_path"]).open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()

        return {
            "task_id": task_id,
            "entries": result["entries"],
            "size": result["size"],
            "sha256": await asyncio.to_thread(digest),
            "download_url": f"/api/logs/export/{task_id}/download",
        }

    return await manager.submit_durable(
        kind, run, task_id=task_id, operator_id=operator_id, inputs=inputs, retry_of=retry_of, factory=factory
    )
