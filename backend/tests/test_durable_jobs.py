"""Restart, ownership, terminal retry and storage failures on isolated SQLite."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import CurrentUser, get_current_user
from app.db.database import get_db
from app.db.models import BackgroundJob
from app.hostcomm.protocol import now_iso
from app.main import create_app
from app.services.background_jobs import BackgroundJobCapacityError, BackgroundJobManager
from app.services.job_store import interrupt_unfinished


def job(task_id, *, owner="op", status="pending", kind="report"):
    return BackgroundJob(
        task_id=task_id,
        kind=kind,
        operator_id=owner,
        status=status,
        progress=0,
        input_json='{"test_id":"T-1","format":"html"}',
        created_at=now_iso(),
    )


async def terminal(manager, task_id):
    async with asyncio.timeout(10):
        while task_id in manager._tasks:
            await asyncio.sleep(0.005)


async def test_restart_marks_pending_running_interrupted_without_invoking_work(db_session):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    for index, status in enumerate(("pending", "running", "completed", "failed")):
        db_session.add(job(str(index) * 32, status=status))
    db_session.add(job("a" * 32, kind="v2_source_recovery"))
    await db_session.commit()
    await interrupt_unfinished(factory)
    await interrupt_unfinished(factory)
    async with factory() as reopened:
        rows = (await reopened.scalars(select(BackgroundJob))).all()
    assert [row.status for row in rows] == ["interrupted", "interrupted", "completed", "failed", "interrupted"]
    assert rows[0].finished_at and rows[0].error_code == "service_interrupted"
    assert rows[-1].result_json is None


async def test_admitted_result_survives_manager_recreation_and_capacity_is_bounded(db_session):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    manager = BackgroundJobManager(max_concurrency=1, max_records=1)
    gate = asyncio.Event()

    async def runner():
        await gate.wait()
        return {"id": 17}

    task_id = await manager.submit_durable("report", runner, operator_id="op", inputs={}, factory=factory)
    with pytest.raises(BackgroundJobCapacityError):
        await manager.submit_durable("report", runner, operator_id="op", inputs={}, factory=factory)
    gate.set()
    await terminal(manager, task_id)
    await manager.shutdown()
    assert BackgroundJobManager().snapshot(task_id) is None
    async with factory() as reopened:
        row = await reopened.get(BackgroundJob, task_id)
        assert row.status == "completed" and json.loads(row.result_json) == {"id": 17}
        assert row.started_at and row.finished_at


async def test_job_is_not_run_when_durable_admission_fails(db_session, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    manager = BackgroundJobManager()
    ran = False

    async def runner():
        nonlocal ran
        ran = True
        return {}

    async def fail(_self):
        raise OSError("private disk path")

    monkeypatch.setattr(AsyncSession, "commit", fail)
    with pytest.raises(OSError):
        await manager.submit_durable(
            "report",
            runner,
            operator_id="op",
            inputs={},
            factory=async_sessionmaker(db_session.bind, expire_on_commit=False),
        )
    await asyncio.sleep(0)
    assert not ran and not manager._tasks and manager._admissions == 0


async def test_queued_job_shutdown_is_persisted_even_before_first_coroutine_step(db_session):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    manager = BackgroundJobManager()

    async def never():
        await asyncio.Event().wait()

    task_id = await manager.submit_durable("v2_source_recovery", never, operator_id="admin", inputs={}, factory=factory)
    await manager.shutdown()
    async with factory() as db:
        assert (await db.get(BackgroundJob, task_id)).status == "interrupted"


async def test_http_ownership_legacy_routes_pagination_and_retry(db_session, monkeypatch):
    from app.api.routes import jobs

    for row in (
        job("a" * 32, status="failed"),
        job("b" * 32, owner="other"),
        job("c" * 32, kind="v2_source_recovery", status="interrupted"),
        job("d" * 32, kind="log_export", status="interrupted"),
    ):
        db_session.add(row)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    app = create_app()
    current = CurrentUser("op", "operator")
    app.dependency_overrides[get_current_user] = lambda: current

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    retries = []

    async def submit(kind, inputs, owner, **kwargs):
        retries.append((kind, owner, kwargs["retry_of"]))
        return "e" * 32

    monkeypatch.setattr(jobs, "submit_export", submit)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        data = (await http.get("/api/jobs?page_size=1")).json()["data"]
        assert data["total"] == 2 and len(data["items"]) == 1
        assert (await http.get("/api/jobs/" + "b" * 32)).status_code == 404
        assert (await http.get("/api/reports/tasks/" + "b" * 32)).status_code == 404
        assert (await http.get("/api/reports/tasks/" + "a" * 32)).json()["data"]["status"] == "failed"
        assert (await http.get("/api/logs/export/" + "d" * 32)).json()["data"]["status"] == "interrupted"
        assert (await http.get("/api/logs/export/" + "d" * 32 + "/download")).status_code == 409
        response = await http.post("/api/jobs/" + "a" * 32 + "/retry")
        assert response.status_code == 202 and response.json()["data"]["task_id"] != "a" * 32
        current = CurrentUser("admin", "admin")
        assert (await http.get("/api/jobs")).json()["data"]["total"] == 4
        assert (await http.post("/api/jobs/" + "b" * 32 + "/retry")).status_code == 409
        assert (await http.post("/api/jobs/" + "c" * 32 + "/retry")).status_code == 409
        current = CurrentUser("op", "observer")
        assert (await http.get("/api/jobs")).status_code == 403
    assert retries == [("report", "op", "a" * 32)]


async def test_downloads_require_completed_task_and_verified_bytes(db_session, tmp_path, monkeypatch):
    import hashlib

    from app.core.config import get_settings
    from app.db.models import ReportExport

    monkeypatch.setattr(type(get_settings()), "exports_dir", property(lambda _: tmp_path))
    content = b"<!DOCTYPE html>archived"
    report_path = tmp_path / "report.html"
    report_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    report_job = job("a" * 32, status="interrupted")
    log_job = job("b" * 32, status="completed", kind="log_export")
    log_job.result_json = json.dumps({"sha256": digest})
    db_session.add_all([report_job, log_job])
    record = ReportExport(
        test_id="T-1",
        operator_id="op",
        generated_at=now_iso(),
        format="html",
        file_path=str(report_path),
        file_size_bytes=len(content),
        notes=json.dumps({"artifact_sha256": digest, "task_id": "a" * 32}),
    )
    db_session.add(record)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    app = create_app()
    current = CurrentUser("op", "operator")
    app.dependency_overrides[get_current_user] = lambda: current

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    log_path = tmp_path / ("b" * 32 + ".zip")
    log_path.write_bytes(content)
    legacy_id = "c" * 32
    (tmp_path / (legacy_id + ".zip")).write_bytes(b"legacy")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        report_url = f"/api/reports/{record.id}/download"
        log_url = "/api/logs/export/" + "b" * 32 + "/download"
        assert (await http.get(report_url)).status_code == 409
        report_job.status = "completed"
        await db_session.commit()
        assert (await http.get(report_url)).content == content
        assert (await http.get(log_url)).content == content
        report_path.write_bytes(b"corrupt")
        log_path.write_bytes(b"corrupt")
        assert "artifact_digest_mismatch" in (await http.get(report_url)).text
        assert (await http.get(log_url)).status_code == 409
        assert (await http.get(f"/api/logs/export/{legacy_id}/download")).status_code == 404
        current = CurrentUser("admin", "admin")
        assert (await http.get(f"/api/logs/export/{legacy_id}")).json()["data"]["result"]["legacy_unverified"]
        assert (await http.get(f"/api/logs/export/{legacy_id}/download")).content == b"legacy"


async def test_actual_export_runner_archives_report_and_zip_and_persists_disk_failure(
    db_session, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from app.core.config import get_settings
    from app.db.models import TestSession
    from app.services.export_jobs import submit_export

    settings = get_settings()
    monkeypatch.setattr(type(settings), "reports_dir", property(lambda _: tmp_path))
    monkeypatch.setattr(type(settings), "exports_dir", property(lambda _: tmp_path))
    db_session.add(TestSession(test_id="P2-EXPORT", operator_id="op", start_time="2026-10-23T00:00:00Z"))
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    manager = BackgroundJobManager()
    try:
        for kind in ("report", "log_export"):
            task_id = await submit_export(
                kind, {"test_id": "P2-EXPORT", "format": "html"}, "op", manager=manager, factory=factory
            )
            await terminal(manager, task_id)
            async with factory() as reopened:
                row = await reopened.get(BackgroundJob, task_id)
                assert row.status == "completed", row.error_code
                result = json.loads(row.result_json)
                assert len(result["sha256"]) == 64 and result["download_url"].startswith("/api/")
                assert "file_path" not in result
                if kind == "report":
                    assert json.loads(row.snapshot_json)["test"]["test_id"] == "P2-EXPORT"
        monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=0))
        task_id = await submit_export("log_export", {"test_id": "P2-EXPORT"}, "op", manager=manager, factory=factory)
        await terminal(manager, task_id)
        async with factory() as reopened:
            row = await reopened.get(BackgroundJob, task_id)
            assert row.status == "failed" and row.error_code == "insufficient_disk_space"
            assert row.result_json is None
    finally:
        await manager.shutdown()


async def test_runtime_metrics_require_admin_and_unobserved_timings_stay_unknown(tmp_path, monkeypatch):
    from app.api.routes import system
    from app.services.runtime_metrics import RuntimeMetrics

    metrics = RuntimeMetrics()
    monkeypatch.setattr(system, "runtime_metrics", metrics)
    monkeypatch.setattr(system, "get_settings", lambda: type("Settings", (), {"data_dir": tmp_path})())
    app = create_app()
    current = CurrentUser("op", "operator")
    app.dependency_overrides[get_current_user] = lambda: current
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/api/system/runtime-metrics")).status_code == 403
        current = CurrentUser("admin", "admin")
        response = await http.get("/api/system/runtime-metrics")
        assert response.status_code == 200
        data = response.json()["data"]
        assert all(value is None for value in data["timing"].values())
        assert data["communication"] is None and data["storage_free_bytes"] > 0
        assert data["render"]["concurrency"] == 1 and data["jobs"]["capacity"] == 200
