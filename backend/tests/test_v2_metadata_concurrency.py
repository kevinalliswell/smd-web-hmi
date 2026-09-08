"""Audited metadata edits must preserve independently committed source evidence."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.api.routes import experiment_review as review
from app.db.models import EventLog
from app.db.models import TestSession as Experiment
from app.db.v2_models import V2SourceRecord
from tests.test_v2_run_recovery import bind, discover, message, system  # noqa: F401


def metadata():
    return review.MetadataRequest(
        report_context={"abnormal_operations": "Reviewed synthetic source"}, reason="核查并补录合成资料来源"
    )


async def recovered(system):
    factory, _, _, service = system
    case = await discover(system)
    bound = await bind(service, case)
    await service.replay_batch(case["id"])
    return factory, bound["test_id"]


async def assert_evidence(factory, test_id):
    async with factory() as db:
        row = await db.scalar(select(Experiment).where(Experiment.test_id == test_id))
        basis = json.loads(row.measurement_basis_json)
        assert len(basis["v2"]["first_drip_events"]) == 1
        assert basis["v2"]["first_drip_events"][0]["event_id"] == message("event")["payload"]["event_id"]
        assert basis["report_context"]["abnormal_operations"] == "Reviewed synthetic source"
        assert basis["metadata_revision"] == 1
        assert row.start_time is None
        raw = await db.scalar(select(V2SourceRecord).where(V2SourceRecord.record_type == "event"))
        assert raw.archived == 1 and raw.run_id == basis["v2"]["run_id"]
        audit = await db.scalar(select(EventLog).where(EventLog.event_code == "METADATA_REVISED"))
        assert audit.operator_id == "admin" and audit.text == metadata().reason
        return json.loads(audit.detail_json)


async def test_metadata_refreshes_cached_basis_after_committed_first_drip(system):
    factory, test_id = await recovered(system)
    store = system[2]
    async with factory() as db:
        cached = await db.scalar(select(Experiment).where(Experiment.test_id == test_id))
        assert "first_drip_events" not in json.loads(cached.measurement_basis_json)["v2"]
        await store.ingest_live(message("event"))
        await review.update_metadata(test_id, metadata(), CurrentUser("admin", "admin"), db)
    audit = await assert_evidence(factory, test_id)
    assert len(json.loads(audit["before"])["v2"]["first_drip_events"]) == 1
    assert len(audit["after"]["v2"]["first_drip_events"]) == 1


async def test_source_projection_waits_for_metadata_transaction_and_preserves_both(system, monkeypatch):
    factory, test_id = await recovered(system)
    store = system[2]
    original_get = review._get_test
    source_task = None

    async def source_during_read(db, identity):
        nonlocal source_task
        row = await original_get(db, identity)
        source_task = asyncio.create_task(store.ingest_live(message("event")))
        # Give the independent writer a chance to commit after this read. With
        # the database reservation it must wait until the metadata commit.
        await asyncio.wait({source_task}, timeout=0.1)
        assert not source_task.done()
        return row

    monkeypatch.setattr(review, "_get_test", source_during_read)
    try:
        async with factory() as db:
            await review.update_metadata(test_id, metadata(), CurrentUser("admin", "admin"), db)
        await asyncio.wait_for(asyncio.shield(source_task), 10)
        await assert_evidence(factory, test_id)
    finally:
        if source_task:
            source_task.cancel()
            await asyncio.gather(source_task, return_exceptions=True)


async def test_rejected_metadata_releases_writer_before_request_session_closes(system):
    factory, test_id = await recovered(system)
    async with factory() as db:
        row = await db.scalar(select(Experiment).where(Experiment.test_id == test_id))
        basis = json.loads(row.measurement_basis_json)
        basis["sample_metadata"] = {"h1_mm": 15, "h2_mm": 5}
        row.original_height_mm = 10
        row.measurement_basis_json = before = json.dumps(basis)
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await review.update_metadata(
                test_id,
                review.MetadataRequest(sample_metadata={"h1_mm": 20}, reason="Inconsistent height"),
                CurrentUser("admin", "admin"),
                db,
            )
        assert error.value.status_code == 409
        assert not db.in_transaction()
        async with factory() as check:
            stored = await check.scalar(select(Experiment).where(Experiment.test_id == test_id))
            assert stored.measurement_basis_json == before and stored.original_height_mm == 10
            assert await check.scalar(select(EventLog.id).where(EventLog.event_code == "METADATA_REVISED")) is None
        # The original request session stays open; a separate archive writer must
        # nevertheless finish immediately after the failed metadata transaction.
        await asyncio.wait_for(system[2].ingest_live(message("event")), 10)
