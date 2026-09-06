"""Durable raw-chunk ACKs and immutable v2 source archives, isolated from live control."""

from __future__ import annotations

import asyncio
import hashlib
import json

from pydantic import TypeAdapter
from sqlalchemy import select, update

from app.db.v2_models import V2LogChunk, V2LogCursor, V2LogGap, V2LogTransfer, V2SourceRecord
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.codec import (
    canonical_bytes,
    decode_chunk,
    strict_loads,
    validate_log_transfer,
    validate_message,
)
from app.hostcomm.v2_contract.messages import LOG_RECORD_ADAPTER, LogChunk, LogRequest, LogResult
from app.hostcomm.v2_contract.types import MAX_FRAME_BYTES, Identifier


def _json(value):
    return canonical_bytes(value).decode("utf-8")


def _payload(message):
    return message["payload"] if "payload" in message else message


class V2SourceLogStore:
    """All writes use the application's SQLite session factory, never a second database.

    on_record(db, record, origin) may append legacy archive rows inside this transaction.
    It MUST NOT commit, roll back, change live device state or infer an absent run_id.
    Backfill callbacks run only after complete transfer validation, not on chunk arrival.
    """

    def __init__(self, factory, *, device_id, on_record=None, write_lock=None):
        self.factory = factory
        self.device_id = TypeAdapter(Identifier).validate_python(device_id)
        self.on_record = on_record
        self.write_lock = write_lock or asyncio.Lock()

    async def begin(self, request, *, request_msg_id=None, session_id=None, boot_id=None):
        query = LogRequest.model_validate(request).model_dump()
        bindings = (request_msg_id, session_id, boot_id)
        if any(item is not None for item in bindings):
            if not all(item is not None for item in bindings):
                raise ValueError("request/session/boot bindings must be provided together")
            for item in bindings:
                TypeAdapter(Identifier).validate_python(item)
        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2LogTransfer)
                .where(V2LogTransfer.transfer_id == query["transfer_id"])
                .values(updated_at=V2LogTransfer.updated_at)
            )
            existing = await db.get(V2LogTransfer, query["transfer_id"])
            if existing:
                if (
                    existing.device_id != self.device_id
                    or existing.request_json != _json(query)
                    or (existing.request_msg_id, existing.session_id, existing.boot_id) != bindings
                ):
                    raise ValueError("transfer identity/session conflict")
                return self._progress(existing)
            row = V2LogTransfer(
                transfer_id=query["transfer_id"],
                device_id=self.device_id,
                request_json=_json(query),
                request_msg_id=request_msg_id,
                session_id=session_id,
                boot_id=boot_id,
                status="receiving",
                next_offset=0,
                next_index=0,
                pending_bytes=b"",
                record_count=0,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            db.add(row)
            return self._progress(row)

    @staticmethod
    def _progress(row):
        return {
            "transfer_id": row.transfer_id,
            "status": row.status,
            "next_offset": row.next_offset,
            "next_index": row.next_index,
            "committed_record_seq": row.committed_record_seq,
        }

    async def progress(self, transfer_id):
        async with self.factory() as db:
            row = await db.get(V2LogTransfer, transfer_id)
            if not row or row.device_id != self.device_id:
                raise ValueError("unknown transfer")
            return self._progress(row)

    @staticmethod
    def _binding(row, frame, expected_type):
        if row.request_msg_id is not None and (
            frame.get("type") != expected_type
            or frame.get("reply_to") != row.request_msg_id
            or frame.get("session_id") != row.session_id
            or frame.get("boot_id") != row.boot_id
        ):
            raise ValueError("response request/session/boot mismatch")

    async def _fail(self, transfer_id):
        async with self.write_lock, self.factory() as db, db.begin():
            await db.execute(
                update(V2LogTransfer)
                .where(
                    V2LogTransfer.transfer_id == transfer_id,
                    V2LogTransfer.device_id == self.device_id,
                    V2LogTransfer.status == "receiving",
                )
                .values(status="failed", updated_at=now_iso())
            )

    async def append_chunk(self, message):
        part = LogChunk.model_validate(_payload(message))
        try:
            async with self.write_lock, self.factory() as db, db.begin():
                await db.execute(
                    update(V2LogTransfer)
                    .where(V2LogTransfer.transfer_id == part.transfer_id)
                    .values(updated_at=V2LogTransfer.updated_at)
                )
                row = await db.get(V2LogTransfer, part.transfer_id)
                if not row or row.device_id != self.device_id:
                    raise ValueError("unknown transfer")
                self._binding(row, message, "log_chunk")
                if row.status == "failed":
                    raise ValueError("transfer is no longer receiving")
                previous = await db.get(V2LogChunk, (part.transfer_id, part.chunk_index))
                if previous:
                    if previous.metadata_json != _json(part):
                        raise ValueError("retransmitted chunk bytes/identity conflict")
                    return json.loads(previous.ack_json)
                if row.status != "receiving":
                    raise ValueError("transfer is no longer receiving")
                query = LogRequest.model_validate_json(row.request_json)
                raw = decode_chunk(part.data_b64)
                if (
                    part.log_id != query.requested.log_id
                    or part.offset != row.next_offset
                    or part.chunk_index != row.next_index
                    or hashlib.sha256(raw).hexdigest() != part.chunk_digest
                ):
                    raise ValueError("chunk identity/offset/digest conflict")
                if int(part.snapshot_highwater) < int(
                    query.requested.last_record_seq
                ) or row.snapshot_highwater not in {None, part.snapshot_highwater}:
                    raise ValueError("chunk committed highwater changed")
                if row.next_offset + len(raw) > query.max_bytes:
                    raise ValueError("transfer exceeds requested byte budget")
                combined = row.pending_bytes + raw
                lines = combined.split(b"\n")
                row.pending_bytes = lines.pop()
                if len(row.pending_bytes) > MAX_FRAME_BYTES:
                    raise ValueError("source record exceeds bounded line length")
                last = (
                    int(row.committed_record_seq)
                    if row.committed_record_seq is not None
                    else int(query.requested.first_record_seq) - 1
                )
                for line in lines:
                    record = LOG_RECORD_ADAPTER.validate_python(strict_loads(line))
                    if canonical_bytes(record) != line:
                        raise ValueError("source log record is not canonical JSON")
                    seq = int(record.record_seq)
                    if record.log_id != part.log_id or seq <= last or seq > int(query.requested.last_record_seq):
                        raise ValueError("source record order/cut conflict")
                    last = seq
                    row.committed_record_seq = record.record_seq
                    row.record_count += 1
                if row.record_count > query.max_records:
                    raise ValueError("transfer exceeds requested record budget")
                row.next_index += 1
                row.next_offset += len(raw)
                row.snapshot_highwater, row.updated_at = part.snapshot_highwater, now_iso()
                ack = {
                    "transfer_id": part.transfer_id,
                    "log_id": part.log_id,
                    "chunk_index": part.chunk_index,
                    "chunk_digest": part.chunk_digest,
                    "next_offset": row.next_offset,
                    "committed_record_seq": row.committed_record_seq,
                }
                db.add(
                    V2LogChunk(
                        transfer_id=part.transfer_id,
                        chunk_index=part.chunk_index,
                        metadata_json=_json(part),
                        raw_bytes=raw,
                        ack_json=_json(ack),
                    )
                )
            # Even a partial first record has durable chunk bytes now; do not wait for its LF to ACK.
            return ack
        except Exception:
            await self._fail(part.transfer_id)
            raise

    async def finish(self, message):
        terminal = LogResult.model_validate(_payload(message))
        try:
            async with self.write_lock, self.factory() as db, db.begin():
                await db.execute(
                    update(V2LogTransfer)
                    .where(V2LogTransfer.transfer_id == terminal.transfer_id)
                    .values(updated_at=V2LogTransfer.updated_at)
                )
                row = await db.get(V2LogTransfer, terminal.transfer_id)
                if not row or row.device_id != self.device_id:
                    raise ValueError("unknown transfer")
                self._binding(row, message, "log_result")
                if row.status == "failed":
                    raise ValueError("transfer failed; restart from a persisted complete source record")
                if row.result_json is not None and row.result_json != _json(terminal):
                    raise ValueError("transfer terminal result conflict")
                chunks = (
                    await db.scalars(
                        select(V2LogChunk)
                        .where(V2LogChunk.transfer_id == terminal.transfer_id)
                        .order_by(V2LogChunk.chunk_index)
                    )
                ).all()
                records = validate_log_transfer(
                    json.loads(row.request_json),
                    [json.loads(chunk.metadata_json) for chunk in chunks],
                    terminal.model_dump(),
                )
                if row.result_json is not None:
                    await self._advance_verified(db, terminal)
                    return [record.model_dump() for record in records]
                for record in records:
                    await self._ingest(db, record.model_dump(), "backfill")
                for gap in terminal.missing:
                    db.add(
                        V2LogGap(
                            device_id=self.device_id,
                            transfer_id=row.transfer_id,
                            log_id=gap.log_id,
                            first_record_seq=gap.first_record_seq,
                            last_record_seq=gap.last_record_seq,
                            reason=gap.reason,
                            created_at=now_iso(),
                        )
                    )
                await self._advance_verified(db, terminal)
                row.result_json, row.status, row.updated_at = _json(terminal), terminal.status, now_iso()
                return [record.model_dump() for record in records]
        except Exception:
            await self._fail(terminal.transfer_id)
            raise

    async def _advance_verified(self, db, terminal):
        # Scanning includes explicitly accounted-for gaps; verification never crosses them.
        key = (self.device_id, terminal.requested.log_id)
        row = await db.get(V2LogCursor, key)
        first, last = int(terminal.requested.first_record_seq), int(terminal.requested.last_record_seq)
        if row is None:
            row = V2LogCursor(device_id=key[0], log_id=key[1], scanned_through_seq=str(last), updated_at=now_iso())
            db.add(row)
        else:
            row.scanned_through_seq = str(max(last, int(row.scanned_through_seq or "0")))
            row.updated_at = now_iso()
        if terminal.status != "complete":
            return
        if row.verified_from_seq is None or row.verified_through_seq is None:
            row.verified_from_seq, row.verified_through_seq = str(first), str(last)
        elif first <= int(row.verified_through_seq) + 1 and last >= int(row.verified_from_seq) - 1:
            row.verified_from_seq = str(min(first, int(row.verified_from_seq)))
            row.verified_through_seq = str(max(last, int(row.verified_through_seq)))

    async def _ingest(self, db, record, origin):
        kind, payload = record["record_type"], record["data"]
        boot = payload["sample"]["boot_id"] if kind == "sample" else record["boot_id"]
        seq = payload["sample"]["sample_seq"] if kind == "sample" else payload["event_seq"]
        source_bytes = canonical_bytes(payload)
        raw_record = canonical_bytes(record) + b"\n" if record.get("log_id") is not None else None
        existing = await db.scalar(
            select(V2SourceRecord).where(
                V2SourceRecord.device_id == self.device_id,
                V2SourceRecord.record_type == kind,
                V2SourceRecord.boot_id == boot,
                V2SourceRecord.source_seq == seq,
            )
        )
        if record.get("log_id") is not None:
            location = await db.scalar(
                select(V2SourceRecord).where(
                    V2SourceRecord.device_id == self.device_id,
                    V2SourceRecord.log_id == record["log_id"],
                    V2SourceRecord.record_seq == record["record_seq"],
                )
            )
            if location is not None and (existing is None or location.id != existing.id):
                raise ValueError("source log location conflict")
        if existing:
            if existing.payload_bytes != source_bytes or (
                existing.record_bytes is not None and raw_record is not None and existing.record_bytes != raw_record
            ):
                raise ValueError("source identity byte conflict")
            if raw_record is not None:
                existing.log_id, existing.record_seq, existing.record_bytes = (
                    record["log_id"],
                    record["record_seq"],
                    raw_record,
                )
            await self._archive(db, existing, record, origin)
            return False
        if (
            kind == "event"
            and await db.scalar(
                select(V2SourceRecord.id).where(
                    V2SourceRecord.device_id == self.device_id, V2SourceRecord.event_id == payload["event_id"]
                )
            )
            is not None
        ):
            raise ValueError("source event identity conflict")
        row = V2SourceRecord(
            device_id=self.device_id,
            record_type=kind,
            boot_id=boot,
            source_seq=seq,
            event_id=payload.get("event_id"),
            run_id=payload.get("run_id"),
            log_id=record.get("log_id"),
            record_seq=record.get("record_seq"),
            payload_bytes=source_bytes,
            record_bytes=raw_record,
            archived=0,
            received_at=now_iso(),
        )
        db.add(row)
        await db.flush()
        await self._archive(db, row, record, origin)
        return True

    async def _archive(self, db, row, record, origin):
        if self.on_record is not None and not row.archived:
            transaction = db.get_transaction()
            archived = await self.on_record(db, record, origin)
            if db.get_transaction() is not transaction:
                raise RuntimeError("archive callback must not commit or replace the source transaction")
            # Unknown runs may be linked later; a callback can explicitly defer projection.
            row.archived = 0 if archived is False else 1

    async def ingest_live(self, envelope):
        message = validate_message(envelope)
        if message.type not in {"telemetry", "event"}:
            raise ValueError("only live telemetry/events are source records")
        record = {
            "record_type": "sample" if message.type == "telemetry" else "event",
            "boot_id": message.boot_id,
            "data": message.payload.model_dump(),
            "log_id": None,
            "record_seq": None,
        }
        async with self.write_lock, self.factory() as db, db.begin():
            # Reserve writer before duplicate checks, also across independent store instances.
            await db.execute(
                update(V2LogTransfer)
                .where(V2LogTransfer.device_id == self.device_id)
                .values(updated_at=V2LogTransfer.updated_at)
            )
            return await self._ingest(db, record, "live")


async def read_alarm_snapshot(transport, *, max_restarts=3, max_pages=4096):
    """Read only one fixed alarm revision; discard partial pages on a revision conflict."""
    from app.hostcomm.v2_contract.messages import AlarmsSnapshot

    class RevisionChanged(ValueError):
        code = "state_conflict"

    for _ in range(max_restarts):
        offset, revision, alarms, seen, context = 0, None, [], set(), None
        try:
            for _page in range(max_pages):
                response = await transport.request(
                    "get_alarms", {"expected_revision": revision, "page_offset": offset, "limit": 16}
                )
                page = AlarmsSnapshot.model_validate(response["payload"])
                current_context = (response.get("boot_id"), response.get("session_id"))
                if context is not None and context != current_context:
                    raise RevisionChanged("alarm connection changed while paging")
                context = current_context
                if page.page_offset != offset or (revision is not None and page.revision != revision):
                    raise ValueError("alarm revision/page conflict")
                revision = page.revision
                for alarm in page.items:
                    key = (alarm.alarm_id, alarm.occurrence_seq)
                    if key in seen:
                        raise ValueError("duplicate alarm across pages")
                    seen.add(key)
                    alarms.append(alarm.model_dump())
                if page.next_offset is None:
                    result = {"revision": revision, "items": alarms}
                    if response.get("boot_id") is not None and response.get("uptime_ms") is not None:
                        result.update(boot_id=response["boot_id"], snapshot_uptime_ms=response["uptime_ms"])
                    return result
                offset = page.next_offset
            raise ValueError("alarm snapshot exceeds bounded page count")
        except Exception as exc:
            if getattr(exc, "code", None) != "state_conflict":
                raise
    raise ValueError("alarm revision did not stabilize within the retry budget")
