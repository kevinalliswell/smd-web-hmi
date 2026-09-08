"""Durable v2 command identity and reconciliation; retries never resend side effects."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from app.db.cancellation import finish_db_work
from app.db.v2_models import V2ControllerIdentity, V2LogCursor, V2Operation, V2OperationReview
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.codec import canonical_bytes, command_digest, digest
from app.hostcomm.v2_contract.messages import Command, OperationResult, StatusSnapshot
from app.hostcomm.v2_contract.types import U64, Identifier

MAX_SEQUENCE = 2**64 - 1
COMMAND_ADAPTER = TypeAdapter(Command)


def _json(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def _identifier(value: str) -> str:
    return TypeAdapter(Identifier).validate_python(value)


class V2OperationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@finish_db_work
async def ensure_v2_identity(factory, device_id, controller_id=None, controller_epoch=None, *, write_lock=None):
    """Run before TLS construction. Existing pairing identity is never silently replaced."""
    _identifier(device_id)
    for value in (controller_id, controller_epoch):
        if value is not None:
            _identifier(value)
    async with write_lock or asyncio.Lock():
        async with factory() as db, db.begin():
            # First statement reserves the SQLite writer before reading its uint64 text counter.
            await db.execute(
                insert(V2ControllerIdentity)
                .values(
                    device_id=device_id,
                    controller_id=controller_id or uuid.uuid4().hex,
                    controller_epoch=controller_epoch or uuid.uuid4().hex,
                    last_seq="0",
                    created_at=now_iso(),
                )
                .on_conflict_do_nothing(index_elements=["device_id"])
            )
            row = await db.get(V2ControllerIdentity, device_id)
            if (controller_id is not None and row.controller_id != controller_id) or (
                controller_epoch is not None and row.controller_epoch != controller_epoch
            ):
                raise V2OperationError("identity_conflict", "local maintenance pairing is required")
            return {
                "device_id": row.device_id,
                "controller_id": row.controller_id,
                "controller_epoch": row.controller_epoch,
                "last_seq": row.last_seq,
            }


def operation_dict(row: V2Operation) -> dict:
    return {
        "operation_id": row.operation_id,
        "msg_id": row.msg_id,
        "device_id": row.device_id,
        "controller_epoch": row.controller_epoch,
        "command_seq": row.command_seq,
        "command": row.command,
        "status": row.status,
        "reason": row.reason,
        "request_digest": row.request_digest,
        "request": json.loads(row.request_json),
        "result": json.loads(row.result_json) if row.result_json else None,
        "actor": row.actor,
        "role": row.role,
        "reconciled": bool(row.reconciled),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class V2OperationCoordinator:
    """Use the application session factory and inject its shared short-write lock when available.

    No write lock is held while waiting on network I/O. Database admission, rather than a
    normal-command receipt lock, prevents overlapping ordinary writes and lets stop proceed.
    """

    def __init__(self, factory, transport, *, device_id, controller_id=None, controller_epoch=None, write_lock=None):
        self.factory, self.transport = factory, transport
        self.device_id = _identifier(device_id)
        self.controller_id, self.controller_epoch = controller_id, controller_epoch
        self.write_lock = write_lock or asyncio.Lock()

    @finish_db_work
    async def initialize(self, *, recover=True, board_highwater=None) -> dict:
        identity = await ensure_v2_identity(
            self.factory, self.device_id, self.controller_id, self.controller_epoch, write_lock=self.write_lock
        )
        self.controller_id, self.controller_epoch = identity["controller_id"], identity["controller_epoch"]
        if recover:
            async with self.write_lock, self.factory() as db, db.begin():
                await db.execute(
                    update(V2Operation)
                    .where(
                        V2Operation.device_id == self.device_id, V2Operation.status.in_(["pending", "sent", "accepted"])
                    )
                    .values(status="unknown", reason="backend_restart", reconciled=0, updated_at=now_iso())
                )
        if board_highwater is not None:
            identity["last_seq"] = await self.reconcile_highwater(board_highwater)
        return identity

    @finish_db_work
    async def reconcile_highwater(self, board_highwater: str) -> str:
        board = int(TypeAdapter(U64).validate_python(board_highwater))
        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2ControllerIdentity)
                .where(V2ControllerIdentity.device_id == self.device_id)
                .values(last_seq=V2ControllerIdentity.last_seq)
            )
            row = await db.get(V2ControllerIdentity, self.device_id)
            if row is None or row.controller_epoch != self.controller_epoch:
                raise V2OperationError("identity_conflict", "initialize identity before reconciling")
            row.last_seq = str(max(board, int(row.last_seq)))
            return row.last_seq

    @finish_db_work
    async def get(self, operation_id: str) -> dict | None:
        async with self.factory() as db:
            row = await db.get(V2Operation, _identifier(operation_id))
            return operation_dict(row) if row and row.device_id == self.device_id else None

    @finish_db_work
    async def _intent(self, command, params, actor, role, operation_id, lease_id, state_revision):
        business = digest({"command": command, "params": params, "actor": actor, "role": role})
        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2ControllerIdentity)
                .where(V2ControllerIdentity.device_id == self.device_id)
                .values(last_seq=V2ControllerIdentity.last_seq)
            )
            existing = await db.get(V2Operation, operation_id)
            if existing is not None:
                if existing.device_id != self.device_id or existing.business_digest != business:
                    raise V2OperationError("operation_conflict", "operation identity belongs to a different request")
                return operation_dict(existing), False
            if not self.transport or not self.transport.is_online:
                raise V2OperationError("offline", "a negotiated device session is required")
            if role not in {"operator", "admin", "maintainer"}:
                raise V2OperationError("permission_denied", "control requires an authenticated operator")
            if command == "activate_recipe" and role not in {"admin", "maintainer"}:
                raise V2OperationError("permission_denied", "recipe activation requires maintenance permission")
            if command != "stop_run":
                outstanding = await db.scalar(
                    select(V2Operation.operation_id)
                    .where(
                        V2Operation.device_id == self.device_id,
                        V2Operation.status.in_(
                            ["pending", "sent", "accepted", "unknown", "result_expired", "not_found"]
                        ),
                        V2Operation.reconciled == 0,
                    )
                    .limit(1)
                )
                if outstanding:
                    raise V2OperationError("operation_unresolved", "query and safely reconcile the previous operation")
            identity = await db.get(V2ControllerIdentity, self.device_id)
            if identity is None or identity.controller_epoch != self.controller_epoch:
                raise V2OperationError("identity_conflict", "persistent controller identity is missing")
            sequence = int(identity.last_seq) + 1
            if sequence > MAX_SEQUENCE:
                raise V2OperationError("sequence_exhausted", "maintenance re-pairing is required")
            payload = {
                "operation_id": operation_id,
                "controller_epoch": identity.controller_epoch,
                "command_seq": str(sequence),
                "lease_id": lease_id,
                "expected_boot_id": self.transport.boot_id,
                "expected_state_revision": state_revision,
                "request_digest": "0" * 64,
                "command": command,
                "params": params,
            }
            payload["request_digest"] = command_digest(payload)
            COMMAND_ADAPTER.validate_python(payload)
            identity.last_seq = str(sequence)
            row = V2Operation(
                operation_id=operation_id,
                device_id=self.device_id,
                controller_epoch=identity.controller_epoch,
                command_seq=str(sequence),
                msg_id=uuid.uuid4().hex,
                command=command,
                business_digest=business,
                request_digest=payload["request_digest"],
                actor=actor,
                role=role,
                status="pending",
                reason="intent_committed",
                request_json=_json(payload),
                created_at=now_iso(),
                updated_at=now_iso(),
                reconciled=0,
            )
            db.add(row)
            return operation_dict(row), True

    @finish_db_work
    async def _unknown(self, operation_id, reason, error=None):
        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2Operation)
                .where(
                    V2Operation.operation_id == operation_id,
                    V2Operation.status.not_in(["applied", "rejected", "interrupted"]),
                )
                .values(status="unknown", reason=reason, reconciled=0, updated_at=now_iso())
            )
            if error is not None:
                db.add(
                    V2OperationReview(
                        operation_id=operation_id,
                        actor="system",
                        reason="device_error_response",
                        evidence_json=_json(error),
                        created_at=now_iso(),
                    )
                )

    @finish_db_work
    async def _mark_sent(self, identity):
        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2Operation)
                .where(V2Operation.operation_id == identity)
                .values(status="sent", reason="outcome_pending", updated_at=now_iso())
            )

    async def submit(self, command, params, *, actor, role, operation_id=None, lease_id=None, state_revision=None):
        identity = _identifier(operation_id or uuid.uuid4().hex)
        row, created = await self._intent(command, params, actor, role, identity, lease_id, state_revision)
        if not created:
            return row
        try:
            await self._mark_sent(identity)
            response = await self.transport.request("command", row["request"], msg_id=row["msg_id"])
            await self._record_result(row, response, "command_result")
        except asyncio.CancelledError:
            await self._unknown(identity, "request_cancelled")
            raise
        except Exception as exc:
            await self._unknown(identity, getattr(exc, "code", "outcome_unknown"), getattr(exc, "payload", None))
        return await self.get(identity)

    @finish_db_work
    async def _record_result(self, stored: dict, response: dict, expected_type: str):
        if response.get("type") != expected_type:
            raise V2OperationError("result_mismatch", "unexpected response type")
        result = OperationResult.model_validate(response["payload"]).model_dump()
        if any(result[key] != stored[key] for key in ("operation_id", "controller_epoch", "command_seq")):
            raise V2OperationError("result_mismatch", "device result identity differs from durable intent")
        if result["request_digest"] is not None and result["request_digest"] != stored["request_digest"]:
            raise V2OperationError("result_mismatch", "device result digest differs from durable intent")
        async with self.write_lock, self.factory() as db, db.begin():
            row = await db.get(V2Operation, stored["operation_id"])
            # Cache eviction or a late receipt cannot erase locally proved terminal evidence.
            if row.status in {"applied", "rejected", "interrupted"}:
                if result["status"] in {"accepted", "unknown", "result_expired", "not_found"}:
                    return
                if row.result_json != _json(result):
                    raise V2OperationError("result_conflict", "a retained terminal outcome cannot change")
            row.status, row.reason, row.result_json = result["status"], result["reason"], _json(result)
            if result["status"] == "accepted":
                row.reconciled = 0
            row.updated_at = now_iso()

    async def query(self, operation_id: str) -> dict:
        row = await self.get(operation_id)
        if row is None:
            raise V2OperationError("not_found", "local operation does not exist")
        query = {key: row[key] for key in ("controller_epoch", "operation_id", "command_seq")}
        response = await self.transport.request("get_operation", query)
        await self._record_result(row, response, "operation_snapshot")
        return await self.get(operation_id)

    async def acquire_lease(self, *, actor, role, operation_id=None):
        token = self.transport.lease_token()
        row = await self.submit("acquire_lease", {"lease_ms": 8000}, actor=actor, role=role, operation_id=operation_id)
        result = row["result"] or {}
        if row["status"] == "applied" and result.get("result_boot_id") == self.transport.boot_id:
            frame = await self.transport.request("get_status", {})
            StatusSnapshot.model_validate(frame["payload"])
            self.transport.confirm_lease(frame, token, result.get("lease_id"))
        return row

    @finish_db_work
    async def _applied_lease(self, lease_id):
        async with self.factory() as db:
            rows = await db.scalars(
                select(V2Operation)
                .where(
                    V2Operation.device_id == self.device_id,
                    V2Operation.controller_epoch == self.controller_epoch,
                    V2Operation.command == "acquire_lease",
                    V2Operation.status == "applied",
                )
                .order_by(V2Operation.updated_at.desc())
                .limit(128)
            )
            for row in rows:
                result = OperationResult.model_validate(json.loads(row.result_json))
                request = COMMAND_ADAPTER.validate_python(json.loads(row.request_json))
                if (
                    result.lease_id == lease_id
                    and result.result_boot_id == self.transport.boot_id
                    and request.expected_boot_id == self.transport.boot_id
                ):
                    return True
        return False

    async def confirm_owned_lease(self, frame):
        token = self.transport.lease_token()
        lease_id = frame["payload"]["lease_id"]
        if token.lease_id != lease_id and not await self._applied_lease(lease_id):
            return False
        return self.transport.confirm_lease(frame, token, lease_id)

    async def release_lease(self, *, actor, role, lease_id, state_revision, reason="operator_release"):
        token = await self.transport.quiesce_lease(lease_id)
        applied = False
        try:
            row = await self.submit(
                "release_lease",
                {"reason": reason},
                actor=actor,
                role=role,
                lease_id=lease_id,
                state_revision=state_revision,
            )
            applied = row["status"] == "applied"
            return row
        finally:
            self.transport.finish_release(token, applied=applied)

    async def reconcile_unknown(self, operation_id, *, actor, reason):
        """Read a fresh authenticated snapshot ourselves; never accept an HTTP safety boolean."""
        if not reason.strip() or not self.transport.is_online:
            raise V2OperationError("recovery_evidence_required", "online evidence and an audit reason are required")
        frame = await self.transport.request("get_status", {})
        if frame.get("boot_id") != self.transport.boot_id or frame.get("session_id") != self.transport.session_id:
            raise V2OperationError("stale_session", "recovery snapshot belongs to another session")
        snapshot = StatusSnapshot.model_validate(frame["payload"])
        safety, run = snapshot.safety, snapshot.run
        if (
            run.state not in {"completed", "idle", "fault"}
            or (run.run_id is not None and not run.safe_complete)
            or safety.emergency_stop
            or safety.co_alarm
            or safety.overtemperature
            or not safety.exhaust_ok
            or not safety.profile_approved
            or not safety.hardwired_permit
            or (run.run_id is not None and run.safety_profile_digest != snapshot.profile_digest)
        ):
            raise V2OperationError("unsafe_recovery", "fresh snapshot does not prove safe recovery conditions")
        await self._record_reconciliation(operation_id, actor, reason, frame)
        return await self.get(operation_id)

    @finish_db_work
    async def _record_reconciliation(self, operation_id, actor, reason, frame):
        async with self.write_lock, self.factory() as db, db.begin():
            row = await db.get(V2Operation, operation_id)
            if (
                row is None
                or row.device_id != self.device_id
                or row.status not in {"unknown", "result_expired", "not_found"}
            ):
                raise V2OperationError("state_conflict", "operation is not an uncertain result")
            row.reconciled, row.updated_at = 1, now_iso()
            intervals = (await db.scalars(select(V2LogCursor).where(V2LogCursor.device_id == self.device_id))).all()
            evidence = {
                "status": frame,
                "verified_log_intervals": [
                    {
                        "log_id": item.log_id,
                        "first_record_seq": item.verified_from_seq,
                        "last_record_seq": item.verified_through_seq,
                    }
                    for item in intervals
                ],
            }
            db.add(
                V2OperationReview(
                    operation_id=operation_id,
                    actor=actor,
                    reason=reason,
                    evidence_json=_json(evidence),
                    created_at=now_iso(),
                )
            )
