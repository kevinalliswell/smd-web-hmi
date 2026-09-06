"""Source evidence survives lost ACKs; unverified transfers never become trusted archives."""

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base
from app.db.v2_models import V2LogChunk, V2LogCursor, V2LogGap, V2LogTransfer, V2SourceRecord
from app.hostcomm.v2_contract.codec import canonical_bytes
from app.services.v2_source_logs import V2SourceLogStore, read_alarm_snapshot

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text())


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/logs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def transfer():
    return copy.deepcopy(VECTORS["log_transfer"])


async def test_partial_record_commits_bytes_before_ack_and_recovers_after_restart(factory):
    data = transfer()
    raw = b"".join(base64.b64decode(chunk["data_b64"]) for chunk in data["chunks"])
    chunks = []
    for index, offset in enumerate(range(0, len(raw), 300)):
        part = raw[offset : offset + 300]
        chunks.append(
            {
                **data["chunks"][0],
                "chunk_index": index,
                "offset": offset,
                "data_b64": base64.b64encode(part).decode(),
                "chunk_digest": hashlib.sha256(part).hexdigest(),
            }
        )
    archived = []

    async def archive(db, record, origin):
        archived.append((record["record_type"], origin))

    store = V2SourceLogStore(factory, device_id="a" * 32, on_record=archive)
    await store.begin(data["request"])
    first = await store.append_chunk(chunks[0])
    assert first["next_offset"] == 300 and first["committed_record_seq"] is None
    async with factory() as db:
        assert await db.get(V2LogChunk, (data["request"]["transfer_id"], 0)) is not None
    recovered = V2SourceLogStore(factory, device_id="a" * 32, on_record=archive)
    assert await recovered.append_chunk(chunks[0]) == first
    for chunk in chunks[1:]:
        await recovered.append_chunk(chunk)
    assert not archived
    records = await recovered.finish(data["result"])
    assert len(records) == 2 and len(archived) == 2
    assert await recovered.finish(data["result"]) == records
    assert len(archived) == 2


async def test_bad_terminal_hash_never_archives_staged_records(factory):
    data = transfer()
    store = V2SourceLogStore(factory, device_id="a" * 32)
    await store.begin(data["request"])
    for chunk in data["chunks"]:
        await store.append_chunk(chunk)
    data["result"]["content_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        await store.finish(data["result"])
    async with factory() as db:
        assert not (await db.scalars(select(V2SourceRecord))).all()
        assert (await db.get(V2LogTransfer, data["request"]["transfer_id"])).status == "failed"
    with pytest.raises(ValueError, match="no longer receiving"):
        await store.append_chunk(data["chunks"][0])


async def test_changed_chunk_and_wrong_response_session_are_rejected(factory):
    data = transfer()
    store = V2SourceLogStore(factory, device_id="a" * 32)
    await store.begin(data["request"], request_msg_id="b" * 32, session_id="c" * 32, boot_id="d" * 32)
    frame = {
        "type": "log_chunk",
        "reply_to": "b" * 32,
        "session_id": "e" * 32,
        "boot_id": "d" * 32,
        "payload": data["chunks"][0],
    }
    with pytest.raises(ValueError, match="session"):
        await store.append_chunk(frame)


async def test_live_sample_and_backfill_share_one_archive_identity(factory):
    data = transfer()
    live = copy.deepcopy(next(v["value"] for v in VECTORS["valid_messages"] if v["name"] == "telemetry"))
    archived = []

    async def archive(db, record, origin):
        archived.append((record["data"]["run_id"], origin))

    store = V2SourceLogStore(factory, device_id="a" * 32, on_record=archive)
    assert await store.ingest_live(live)
    assert not await store.ingest_live(live)
    await store.begin(data["request"])
    for chunk in data["chunks"]:
        await store.append_chunk(chunk)
    await store.finish(data["result"])
    assert len(archived) == 2
    async with factory() as db:
        rows = list((await db.scalars(select(V2SourceRecord))).all())
        assert len(rows) == 2 and all(row.log_id for row in rows)
    live["payload"]["values"]["furnace_mc"]["value"] += 1
    with pytest.raises(ValueError, match="conflict"):
        await store.ingest_live(live)


async def test_archive_failure_rolls_back_source_rows_and_cursor(factory):
    data = transfer()

    async def broken(db, record, origin):
        raise RuntimeError("legacy archive failed")

    store = V2SourceLogStore(factory, device_id="a" * 32, on_record=broken)
    await store.begin(data["request"])
    for chunk in data["chunks"]:
        await store.append_chunk(chunk)
    with pytest.raises(RuntimeError, match="archive failed"):
        await store.finish(data["result"])
    async with factory() as db:
        assert not (await db.scalars(select(V2SourceRecord))).all()


async def test_explicit_unavailable_range_is_saved_without_false_records(factory):
    data = transfer()
    result = {
        **data["result"],
        "status": "unavailable",
        "byte_length": 0,
        "record_count": 0,
        "content_digest": hashlib.sha256(b"").hexdigest(),
        "missing": [{**data["request"]["requested"], "reason": "storage_fault"}],
    }
    store = V2SourceLogStore(factory, device_id="a" * 32)
    await store.begin(data["request"])
    assert await store.finish(result) == []
    async with factory() as db:
        assert len((await db.scalars(select(V2LogGap))).all()) == 1
        assert not (await db.scalars(select(V2SourceRecord))).all()
        cursor = await db.get(V2LogCursor, ("a" * 32, result["requested"]["log_id"]))
        assert cursor.scanned_through_seq == result["requested"]["last_record_seq"]
        assert cursor.verified_from_seq is None and cursor.verified_through_seq is None


async def test_partial_scan_then_complete_cut_does_not_promote_the_gap(factory):
    data = transfer()
    records = [json.loads(line) for line in data["canonical_jsonl_utf8"].splitlines()]
    store = V2SourceLogStore(factory, device_id="a" * 32)

    async def receive(request, record, *, highwater, status, missing):
        raw = canonical_bytes(record) + b"\n"
        part = {
            **data["chunks"][0],
            "transfer_id": request["transfer_id"],
            "snapshot_highwater": highwater,
            "chunk_index": 0,
            "offset": 0,
            "data_b64": base64.b64encode(raw).decode(),
            "chunk_digest": hashlib.sha256(raw).hexdigest(),
        }
        result = {
            **data["result"],
            "transfer_id": request["transfer_id"],
            "requested": request["requested"],
            "snapshot_highwater": highwater,
            "status": status,
            "byte_length": len(raw),
            "record_count": 1,
            "content_digest": hashlib.sha256(raw).hexdigest(),
            "missing": missing,
        }
        await store.begin(request)
        await store.append_chunk(part)
        await store.finish(result)

    await receive(
        data["request"],
        records[0],
        highwater="2",
        status="partial",
        missing=[{**data["request"]["requested"], "first_record_seq": "2", "reason": "storage_fault"}],
    )
    key = ("a" * 32, data["request"]["requested"]["log_id"])
    async with factory() as db:
        cursor = await db.get(V2LogCursor, key)
        assert cursor.scanned_through_seq == "2"
        assert cursor.verified_from_seq is None and cursor.verified_through_seq is None
    later = {
        **data["request"],
        "transfer_id": "b" * 32,
        "requested": {**data["request"]["requested"], "first_record_seq": "3", "last_record_seq": "3"},
    }
    records[1]["record_seq"] = "3"
    await receive(later, records[1], highwater="3", status="complete", missing=[])
    async with factory() as db:
        cursor = await db.get(V2LogCursor, key)
        assert cursor.scanned_through_seq == "3"
        assert (cursor.verified_from_seq, cursor.verified_through_seq) == ("3", "3")
        assert len((await db.scalars(select(V2LogGap))).all()) == 1


async def test_previously_unarchived_live_source_can_be_archived_once(factory):
    live = copy.deepcopy(next(v["value"] for v in VECTORS["valid_messages"] if v["name"] == "telemetry"))
    store = V2SourceLogStore(factory, device_id="a" * 32)
    await store.ingest_live(live)
    archived = []

    async def archive(db, record, origin):
        archived.append(record["data"]["run_id"])

    recovered = V2SourceLogStore(factory, device_id="a" * 32, on_record=archive)
    assert not await recovered.ingest_live(live)
    assert not await recovered.ingest_live(live)
    assert len(archived) == 1


async def test_alarm_revision_restart_discards_partial_old_snapshot():
    occurrence = copy.deepcopy(
        next(v["value"]["payload"]["items"][0] for v in VECTORS["valid_messages"] if v["name"] == "alarms_snapshot")
    )
    calls = []

    class Conflict(Exception):
        code = "state_conflict"

    class Transport:
        async def request(self, kind, query):
            calls.append(copy.deepcopy(query))
            if len(calls) == 1:
                return {"payload": {"revision": "1", "page_offset": 0, "items": [occurrence], "next_offset": 1}}
            if len(calls) == 2:
                raise Conflict()
            return {"payload": {"revision": "2", "page_offset": 0, "items": [], "next_offset": None}}

    assert await read_alarm_snapshot(Transport()) == {"revision": "2", "items": []}
    assert calls[1]["expected_revision"] == "1"
    assert calls[2] == {"expected_revision": None, "page_offset": 0, "limit": 16}
