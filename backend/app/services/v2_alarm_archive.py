"""Monotonic alarm occurrence projections and fixed-revision reconciliation evidence."""

import json

from pydantic import TypeAdapter
from sqlalchemy import select

from app.db.models import AlarmLog
from app.db.v2_models import V2AlarmProjection, V2AlarmSnapshot
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.codec import canonical_bytes
from app.hostcomm.v2_contract.messages import AlarmOccurrence
from app.hostcomm.v2_contract.types import U64, Identifier


def _json(value):
    return canonical_bytes(value).decode("utf-8")


class V2AlarmArchive:
    def __init__(self, device_id):
        self.device_id = device_id

    async def _new(self, db, payload, *, test_id, raised_at, evidence, boot_id, event_seq, uptime):
        alarm = AlarmLog(
            test_id=test_id,
            alarm_code=payload["code"],
            level={"info": 0, "warning": 2, "trip": 3}[payload["severity"]],
            occur_time=raised_at or now_iso(),
            text=payload["code"],
            latched=int(payload["severity"] == "trip"),
        )
        db.add(alarm)
        await db.flush()
        projection = V2AlarmProjection(
            device_id=self.device_id,
            alarm_id=payload["alarm_id"],
            occurrence_seq=payload["occurrence_seq"],
            alarm_log_id=alarm.id,
            latest_boot_id=boot_id,
            latest_event_seq=event_seq,
            latest_uptime_ms=uptime,
            active=1,
            acknowledged=0,
            source_json=_json(evidence),
        )
        db.add(projection)
        return projection, alarm

    @staticmethod
    def _apply(projection, alarm, payload, *, at, evidence):
        # An occurrence never becomes active/unacknowledged again. A new occurrence needs a new identity.
        if payload["code"] != alarm.alarm_code:
            raise ValueError("alarm occurrence code conflict")
        if not payload["active"]:
            projection.active = 0
            alarm.clear_time = alarm.clear_time or at
        if payload["acknowledged"]:
            projection.acknowledged = 1
            alarm.ack_time = alarm.ack_time or at
        projection.source_json = _json(evidence)

    async def event(self, db, payload, *, boot_id, test_id, origin):
        key = (self.device_id, payload["alarm_id"], payload["occurrence_seq"])
        projection = await db.get(V2AlarmProjection, key)
        evidence = {
            "source": origin,
            "boot_id": boot_id,
            "event": payload,
            "occur_time_basis": "raised_event" if payload["transition"] == "raised" else "first_observed",
        }
        at = payload["event_timestamp"] or now_iso()
        if projection is None:
            projection, alarm = await self._new(
                db,
                payload,
                test_id=test_id,
                raised_at=at,
                evidence=evidence,
                boot_id=boot_id,
                event_seq=payload["event_seq"],
                uptime=payload["event_uptime_ms"],
            )
        else:
            alarm = await db.get(AlarmLog, projection.alarm_log_id)
            if alarm is None:
                raise ValueError("alarm projection refers to a missing archive")
            # Replay from a previous boot never rewrites the current view of an existing occurrence.
            if origin == "backfill" and projection.latest_boot_id != boot_id:
                return
            if (
                projection.latest_boot_id == boot_id
                and projection.latest_event_seq is not None
                and int(payload["event_seq"]) <= int(projection.latest_event_seq)
            ):
                return
            if (
                projection.latest_boot_id == boot_id
                and projection.latest_uptime_ms is not None
                and int(payload["event_uptime_ms"]) < int(projection.latest_uptime_ms)
            ):
                return
        if payload["transition"] == "raised":
            projection.raised_boot_id = projection.raised_boot_id or boot_id
            projection.raised_event_seq = projection.raised_event_seq or payload["event_seq"]
        self._apply(projection, alarm, payload, at=at, evidence=evidence)
        projection.latest_boot_id, projection.latest_event_seq = boot_id, payload["event_seq"]
        projection.latest_uptime_ms = payload["event_uptime_ms"]
        if origin == "backfill":
            latest = await db.scalar(
                select(V2AlarmSnapshot)
                .where(V2AlarmSnapshot.device_id == self.device_id)
                .order_by(V2AlarmSnapshot.id.desc())
                .limit(1)
            )
            if (
                latest
                and latest.snapshot_uptime_ms is not None
                and (latest.boot_id != boot_id or int(latest.snapshot_uptime_ms) > int(payload["event_uptime_ms"]))
            ):
                current = next(
                    (
                        item
                        for item in json.loads(latest.payload_json)["items"]
                        if (item["alarm_id"], item["occurrence_seq"]) == key[1:]
                    ),
                    None,
                )
                self._apply(
                    projection,
                    alarm,
                    current or {"code": alarm.alarm_code, "active": False, "acknowledged": True},
                    at=latest.created_at,
                    evidence={
                        "source": "fixed_alarm_snapshot",
                        "boot_id": latest.boot_id,
                        "revision": latest.revision,
                        "historical_event_reconciled": payload,
                    },
                )
                projection.latest_boot_id, projection.latest_uptime_ms = latest.boot_id, latest.snapshot_uptime_ms
                projection.latest_event_seq = None

    async def reconcile(self, db, snapshot, *, boot_id):
        """Caller proves this is the current boot. Absence requires a complete timed snapshot."""
        TypeAdapter(Identifier).validate_python(boot_id)
        if snapshot.get("boot_id", boot_id) != boot_id:
            raise ValueError("alarm snapshot boot mismatch")
        revision = TypeAdapter(U64).validate_python(snapshot["revision"])
        uptime = snapshot.get("snapshot_uptime_ms")
        if uptime is not None:
            TypeAdapter(U64).validate_python(uptime)
        items = [AlarmOccurrence.model_validate(item).model_dump() for item in snapshot["items"]]
        keys = {(item["alarm_id"], item["occurrence_seq"]) for item in items}
        if len(keys) != len(items):
            raise ValueError("duplicate alarm occurrence in complete snapshot")
        payload = {"revision": revision, "items": items}
        previous = (
            await db.scalars(
                select(V2AlarmSnapshot).where(
                    V2AlarmSnapshot.device_id == self.device_id, V2AlarmSnapshot.boot_id == boot_id
                )
            )
        ).all()
        if any(int(row.revision) > int(revision) for row in previous):
            return False
        existing = next((row for row in previous if row.revision == revision), None)
        if existing is not None:
            if existing.payload_json != _json(payload):
                raise ValueError("same-revision alarm snapshot conflict")
            return False
        at = now_iso()
        db.add(
            V2AlarmSnapshot(
                device_id=self.device_id,
                boot_id=boot_id,
                revision=revision,
                snapshot_uptime_ms=uptime,
                payload_json=_json(payload),
                created_at=at,
            )
        )
        evidence = {
            "source": "fixed_alarm_snapshot",
            "boot_id": boot_id,
            "revision": revision,
            "snapshot_uptime_ms": uptime,
            "observed_at": at,
        }
        for item in items:
            projection = await db.get(V2AlarmProjection, (self.device_id, item["alarm_id"], item["occurrence_seq"]))
            item_evidence = {
                **evidence,
                "occurrence": item,
                "occur_time_basis": "raised_source" if item["raised_timestamp"] else "first_observed",
            }
            if projection is None:
                projection, alarm = await self._new(
                    db,
                    item,
                    test_id=None,
                    raised_at=item["raised_timestamp"],
                    evidence=item_evidence,
                    boot_id=boot_id,
                    event_seq=None,
                    uptime=uptime,
                )
                projection.raised_boot_id, projection.raised_event_seq = (
                    item["raised_boot_id"],
                    item["raised_event_seq"],
                )
            else:
                alarm = await db.get(AlarmLog, projection.alarm_log_id)
                if (
                    projection.latest_boot_id == boot_id
                    and projection.latest_uptime_ms is not None
                    and (uptime is None or int(projection.latest_uptime_ms) >= int(uptime))
                ):
                    continue
            self._apply(projection, alarm, item, at=at, evidence=item_evidence)
            projection.latest_boot_id, projection.latest_uptime_ms = boot_id, uptime
            projection.latest_event_seq = None
        if uptime is not None:
            projections = (
                await db.scalars(select(V2AlarmProjection).where(V2AlarmProjection.device_id == self.device_id))
            ).all()
            for projection in projections:
                if (projection.alarm_id, projection.occurrence_seq) in keys:
                    continue
                if (
                    projection.latest_boot_id == boot_id
                    and projection.latest_uptime_ms is not None
                    and int(projection.latest_uptime_ms) >= int(uptime)
                ):
                    continue
                alarm = await db.get(AlarmLog, projection.alarm_log_id)
                self._apply(
                    projection,
                    alarm,
                    {"code": alarm.alarm_code, "active": False, "acknowledged": True},
                    at=at,
                    evidence={**evidence, "absent_from_active_and_unacknowledged": True},
                )
                projection.latest_boot_id, projection.latest_uptime_ms = boot_id, uptime
                projection.latest_event_seq = None
        return True
