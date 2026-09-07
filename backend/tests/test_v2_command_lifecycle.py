"""Device command identity must not be inferred from the latest historical archive."""

import json
from types import SimpleNamespace

import pytest

from app.db.models import TestSession as Experiment
from app.db.v2_models import V2RunBinding, V2RunRecovery
from app.services.cache import StatusCache
from app.services.command_service import CommandError, CommandService
from app.services.test_runtime import active_test


async def recovered(db, *, test_id="HISTORICAL", state="bound"):
    row = Experiment(
        test_id=test_id,
        operator_id="device-recovery",
        start_time=None,
        discovered_at="2026-09-01T00:00:00Z",
        phase="needs_review",
    )
    db.add_all(
        [
            row,
            V2RunBinding(
                device_id="d" * 32,
                run_id="a" * 32,
                test_id=test_id,
                recipe_digest=None,
                profile_digest=None,
                created_at="2026-09-01T00:00:00Z",
            ),
            V2RunRecovery(
                id="c" * 32,
                device_id="d" * 32,
                run_id="a" * 32,
                test_id=test_id,
                first_seen_at="2026-09-01T00:00:00Z",
                last_seen_at="2026-09-01T00:00:00Z",
                review_revision=2,
                review_state=state,
                evidence_json="{}",
                replay_status="pending",
                replay_through_id=0,
            ),
        ]
    )
    await db.commit()
    return row


async def service(*, version="2.0", may_start=True):
    cache = StatusCache()
    await cache.update(
        {"system": {"current_state": "idle", "can_start_test": may_start}, "comm_quality": "online", "data_fresh": True}
    )
    client = SimpleNamespace(
        protocol_version=version,
        device_id="d" * 32,
        current_run_identity=None,
        is_online=True,
        get_status=cache.get_snapshot,
    )
    return CommandService(client, cache)


async def test_reviewed_historical_recovery_does_not_block_fresh_idle_start(db_session):
    row = await recovered(db_session)
    commands = await service()
    try:
        assert await commands._reserve_start("start_test", {"test_id": "NEW"}, db_session) == "NEW"
        assert row.phase == "needs_review" and row.end_time is None
    finally:
        await commands._release_start("NEW")


@pytest.mark.parametrize("variant", ["legacy", "ordinary", "conflict", "unreviewed", "not_ready"])
async def test_start_exclusion_does_not_hide_other_unresolved_sessions(db_session, variant):
    if variant == "ordinary":
        db_session.add(Experiment(test_id="OLD", operator_id="admin", start_time="2026-09-01T00:00:00Z"))
        await db_session.commit()
    else:
        await recovered(db_session, state=variant if variant in {"conflict", "unreviewed"} else "bound")
    commands = await service(version="1.0" if variant == "legacy" else "2.0", may_start=variant != "not_ready")
    try:
        with pytest.raises(CommandError):
            await commands._reserve_start("start_test", {"test_id": "NEW"}, db_session)
    finally:
        await commands._release_start("NEW")


async def test_unknown_run_stop_never_changes_historical_open_archive(db_session):
    row = await recovered(db_session)
    commands = await service()
    active_test.restore(row.test_id, needs_device_reconcile=True)
    try:
        await commands._handle_lifecycle(
            db_session, "stop_test", {}, "admin", "admin", None, {"result": "accepted", "wire_operation_id": "9" * 32}
        )
        await db_session.refresh(row)
        assert row.stop_requested_at is None and row.phase == "needs_review"
    finally:
        active_test.stop()


@pytest.mark.parametrize("proof", ["matched", "unknown_run", "wrong_device", "changed_digest", "different_request"])
async def test_stop_targets_durable_request_even_when_runtime_points_elsewhere(db_session, proof):
    import uuid

    from app.db.v2_models import V2Operation
    from app.hostcomm.v2_contract.codec import command_digest
    from tests.test_hostcomm_v2_transport import example

    historical = await recovered(db_session)
    actual = Experiment(test_id="ACTUAL", operator_id="admin", start_time="2026-09-01T00:00:00Z", phase="measuring")
    db_session.add_all(
        [
            actual,
            V2RunBinding(
                device_id="d" * 32,
                run_id="b" * 32,
                test_id="ACTUAL",
                recipe_digest=None,
                profile_digest=None,
                created_at="2026-09-01T00:00:00Z",
            ),
        ]
    )
    request = example("stop_run")["payload"]
    msg_id, epoch = "outer-stop-request", "5" * 32
    wire_id = uuid.uuid5(uuid.UUID(hex=epoch), msg_id).hex
    request.update(operation_id=wire_id, controller_epoch=epoch)
    request["params"]["run_id"] = "f" * 32 if proof == "unknown_run" else "b" * 32
    request["request_digest"] = command_digest(request)
    db_session.add(
        V2Operation(
            operation_id=wire_id,
            device_id="e" * 32 if proof == "wrong_device" else "d" * 32,
            controller_epoch=epoch,
            command_seq=request["command_seq"],
            msg_id="8" * 32,
            command="stop_run",
            business_digest="1" * 64,
            request_digest=request["request_digest"],
            actor="admin",
            role="admin",
            status="applied",
            reason="ok",
            request_json=(
                json.dumps(request)
                if proof != "changed_digest"
                else json.dumps({**request, "request_digest": "0" * 64})
            ),
            created_at="2026-09-01T00:00:00Z",
            updated_at="2026-09-01T00:00:00Z",
        )
    )
    await db_session.commit()
    commands = await service()
    active_test.restore(historical.test_id, needs_device_reconcile=True)
    try:
        await commands._handle_lifecycle(
            db_session,
            "stop_test",
            {},
            "admin",
            "admin",
            None,
            {"result": "accepted", "wire_operation_id": wire_id},
            operation_msg_id="unrelated-request" if proof == "different_request" else msg_id,
        )
        await db_session.refresh(historical)
        await db_session.refresh(actual)
        assert historical.phase == "needs_review" and historical.stop_requested_at is None
        assert (actual.stop_requested_at is not None) is (proof == "matched")
    finally:
        active_test.stop()
