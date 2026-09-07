"""Transactional projections of immutable v2 sources into the existing experiment archive."""

from __future__ import annotations

import json

from pydantic import TypeAdapter
from sqlalchemy import select

from app.db.models import EventLog, SamplePoint, TestSession
from app.db.v2_models import V2RunBinding, V2SourceRecord
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.messages import RunStatus, StatusSnapshot
from app.hostcomm.v2_contract.types import Identifier
from app.services.v2_alarm_archive import V2AlarmArchive
from app.services.v2_recovery_evidence import discover_run


def _json(value):
    # Existing experiment metadata legitimately contains decimal engineering values.
    # Only wire/source bytes use the stricter fixed-point canonical codec.
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _basis(row):
    try:
        value = json.loads(row.measurement_basis_json or "{}")
        if not isinstance(value, dict) or not isinstance(value.get("v2", {}), dict):
            raise ValueError("invalid archive basis")
    except (ValueError, TypeError):
        value = {"invalid_previous_basis": True, "previous_basis_raw": row.measurement_basis_json}
        row.data_integrity = "incomplete"
    value.setdefault("v2", {})
    return value


def _value(points, name, divisor=1):
    point = points[name]
    return point["value"] / divisor if point["quality"] == "good" and point["value"] is not None else None


def _quality(points, name):
    return 1 if points[name]["quality"] == "good" else 0


class V2ArchiveProjector:
    """Methods never commit, infer an active test, create source samples or update live state.

    Pass on_record to V2SourceLogStore. False means an unknown run remains raw-only and
    can be projected after an explicit binding. reconcile_status expects the validated
    current session's status, with its boot_id; the adapter owns session freshness checks.
    """

    def __init__(self, device_id):
        self.device_id = TypeAdapter(Identifier).validate_python(device_id)
        self.alarms = V2AlarmArchive(self.device_id)

    async def reconcile_alarms(self, db, snapshot, *, boot_id):
        return await self.alarms.reconcile(db, snapshot, boot_id=boot_id)

    async def _bound(self, db, run_id):
        if run_id is None:
            return None, None
        binding = await db.get(V2RunBinding, (self.device_id, run_id))
        if binding is None:
            return None, None
        row = await db.scalar(select(TestSession).where(TestSession.test_id == binding.test_id))
        return binding, row

    async def on_record(self, db, record, origin):
        payload = record["data"]
        source_boot = payload["sample"]["boot_id"] if record["record_type"] == "sample" else record["boot_id"]
        case = await discover_run(
            db,
            self.device_id,
            payload.get("run_id"),
            boot_id=source_boot,
            origin=origin,
            run=payload.get("run") if payload.get("kind") == "run_changed" else None,
            observed_at=record.get("received_at"),
        )
        binding, row = await self._bound(db, payload.get("run_id"))
        is_alarm = record["record_type"] == "event" and payload.get("kind") == "alarm"
        if (row is None or (case and case.review_state == "conflict")) and not is_alarm:
            return False
        received_at = record.get("received_at") or now_iso()
        evidence = {
            "device_id": self.device_id,
            "origin": origin,
            "log_id": record.get("log_id"),
            "record_seq": record.get("record_seq"),
            "received_at": received_at,
        }
        if record["record_type"] == "sample":
            await self._sample(db, row, payload, evidence)
            await self._recover_start_time(db, row)
            return True
        evidence.update(boot_id=record["boot_id"], event=payload)
        db.add(
            EventLog(
                test_id=row.test_id if row else None,
                ts=payload["event_timestamp"] or received_at,
                source=f"hostcomm_v2_{origin}",
                event_code=f"V2-{payload['kind']}",
                level={"info": 0, "warning": 2, "trip": 3}.get(payload.get("severity"), 0),
                text=payload.get("code", payload["kind"]),
                detail_json=_json({"v2": evidence}),
            )
        )
        if is_alarm:
            await self.alarms.event(
                db, payload, boot_id=record["boot_id"], test_id=row.test_id if row else None, origin=origin
            )
        elif payload["kind"] == "run_changed":
            self._run(
                row,
                binding,
                RunStatus.model_validate(payload["run"]).model_dump(),
                boot_id=record["boot_id"],
                observed_at=payload["event_timestamp"] or received_at,
                source="event",
                authoritative=False,
            )
        elif payload["kind"] == "first_drip":
            basis = _basis(row)
            events = basis["v2"].setdefault("first_drip_events", [])
            if not any(event["event_id"] == payload["event_id"] for event in events):
                events.append({"boot_id": record["boot_id"], **payload})
            row.measurement_basis_json = _json(basis)
        if row is not None:
            await self._recover_start_time(db, row)
        return True

    async def _sample(self, db, row, payload, evidence):
        points, reference = payload["values"], payload["sample"]
        evidence.update(
            boot_id=reference["boot_id"],
            telemetry=payload,
            timestamp_basis="device_timestamp" if payload["sample_timestamp"] else "received_at",
        )
        extra = {
            "v2": evidence,
            "measurement": {
                "first_drip": payload["first_drip_latched"],
                "first_drip_valid": payload["first_drip_detector_quality"] == "good",
            },
        }
        db.add(
            SamplePoint(
                test_id=row.test_id,
                ts=payload["sample_timestamp"] or evidence["received_at"],
                source=f"hostcomm_v2_{evidence['origin']}",
                source_boot_id=reference["boot_id"],
                source_sequence=reference["sample_seq"],
                source_run_id=payload["run_id"],
                source_uptime_ms=payload["sample_uptime_ms"],
                furnace_pv=_value(points, "furnace_mc", 1000),
                furnace_sv=_value(points, "furnace_setpoint_mc", 1000),
                burden_temp=_value(points, "burden_mc", 1000),
                burden_temp_v=_quality(points, "burden_mc"),
                n2_sp=_value(points, "n2_setpoint_ml_min", 1000),
                n2_pv=_value(points, "n2_measured_ml_min", 1000),
                co_sp=_value(points, "co_setpoint_ml_min", 1000),
                co_pv=_value(points, "co_measured_ml_min", 1000),
                drip_weight=_value(points, "drip_mass_mg", 1000),
                delta_p=_value(points, "pressure_drop_pa"),
                delta_p_v=_quality(points, "pressure_drop_pa"),
                displacement=_value(points, "displacement_um", 1000),
                displacement_v=_quality(points, "displacement_um"),
                # Telemetry carries no lifecycle state or physical relay feedback; leave those unknown.
                current_state=None,
                safety_relay=None,
                ext_json=_json(extra),
            )
        )

    async def reconcile_status(self, db, snapshot, *, boot_id, received_at=None):
        TypeAdapter(Identifier).validate_python(boot_id)
        status = StatusSnapshot.model_validate(snapshot).model_dump()
        case = await discover_run(
            db,
            self.device_id,
            status["run"]["run_id"],
            boot_id=boot_id,
            origin="status_snapshot",
            run=status["run"],
            observed_at=received_at,
            authoritative=True,
            status=status,
        )
        binding, row = await self._bound(db, status["run"]["run_id"])
        if row is None or (case and case.review_state == "conflict"):
            return False
        self._run(
            row,
            binding,
            status["run"],
            boot_id=boot_id,
            observed_at=received_at or now_iso(),
            source="status_snapshot",
            authoritative=True,
        )
        basis = _basis(row)
        basis["v2"]["status_evidence"] = {"boot_id": boot_id, "received_at": received_at or now_iso(), "status": status}
        row.measurement_basis_json = _json(basis)
        await self._recover_start_time(db, row)
        return True

    async def _recover_start_time(self, db, row):
        basis = _basis(row)
        reference = basis["v2"].get("measurement_start")
        if row.start_time is not None or "recovery" not in basis or reference is None:
            return
        raw = await db.scalar(
            select(V2SourceRecord).where(
                V2SourceRecord.device_id == self.device_id,
                V2SourceRecord.run_id == basis["v2"]["run_id"],
                V2SourceRecord.record_type == "sample",
                V2SourceRecord.boot_id == reference["boot_id"],
                V2SourceRecord.source_seq == reference["sample_seq"],
            )
        )
        if raw:
            row.start_time = json.loads(raw.payload_bytes).get("sample_timestamp")

    def _run(self, row, binding, run, *, boot_id, observed_at, source, authoritative):
        for key, expected in (
            ("recipe_digest", binding.recipe_digest),
            ("safety_profile_digest", binding.profile_digest),
        ):
            if run[key] is not None and expected is not None and run[key] != expected:
                raise ValueError("run configuration conflicts with its durable binding")
            if expected is None and run[key] is not None:
                setattr(binding, "profile_digest" if key == "safety_profile_digest" else key, run[key])
        basis = _basis(row)
        v2 = basis["v2"]
        v2.update(
            run_id=binding.run_id,
            device_id=self.device_id,
            recipe_digest=binding.recipe_digest,
            safety_profile_digest=binding.profile_digest,
        )
        # Proven source boundaries are immutable and may refer to a previous boot.
        for key in ("measurement_start", "measurement_end", "safe_boundary"):
            if run[key] is not None:
                if v2.get(key) is not None and v2[key] != run[key]:
                    raise ValueError(f"run source boundary conflict: {key}")
                v2[key] = run[key]
        same_boot = v2.get("status_boot_id") in {None, boot_id}
        newer = same_boot and int(run["state_revision"]) >= int(v2.get("state_revision", "0"))
        accept_state = newer if same_boot else authoritative
        known_invalid = v2.get("outcome") == "invalid"
        if row.end_time is not None and not run["safe_complete"]:
            accept_state = False
        # Earlier events remain in EventLog and can fill missing immutable boundaries,
        # but cannot undo a fresh snapshot or a terminal archive across boot boundaries.
        if accept_state:
            v2.update(
                status_boot_id=boot_id,
                state_revision=run["state_revision"],
                state=run["state"],
                outcome=run["outcome"],
                fault_revision=run["fault_revision"],
                measurement_complete=run["measurement_complete"],
                safe_complete=run["safe_complete"],
                last_state_source=source,
                last_observed_at=observed_at,
            )
            row.phase = "completed" if run["safe_complete"] else run["state"]
        if run["measurement_complete"]:
            v2["measurement_complete"] = True
            row.measurement_completed_at = row.measurement_completed_at or observed_at
        if known_invalid or run["outcome"] == "invalid":
            row.data_integrity = "incomplete"
            v2["outcome"] = "invalid"
        if run["safe_complete"] and (accept_state or row.end_time is None):
            v2["safe_complete"] = True
            row.safety_completed_at = row.safety_completed_at or observed_at
            row.end_time = row.end_time or observed_at
            row.state_at_end = run["state"]
            row.end_reason = {
                "valid_candidate": "completed",
                "aborted": "operator_stop",
                "invalid": "invalid",
                "pending": "safe_end_incomplete",
            }[v2.get("outcome", run["outcome"])]
            row.phase = "completed"
            if not run["measurement_complete"]:
                row.data_integrity = "incomplete"
        # Neither a source boundary nor current idle status proves all intervening samples.
        row.measurement_basis_json = _json(basis)
