"""报告/日志 POST 只提交任务，不在请求协程执行重活。"""

from __future__ import annotations

from app.api.deps import CurrentUser
from app.api.routes import logs as logs_route
from app.api.routes import reports as reports_route
from app.db.models import TestSession


async def test_log_export_endpoint_returns_pending_without_running_job(monkeypatch):
    submitted = {}

    def submit(kind, runner, *, task_id=None):
        submitted.update(kind=kind, runner=runner, task_id=task_id)
        return task_id

    monkeypatch.setattr(logs_route.background_jobs, "submit", submit)

    response = await logs_route.export_log(logs_route.ExportLogRequest(test_id="TEST-ASYNC"))

    assert response["data"]["status"] == "pending"
    assert submitted["kind"] == "log_export"
    assert callable(submitted["runner"])
    assert len(response["data"]["task_id"]) == 32


async def test_report_endpoint_returns_pending_without_running_job(monkeypatch, db_session):
    db_session.add(TestSession(test_id="TEST-ASYNC", operator_id="op", start_time="2026-06-10T00:00:00Z"))
    await db_session.commit()
    submitted = {}

    def submit(kind, runner, *, task_id=None):
        submitted.update(kind=kind, runner=runner)
        return "b" * 32

    monkeypatch.setattr(reports_route.background_jobs, "submit", submit)

    response = await reports_route.generate_report(
        reports_route.GenerateReportRequest(test_id="TEST-ASYNC"),
        CurrentUser("op", "operator"),
        db_session,
    )

    assert response["data"] == {"task_id": "b" * 32, "status": "pending", "progress": 0}
    assert submitted["kind"] == "report"
    assert callable(submitted["runner"])
