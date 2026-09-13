"""One bounded replay worker; database state, not an in-memory queue, is authoritative."""

import asyncio
import json

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.db.cancellation import finish_db_work
from app.db.v2_models import V2RunBinding, V2RunRecovery, V2SourceRecord
from app.services.maintenance_service import MaintenanceBlockedError, maintenance_manager
from app.services.v2_recovery_evidence import discover_run, json_text

logger = get_logger("v2_recovery")


class V2RecoveryWorker:
    def __init__(self, service):
        self.service = service
        self.wake = asyncio.Event()
        self.task = None
        self.bootstrap_cursor = 0
        self.bootstrapped = False

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="v2-run-recovery")
            self.wake.set()

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    @finish_db_work
    async def _pending(self):
        async with self.service.factory() as db:
            return await db.scalar(
                select(V2RunRecovery.id)
                .where(V2RunRecovery.review_state == "bound", V2RunRecovery.replay_status.in_(("pending", "running")))
                .order_by(V2RunRecovery.first_seen_at)
                .limit(1)
            )

    @finish_db_work
    async def _bootstrap_batch(self):
        async with self.service.write_lock, self.service.factory() as db, db.begin():
            await db.execute(
                update(V2RunRecovery).where(V2RunRecovery.id == "").values(last_seen_at=V2RunRecovery.last_seen_at)
            )
            sources = (
                await db.scalars(
                    select(V2SourceRecord)
                    .outerjoin(
                        V2RunBinding,
                        (V2RunBinding.device_id == V2SourceRecord.device_id)
                        & (V2RunBinding.run_id == V2SourceRecord.run_id),
                    )
                    .where(
                        V2SourceRecord.id > self.bootstrap_cursor,
                        V2SourceRecord.run_id.is_not(None),
                        V2RunBinding.run_id.is_(None),
                    )
                    .order_by(V2SourceRecord.id)
                    .limit(100)
                )
            ).all()
            for raw in sources:
                try:
                    payload = json.loads(raw.payload_bytes)
                    if not isinstance(payload, dict):
                        raise ValueError("invalid source object")
                except (ValueError, TypeError):
                    case = await discover_run(
                        db,
                        raw.device_id,
                        raw.run_id,
                        boot_id=raw.boot_id,
                        origin="existing_source",
                        observed_at=raw.received_at,
                    )
                    evidence = json.loads(case.evidence_json)
                    evidence.setdefault("conflicts", []).append(
                        {"source_record_id": raw.id, "reason": "invalid_stored_source"}
                    )
                    case.review_state, case.evidence_json = "conflict", json_text(evidence)
                    case.review_revision += 1
                    continue
                await discover_run(
                    db,
                    raw.device_id,
                    raw.run_id,
                    boot_id=raw.boot_id,
                    origin="existing_source",
                    run=payload.get("run") if payload.get("kind") == "run_changed" else None,
                    observed_at=raw.received_at,
                )
            return sources[-1].id if sources else self.bootstrap_cursor, len(sources) < 100

    async def _run(self):
        backoff = 1
        while True:
            try:
                if not self.bootstrapped:
                    with maintenance_manager.business_guard():
                        self.bootstrap_cursor, self.bootstrapped = await self._bootstrap_batch()
                    if not self.bootstrapped:
                        await asyncio.sleep(0)
                        continue
                recovery_id = await self._pending()
                if recovery_id:
                    with maintenance_manager.business_guard():
                        await self.service.replay_batch(recovery_id)
                    backoff = 1
                    await asyncio.sleep(0)
                    continue
                self.wake.clear()
                # Periodic durable discovery also closes a wake/clear race across processes.
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=5)
                except TimeoutError:
                    pass
                backoff = 1
            except MaintenanceBlockedError:
                # Durable progress resumes after commit removes the maintenance
                # gate. Do not project user-requested replay into a rollback window.
                await asyncio.sleep(1)
            except Exception as exc:
                logger.warning("replay.worker_retry", error_type=type(exc).__name__, retry_delay_s=backoff)
                # Includes lookup/failed-state persistence errors. Never spin on a full/locked DB.
                await asyncio.sleep(backoff)
                backoff = min(30, backoff * 2)
            else:
                await asyncio.sleep(0)
