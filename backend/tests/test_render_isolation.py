"""Real subprocess rendering, failure cleanup and revision conflicts; never touch a device."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.models import BackgroundJob, ReportExport, TestSession
from app.services import render_client, report_service


async def seed(db):
    db.add(
        TestSession(
            test_id="P2-REPORT",
            operator_id="op",
            start_time="2026-10-23T00:00:00Z",
            measurement_basis_json='{"metadata_revision":1}',
            recipe_snapshot_json='{"version":3}',
        )
    )
    await db.commit()


async def test_snapshot_persisted_before_render_no_transaction_or_secret_and_conflict_rejected(
    db_session, tmp_path, monkeypatch
):
    await seed(db_session)
    db_session.add(
        BackgroundJob(
            task_id="a" * 32,
            kind="report",
            operator_id="op",
            status="running",
            progress=10,
            input_json="{}",
            created_at="2026-10-23T00:00:00Z",
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    original = render_client.render_snapshot
    monkeypatch.setattr(type(get_settings()), "reports_dir", property(lambda _: tmp_path))

    async def render(snapshot, directory, **kwargs):
        assert not db_session.in_transaction()
        assert snapshot["metrics"]["algorithm_version"]
        assert snapshot["test"]["measurement_basis_json"] == '{"metadata_revision":1}'
        assert "jwt" not in json.dumps(snapshot).lower()
        async with factory() as other:
            row = await other.get(BackgroundJob, "a" * 32)
            assert json.loads(row.snapshot_json)["test"]["recipe_snapshot_json"] == '{"version":3}'
            await other.execute(update(TestSession).values(measurement_basis_json='{"metadata_revision":2}'))
            await other.commit()
        return await original(snapshot, directory, **kwargs)

    monkeypatch.setattr(render_client, "render_snapshot", render)
    with pytest.raises(render_client.RenderError, match="source_changed_during_report"):
        await report_service.generate_report(db_session, "P2-REPORT", operator_id="op", task_id="a" * 32)
    assert not list(tmp_path.glob("*.html")) and not list(tmp_path.glob(".render-*"))
    assert await db_session.scalar(select(ReportExport)) is None


async def test_low_disk_refuses_worker_and_publishes_no_artifact(db_session, tmp_path, monkeypatch):
    await seed(db_session)
    monkeypatch.setattr(type(get_settings()), "reports_dir", property(lambda _: tmp_path))
    monkeypatch.setattr(render_client.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(render_client.RenderError, match="insufficient_disk_space"):
        await report_service.generate_report(db_session, "P2-REPORT", operator_id="op")
    assert not list(tmp_path.glob("*.html"))


async def test_worker_failure_is_terminal_and_staging_is_cleaned(tmp_path):
    with pytest.raises(render_client.RenderError, match="render_worker_failed"):
        await render_client.render_snapshot({"schema_version": 999}, tmp_path)
    assert not list(tmp_path.iterdir()) and not render_client.metrics()["running"]


async def test_slow_child_keeps_event_loop_live_and_environment_scrubbed(tmp_path, monkeypatch):
    actual = asyncio.create_subprocess_exec
    seen = []
    monkeypatch.setenv("SMD_JWT_SECRET", "must-not-reach-worker")
    monkeypatch.setenv("SMD_HOSTCOMM_PSK_FILE", "must-not-reach-worker")

    async def child(*args, **kwargs):
        assert not any(key.startswith("SMD_") for key in kwargs["env"])
        output = args[args.index("--output") + 1]
        script = "import pathlib,sys,time;time.sleep(.35);pathlib.Path(sys.argv[1]).write_bytes(b'<!DOCTYPE html>ok')"
        return await actual(sys.executable, "-c", script, output, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", child)

    async def probe():
        for _ in range(8):
            await asyncio.sleep(0.02)
            seen.append(render_client.metrics()["running"])

    result, _ = await asyncio.gather(render_client.render_snapshot({"format": "html"}, tmp_path), probe())
    assert result.startswith(b"<!DOCTYPE html>") and any(seen)
    assert not list(tmp_path.iterdir())


async def test_timeout_reaps_owned_worker_and_removes_staging(tmp_path, monkeypatch):
    actual = asyncio.create_subprocess_exec

    async def child(*args, **kwargs):
        return await actual(sys.executable, "-c", "import time;time.sleep(30)", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", child)
    with pytest.raises(render_client.RenderError, match="render_timeout"):
        await render_client.render_snapshot({"format": "html"}, tmp_path, timeout=0.05)
    assert not list(tmp_path.iterdir()) and render_client.metrics()["pid"] is None


async def test_render_queue_is_serial_and_hostcomm_receipts_continue(tmp_path, monkeypatch, mock_server):
    from tests.conftest import make_client

    actual = asyncio.create_subprocess_exec
    active = 0
    maximum = 0

    async def child(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        output = args[args.index("--output") + 1]
        script = "import pathlib,sys,time;time.sleep(.6);pathlib.Path(sys.argv[1]).write_bytes(b'<!DOCTYPE html>ok')"
        process = await actual(sys.executable, "-c", script, output, **kwargs)
        original_wait = process.wait

        async def wait():
            nonlocal active
            result = await original_wait()
            active -= 1
            return result

        process.wait = wait
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", child)
    client = await make_client(mock_server, heartbeat_interval=2.0)
    await client.connect()
    try:
        frames = client.stats["frames_parsed"]
        workers = [asyncio.create_task(render_client.render_snapshot({"format": "html"}, tmp_path)) for _ in range(2)]
        for _ in range(4):
            await asyncio.sleep(0.1)
            status = await client.get_status()
            assert status and client.is_online
        await asyncio.gather(*workers)
        assert client.is_online and client.stats["frames_parsed"] >= frames + 4
        assert maximum == 1 and not list(tmp_path.iterdir())
    finally:
        await client.close()


async def test_live_binding_revision_conflict_prevents_archive(db_session, tmp_path, monkeypatch):
    from app.db.v2_models import V2RunBinding

    await seed(db_session)
    db_session.add(
        V2RunBinding(
            device_id="a" * 32,
            run_id="b" * 32,
            test_id="P2-REPORT",
            recipe_digest="c" * 64,
            created_at="2026-10-23T00:00:00Z",
        )
    )
    await db_session.commit()
    monkeypatch.setattr(type(get_settings()), "reports_dir", property(lambda _: tmp_path))

    async def render(*args, **kwargs):
        async with async_sessionmaker(db_session.bind)() as other:
            await other.execute(update(V2RunBinding).values(profile_digest="d" * 64))
            await other.commit()
        return b"<!DOCTYPE html>ok"

    monkeypatch.setattr(render_client, "render_snapshot", render)
    with pytest.raises(render_client.RenderError, match="source_changed_during_report"):
        await report_service.generate_report(db_session, "P2-REPORT", operator_id="op")
    assert not list(tmp_path.glob("*.html"))


async def test_large_source_real_pdf_keeps_hostcomm_and_loop_responsive(db_session, tmp_path, monkeypatch):
    """Read 20k actual samples and render PDF while the real TCP mock answers reads."""
    import time

    from sqlalchemy import insert

    from app.db.models import SamplePoint
    from app.hostcomm.mock_server import MockHostCommServer
    from tests.conftest import free_port, make_client

    await seed(db_session)
    await db_session.execute(
        insert(SamplePoint),
        [
            dict(
                test_id="P2-REPORT",
                ts="2026-10-23T00:00:00Z",
                furnace_pv=600 + i / 100,
                burden_temp=600 + i / 100,
                delta_p=i / 10,
                displacement=i / 1000,
            )
            for i in range(20_000)
        ],
    )
    await db_session.commit()
    monkeypatch.setattr(type(get_settings()), "reports_dir", property(lambda _: tmp_path))
    server = MockHostCommServer(port=free_port(), status_interval=None)
    await server.start()
    # Use the deployed 2 s / 3 missed heartbeat configuration, not the 100 ms unit-test shortcut.
    client = await make_client(server, heartbeat_interval=2.0, command_timeout=2.0)
    await client.connect()
    original_session = client.stats["session_id"]
    latencies, loop_gaps = [], []
    done = asyncio.Event()

    async def probe():
        previous = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            loop_gaps.append(now - previous)
            previous = now
            started = time.monotonic()
            assert await client.get_status()
            latencies.append(time.monotonic() - started)

    probe_task = asyncio.create_task(probe())
    try:
        record = await report_service.generate_report(db_session, "P2-REPORT", operator_id="op", fmt="pdf")
        await asyncio.sleep(2.1)  # Observe at least one real heartbeat, including a fast CI renderer.
        assert Path(record.file_path).read_bytes().startswith(b"%PDF")
        assert json.loads(record.notes)["metrics"]["sample_count"] == 20_000
        assert latencies and max(latencies) < 2.0 and max(loop_gaps) < 2.0 and client.is_online
        assert client.stats["session_id"] == original_session
        assert client.stats["heartbeat_age_s"] is not None
        print(
            f"P2 20000 samples: reads={len(latencies)}, max_receipt_ms={max(latencies)*1000:.1f}, "
            f"max_probe_gap_ms={max(loop_gaps)*1000:.1f}, pdf_bytes={record.file_size_bytes}"
        )
    finally:
        done.set()
        try:
            await probe_task
        finally:
            await client.close()
            await server.stop()
