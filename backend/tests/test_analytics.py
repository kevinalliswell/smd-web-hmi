"""多试验对比接口测试（规格 7.3）。"""

from __future__ import annotations

from app.api.routes import analytics as analytics_route
from app.db.models import SamplePoint, TestSession


async def _seed(db):
    for tid, base in (("TEST-A", 0), ("TEST-B", 1000)):
        db.add(
            TestSession(
                test_id=tid,
                operator_id="adm",
                start_time="2026-06-10T00:00:00",
                original_height_mm=10.0,
            )
        )
        for i in range(6):
            db.add(
                SamplePoint(
                    test_id=tid,
                    ts=f"2026-06-10T00:00:{i:02d}",
                    source="live_poll",
                    burden_temp=float(base + i * 100),
                    delta_p=float(i * 10),
                    displacement=i * 0.5,
                    drip_weight=0.0 if i < 3 else float(i),
                )
            )
    await db.commit()


async def test_compare_two_tests(db_session):
    await _seed(db_session)
    res = await analytics_route.compare(db_session, test_ids="TEST-A,TEST-B", original_height_mm=10.0)
    data = res["data"]
    assert len(data) == 2
    a = next(d for d in data if d["test_id"] == "TEST-A")
    assert a["sample_count"] == 6
    assert a["metrics"]["delta_p_max"] == 50.0
    assert a["metrics"]["t10"] is not None  # 提供 H，可算 T10


async def test_compare_skips_missing(db_session):
    await _seed(db_session)
    res = await analytics_route.compare(db_session, test_ids="TEST-A,NOPE")
    assert [d["test_id"] for d in res["data"]] == ["TEST-A"]


async def test_compare_uses_sql_metrics_and_stored_height(monkeypatch, db_session):
    await _seed(db_session)

    def fail_if_loaded(*args, **kwargs):
        raise AssertionError("analytics must not load all sample rows into Python")

    monkeypatch.setattr(analytics_route.report_service, "compute_metrics", fail_if_loaded)
    res = await analytics_route.compare(db_session, test_ids="TEST-A")

    assert res["data"][0]["metrics"]["original_height_mm"] == 10.0
    assert res["data"][0]["sample_count"] == 6
