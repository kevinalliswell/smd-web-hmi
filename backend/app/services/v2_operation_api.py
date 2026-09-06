"""Reconcile HTTP operation evidence with the board without replaying writes."""

import json

from sqlalchemy import select

from app.db.models import EventLog, TestSession
from app.db.v2_models import V2RunBinding
from app.hostcomm.protocol import now_iso
from app.services.command_service import CommandError, audit_action
from app.services.logging_service import append_parameter_snapshot
from app.services.operations import canonical_json, operation_payload
from app.services.v2_operations import V2OperationError


async def _close_rejected_start(db, row, wire):
    """Only a retained rejection proves this reserved run was never accepted.

    Interrupted and audited unknown results still need actual safe-completion evidence.
    Binding checks prevent a historical operation from closing an unrelated session.
    """
    if row.command != "start_test" or wire["command"] != "start_run" or wire["status"] != "rejected":
        return
    run_id = wire["request"]["params"]["run_id"]
    binding = await db.get(V2RunBinding, (wire["device_id"], run_id))
    if binding is None or binding.test_id != json.loads(row.params_json).get("test_id"):
        return
    session = await db.scalar(select(TestSession).where(TestSession.test_id == binding.test_id))
    if session is None or session.end_time is not None:
        return
    try:
        basis = json.loads(session.measurement_basis_json or "{}")
        previous_run = basis.get("v2", {})
        observed_run = previous_run.get("state") or previous_run.get("measurement_start")
    except (ValueError, TypeError, AttributeError):
        return  # Corrupt or conflicting evidence needs investigation, not automatic closure.
    if observed_run or session.measurement_completed_at or session.safety_completed_at:
        return
    session.phase = "start_rejected"
    session.end_time = now_iso()
    session.end_reason = "start_rejected:" + wire["reason"]
    db.add(
        EventLog(
            test_id=session.test_id,
            ts=session.end_time,
            source="hmi",
            event_code="HMI-V2-START-REJECTED",
            level=1,
            text="设备已确认未受理启动，关闭启动预约",
            detail_json=canonical_json(
                {
                    "operation_id": row.operation_id,
                    "wire_operation_id": wire["operation_id"],
                    "wire_result": wire["result"],
                }
            ),
        )
    )


async def query_operation(db, row, client, *, actor=None, reason=None):
    if not client or getattr(client, "protocol_version", None) != "2.0" or not client.is_online:
        raise CommandError(503, "v2_device_unavailable", "当前未连接 HostComm 2 设备")
    wire_id = client.wire_operation_id(row.msg_id)
    # End the read transaction before the coordinator commits device evidence.
    await db.commit()
    existing = await client.operations.get(wire_id)
    prerequisite_only = existing is None
    if prerequisite_only:
        wire_id = client.lease_operation_id(wire_id)
        existing = await client.operations.get(wire_id)
    if existing is None:
        raise CommandError(409, "wire_intent_absent", "尚无该请求的板端命令身份；请检查请求阶段与审计记录")
    try:
        wire = await client.operations.query(wire_id)
        if reason is not None:
            wire = await client.operations.reconcile_unknown(wire_id, actor=actor, reason=reason)
    except V2OperationError as exc:
        raise CommandError(409, exc.code, str(exc)) from exc
    await db.refresh(row)
    if row.status == "verified" and not prerequisite_only:
        # A later recipe activation cannot erase evidence of an earlier readback.
        return {**operation_payload(row), "wire_operation": wire}
    row.updated_at, row.reason_code = now_iso(), wire["reason"]
    response = {
        "result": "unknown",
        "wire_status": wire["status"],
        "wire_operation_id": wire_id,
        "command_seq": wire["command_seq"],
        "wire_reconciled": bool(wire["reconciled"]),
    }
    if prerequisite_only:
        terminal = wire["status"] in {"applied", "rejected", "interrupted"} or wire["reconciled"]
        row.status = response["result"] = "rejected" if terminal else "unknown"
        row.reason_code = "prerequisite_resolved_business_not_sent" if terminal else "prerequisite_outcome_unknown"
        response["prerequisite_only"] = True
    elif wire["status"] in {"accepted", "applied"}:
        row.status, response["result"] = "accepted", "accepted"
        if row.command == "set_parameters" and wire["status"] == "applied":
            parameters = json.loads(row.params_json)
            expected = parameters.get("values", parameters.get("patch"))
            await db.commit()
            readback = await client.get_parameters()
            if readback["params"] == expected:
                await append_parameter_snapshot(
                    db, readback, test_id=None, operator_id=row.operator_id, source="operation_recovery_readback"
                )
                await audit_action(
                    db,
                    operator_id=actor or row.operator_id,
                    role=row.operator_role,
                    action_type="verify_recovered_parameters",
                    params={"operation_id": row.operation_id},
                    result="verified",
                )
                row.status = "verified"
                response.update(readback_ok=True, params=readback["params"])
            else:
                row.status, row.reason_code = "accepted", "applied_recipe_no_longer_current"
                response["readback_ok"] = False
    elif wire["status"] in {"rejected", "interrupted"}:
        row.status, response["result"] = "rejected", "rejected"
    else:
        row.status = "unknown"
    row.result_json = canonical_json(response)
    row.device_result_json = canonical_json(wire["result"])
    if not prerequisite_only:
        await _close_rejected_start(db, row, wire)
    await db.commit()
    return {**operation_payload(row), "wire_operation": wire}
