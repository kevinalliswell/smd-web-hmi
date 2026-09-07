"""Reviewed unknown-run association and crash-resumable transactional archive replay."""

import asyncio
import hashlib
import json
import uuid

from pydantic import TypeAdapter
from sqlalchemy import case as sql_case
from sqlalchemy import func, select, update

from app.db.cancellation import finish_db_work
from app.db.models import EventLog, SamplePoint, TestSession
from app.db.operation_models import Operation
from app.db.v2_models import (
    V2AlarmProjection,
    V2LogGap,
    V2Operation,
    V2RecipeBinding,
    V2RecoveryAssociation,
    V2RecoveryReview,
    V2RunBinding,
    V2RunRecovery,
    V2SourceRecord,
)
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.codec import command_digest, digest
from app.hostcomm.v2_contract.messages import Command
from app.services.v2_recovery_evidence import json_text, summary


class V2RecoveryError(RuntimeError):
    def __init__(self, code, message, status_code=409):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status_code = code, message, status_code


async def require_recovery_report_ready(db, test_id):
    state = (
        await db.execute(
            select(V2RunRecovery.review_state, V2RunRecovery.replay_status).where(V2RunRecovery.test_id == test_id)
        )
    ).first()
    if state and (state.review_state != "bound" or state.replay_status != "complete"):
        raise V2RecoveryError("recovery_replay_pending", "运行归属或原始数据回放尚未核查完成，暂不能生成最终报告")


@finish_db_work
async def recovery_for_run(factory, device_id, run_id):
    async with factory() as db:
        row = await db.scalar(
            select(V2RunRecovery).where(V2RunRecovery.device_id == device_id, V2RunRecovery.run_id == run_id)
        )
        return summary(row) if row else None


class V2RunRecoveryService:
    def __init__(self, factory, *, write_lock=None):
        self.factory = factory
        self.write_lock = write_lock or asyncio.Lock()

    @finish_db_work
    async def list(self, *, limit=100, offset=0):
        async with self.factory() as db:
            rows = await db.scalars(
                select(V2RunRecovery).order_by(V2RunRecovery.first_seen_at.desc()).limit(limit).offset(offset)
            )
            return [summary(row) for row in rows]

    @finish_db_work
    async def detail(self, recovery_id):
        async with self.factory() as db:
            case = await self._case(db, recovery_id)
            result = summary(case)
            experiment = (
                await db.scalar(select(TestSession).where(TestSession.test_id == case.test_id))
                if case.test_id
                else None
            )
            result["start_time"] = experiment.start_time if experiment else None
            result["discovered_at"] = experiment.discovered_at if experiment else case.first_seen_at
            source = (V2SourceRecord.device_id == case.device_id, V2SourceRecord.run_id == case.run_id)
            result["source_count"] = await db.scalar(select(func.count()).select_from(V2SourceRecord).where(*source))
            log_ids = select(V2SourceRecord.log_id).where(*source, V2SourceRecord.log_id.is_not(None))
            gaps = await db.scalars(
                select(V2LogGap)
                .where(V2LogGap.device_id == case.device_id, V2LogGap.log_id.in_(log_ids))
                .order_by(V2LogGap.id.desc())
                .limit(100)
            )
            # Gaps are log-wide evidence, not a claim that every gap intersects this run.
            result["log_gaps"] = [
                {
                    "log_id": g.log_id,
                    "first_record_seq": g.first_record_seq,
                    "last_record_seq": g.last_record_seq,
                    "reason": g.reason,
                }
                for g in gaps
            ]
            reviews = await db.scalars(
                select(V2RecoveryReview)
                .where(V2RecoveryReview.recovery_id == case.id)
                .order_by(V2RecoveryReview.id.desc())
                .limit(100)
            )
            result["reviews"] = [
                {
                    "actor": r.actor,
                    "role": r.role,
                    "reason": r.reason,
                    "action": r.action,
                    "created_at": r.created_at,
                    "evidence": json.loads(r.evidence_json),
                }
                for r in reviews
            ]
            return result

    async def _case(self, db, recovery_id):
        case = await db.get(V2RunRecovery, recovery_id)
        if case is None:
            raise V2RecoveryError("recovery_not_found", "未找到运行恢复记录", 404)
        return case

    async def _review(
        self, db, case, *, action, actor, role, reason, idempotency_key, expected_review_revision, target_test_id=None
    ):
        if role not in {"admin", "maintainer"}:
            raise V2RecoveryError("recovery_role_required", "仅管理员或维护员可核查运行归属", 403)
        if not reason or not 2 <= len(reason.strip()) <= 2000 or not 1 <= len(idempotency_key) <= 128:
            raise V2RecoveryError("recovery_review_invalid", "须填写核查原因及有效的请求标识", 422)
        body = dict(
            action=action,
            actor=actor,
            role=role,
            reason=reason,
            expected_review_revision=expected_review_revision,
            target_test_id=target_test_id,
        )
        digest = hashlib.sha256(json_text(body).encode("utf-8")).hexdigest()
        previous = await db.scalar(
            select(V2RecoveryReview).where(
                V2RecoveryReview.recovery_id == case.id, V2RecoveryReview.idempotency_key == idempotency_key
            )
        )
        if previous:
            if previous.request_digest != digest:
                raise V2RecoveryError("recovery_idempotency_conflict", "同一请求标识对应不同核查内容")
            return previous, True
        if case.review_revision != expected_review_revision:
            raise V2RecoveryError("recovery_revision_conflict", "证据已更新，请重新读取后核查")
        if case.review_state == "conflict":
            raise V2RecoveryError("recovery_evidence_conflict", "原始运行边界或配置摘要相互矛盾，禁止覆盖")
        review = V2RecoveryReview(
            recovery_id=case.id,
            idempotency_key=idempotency_key,
            request_digest=digest,
            actor=actor,
            role=role,
            reason=reason,
            action=action,
            evidence_json=case.evidence_json,
            result_json="{}",
            created_at=now_iso(),
        )
        db.add(review)
        return review, False

    @finish_db_work
    async def bind(
        self, recovery_id, *, actor, role, reason, idempotency_key, expected_review_revision, target_test_id=None
    ):
        async with self.write_lock, self.factory() as db, db.begin():
            # Reserve SQLite's writer before reading evidence/version, even across service instances.
            await db.execute(
                update(V2RunRecovery)
                .where(V2RunRecovery.id == recovery_id)
                .values(last_seen_at=V2RunRecovery.last_seen_at)
            )
            case = await self._case(db, recovery_id)
            review, duplicate = await self._review(
                db,
                case,
                action="binding",
                actor=actor,
                role=role,
                reason=reason,
                idempotency_key=idempotency_key,
                expected_review_revision=expected_review_revision,
                target_test_id=target_test_id,
            )
            if duplicate:
                return json.loads(review.result_json)
            if case.test_id is not None or await db.get(V2RunBinding, (case.device_id, case.run_id)):
                raise V2RecoveryError("recovery_already_bound", "运行已绑定，不能重新分配历史记录")
            evidence = json.loads(case.evidence_json)
            if target_test_id is not None:
                row = await self._existing_target(db, case, target_test_id)
            else:
                row = TestSession(
                    test_id=f"REC-{case.id}",
                    operator_id="device-recovery",
                    start_time=None,
                    discovered_at=case.first_seen_at,
                    phase="needs_review",
                    mode="unknown",
                    data_integrity="unknown",
                )
                db.add(row)
            basis = json.loads(row.measurement_basis_json or "{}")
            basis["recovery"] = {
                "id": case.id,
                "device_id": case.device_id,
                "run_id": case.run_id,
                "discovered_at": case.first_seen_at,
                "reviewed_by": actor,
                "metadata_origin": "unknown" if target_test_id is None else "persisted_start_request",
                "limitations": ["样品条件、真实开始时间和配方内容不得由发现时间或当前设备配置推定"],
            }
            row.measurement_basis_json = json_text(basis)
            recipe = (
                await db.get(V2RecipeBinding, (case.device_id, evidence.get("recipe_digest")))
                if evidence.get("recipe_digest")
                else None
            )
            if target_test_id is None and recipe:
                # Matching immutable recipe evidence is useful, but does not establish a GB mode or specimen.
                row.recipe_snapshot_json = json_text(
                    {
                        "wire_recipe": json.loads(recipe.recipe_json),
                        "recipe_digest": recipe.recipe_digest,
                        "source_digest": recipe.source_digest,
                    }
                )
            db.add(
                V2RunBinding(
                    device_id=case.device_id,
                    run_id=case.run_id,
                    test_id=row.test_id,
                    recipe_digest=evidence.get("recipe_digest"),
                    profile_digest=evidence.get("safety_profile_digest"),
                    created_at=now_iso(),
                )
            )
            case.test_id, case.review_state, case.replay_status = row.test_id, "bound", "pending"
            case.review_revision += 1
            await db.flush()
            result = summary(case)
            review.result_json = json_text(result)
            return result

    async def _existing_target(self, db, case, test_id):
        row = await db.scalar(select(TestSession).where(TestSession.test_id == test_id))
        if row is None or await db.scalar(select(V2RunBinding).where(V2RunBinding.test_id == test_id)):
            raise V2RecoveryError("recovery_target_proof_missing", "目标记录不存在或已经绑定其他运行")
        try:
            snapshot = json.loads(row.recipe_snapshot_json or "{}")
            outer = await db.get(Operation, snapshot.get("operation_id", ""))
            if outer is None or outer.command != "start_test":
                raise ValueError("missing original start operation")
            params = json.loads(outer.params_json)
            if (
                params.get("test_id") != test_id
                or hashlib.sha256(json_text({"command": outer.command, "params": params}).encode("utf-8")).hexdigest()
                != outer.request_hash
            ):
                raise ValueError("original HTTP request mismatch")
            source = sql_case((func.json_valid(V2Operation.request_json) == 1, V2Operation.request_json), else_="{}")
            wires = (
                await db.scalars(
                    select(V2Operation)
                    .where(
                        V2Operation.device_id == case.device_id,
                        V2Operation.command == "start_run",
                        func.json_extract(source, "$.params.run_id") == case.run_id,
                    )
                    .limit(2)
                )
            ).all()
            if len(wires) != 1:
                raise ValueError("missing or ambiguous run request")
            wire = wires[0]
            request = TypeAdapter(Command).validate_json(wire.request_json).model_dump(mode="json")
            evidence = json.loads(case.evidence_json)
            if (
                request["operation_id"] != wire.operation_id
                or command_digest(request) != wire.request_digest
                or request["request_digest"] != wire.request_digest
                or request["controller_epoch"] != wire.controller_epoch
                or request["command_seq"] != wire.command_seq
                or request["command"] != wire.command
                or wire.actor != outer.operator_id
                or wire.role != outer.operator_role
                or uuid.uuid5(uuid.UUID(hex=wire.controller_epoch), outer.msg_id).hex != wire.operation_id
                or digest(
                    {"command": wire.command, "params": request["params"], "actor": wire.actor, "role": wire.role}
                )
                != wire.business_digest
                or any(
                    evidence.get(key) is not None and request["params"][key] != evidence[key]
                    for key in ("recipe_digest", "safety_profile_digest")
                )
            ):
                raise ValueError("durable start proof conflict")
            return row
        except (ValueError, KeyError, TypeError) as exc:
            raise V2RecoveryError(
                "recovery_target_proof_missing", "缺少精确匹配设备、运行和原始启动请求的持久证据"
            ) from exc

    @finish_db_work
    async def request_replay(self, recovery_id, **review_fields):
        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2RunRecovery)
                .where(V2RunRecovery.id == recovery_id)
                .values(last_seen_at=V2RunRecovery.last_seen_at)
            )
            case = await self._case(db, recovery_id)
            review, duplicate = await self._review(db, case, action="replay", **review_fields)
            if duplicate:
                return json.loads(review.result_json)
            if case.test_id is None:
                raise V2RecoveryError("recovery_binding_required", "请先核查并绑定运行归属")
            case.replay_status, case.replay_error = "pending", None
            case.review_revision += 1
            review.result_json = json_text(summary(case))
            return summary(case)

    async def replay_batch(self, recovery_id, *, batch_size=100):
        try:
            return await self._replay_batch(recovery_id, batch_size=min(max(batch_size, 1), 200))
        except Exception as exc:
            await self._failed(recovery_id, type(exc).__name__)
            raise

    @finish_db_work
    async def _failed(self, recovery_id, code):
        async with self.write_lock, self.factory() as db, db.begin():
            case = await self._case(db, recovery_id)
            case.replay_status, case.replay_error = "failed", code

    @finish_db_work
    async def _replay_batch(self, recovery_id, *, batch_size):
        from app.services.v2_archive import V2ArchiveProjector

        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2RunRecovery)
                .where(V2RunRecovery.id == recovery_id)
                .values(last_seen_at=V2RunRecovery.last_seen_at)
            )
            case = await self._case(db, recovery_id)
            if case.review_state != "bound" or not case.test_id:
                raise V2RecoveryError("recovery_binding_required", "运行归属尚未核查或存在冲突")
            projector = V2ArchiveProjector(case.device_id)
            binding = await db.get(V2RunBinding, (case.device_id, case.run_id))
            row = await db.scalar(select(TestSession).where(TestSession.test_id == case.test_id))
            case.replay_status, case.replay_error = "running", None
            records = (
                await db.scalars(
                    select(V2SourceRecord)
                    .where(
                        V2SourceRecord.device_id == case.device_id,
                        V2SourceRecord.run_id == case.run_id,
                        V2SourceRecord.id > case.replay_through_id,
                    )
                    .order_by(V2SourceRecord.id)
                    .limit(batch_size)
                )
            ).all()
            for raw in records:
                record = {
                    "record_type": raw.record_type,
                    "boot_id": raw.boot_id,
                    "data": json.loads(raw.payload_bytes),
                    "log_id": raw.log_id,
                    "record_seq": raw.record_seq,
                    "received_at": raw.received_at,
                }
                if not raw.archived:
                    if await projector.on_record(db, record, "backfill") is not False:
                        raw.archived = 1
                elif raw.record_type == "event" and record["data"].get("kind") == "alarm":
                    await self._associate_alarm(db, case, raw, record["data"])
                if case.review_state == "conflict":
                    break
                case.replay_through_id = raw.id
            evidence = json.loads(case.evidence_json)
            latest = evidence.get("latest_run")
            if latest and case.review_state != "conflict":
                projector._run(
                    row,
                    binding,
                    latest["run"],
                    boot_id=latest["boot_id"],
                    observed_at=latest["observed_at"],
                    source="recovered_status",
                    authoritative=True,
                )
            await self._original_start(db, row, case)
            case.replay_status = "complete" if len(records) < batch_size else "pending"
            if case.review_state == "conflict":
                case.replay_status, case.replay_error = "failed", "recovery_evidence_conflict"
            basis = json.loads(row.measurement_basis_json or "{}")
            basis.setdefault("recovery", {}).update(
                replay_status=case.replay_status,
                replay_through_id=case.replay_through_id,
                review_revision=case.review_revision,
            )
            row.measurement_basis_json = json_text(basis)
            return summary(case)

    async def _original_start(self, db, row, case):
        ref = json.loads(case.evidence_json).get("measurement_start")
        if row.start_time is not None or not ref:
            return
        raw = await db.scalar(
            select(V2SourceRecord).where(
                V2SourceRecord.device_id == case.device_id,
                V2SourceRecord.run_id == case.run_id,
                V2SourceRecord.record_type == "sample",
                V2SourceRecord.boot_id == ref["boot_id"],
                V2SourceRecord.source_seq == ref["sample_seq"],
            )
        )
        if raw:
            # Reception time is never a substitute for the original acquisition timestamp.
            row.start_time = json.loads(raw.payload_bytes).get("sample_timestamp")

    async def _associate_alarm(self, db, case, raw, payload):
        if await db.get(V2RecoveryAssociation, (case.id, raw.id)):
            return
        source = sql_case((func.json_valid(EventLog.detail_json) == 1, EventLog.detail_json), else_="{}")
        events = (
            await db.scalars(
                select(EventLog)
                .where(
                    EventLog.test_id.is_(None),
                    EventLog.event_code == "V2-alarm",
                    func.json_extract(source, "$.v2.device_id") == case.device_id,
                    func.json_extract(source, "$.v2.event.event_id") == raw.event_id,
                )
                .limit(2)
            )
        ).all()
        if len(events) > 1:
            raise V2RecoveryError("recovery_alarm_identity_conflict", "同一原始报警事件出现多条历史投影，禁止猜测关联")
        event = events[0] if events else None
        alarm = await db.get(V2AlarmProjection, (case.device_id, payload["alarm_id"], payload["occurrence_seq"]))
        if event or alarm:
            db.add(
                V2RecoveryAssociation(
                    recovery_id=case.id,
                    source_record_id=raw.id,
                    event_log_id=event.id if event else None,
                    alarm_log_id=alarm.alarm_log_id if alarm else None,
                )
            )
