"""趋势查询接口测试（时间窗过滤 + 降采样 + 跨试验）。"""

from __future__ import annotations

from app.api.routes import status as status_route
from app.db.models import SamplePoint, TestSession


async def _seed(db):
    for tid in ("TEST-A", "TEST-B"):
        db.add(TestSession(test_id=tid, operator_id="adm", start_time="2026-06-10T00:00:00"))
    # TEST-A: 00:00:00..00:00:09 ；TEST-B: 00:10:00..00:10:09
    for i in range(10):
        db.add(
            SamplePoint(
                test_id="TEST-A",
                ts=f"2026-06-10T00:00:{i:02d}",
                source="live_poll",
                furnace_pv=float(i),
                n2_pv=1.0,
                co_pv=0.0,
            )
        )
    for i in range(10):
        db.add(
            SamplePoint(
                test_id="TEST-B",
                ts=f"2026-06-10T00:10:{i:02d}",
                source="live_poll",
                furnace_pv=float(100 + i),
                n2_pv=2.0,
                co_pv=1.0,
            )
        )
    await db.commit()


async def test_trends_time_window(db_session):
    await _seed(db_session)
    # 仅取 TEST-A 时间窗
    res = await status_route.get_trends(db_session, from_ts="2026-06-10T00:00:00", to_ts="2026-06-10T00:00:09")
    data = res["data"]
    assert data["total"] == 10
    assert all(p["test_id"] == "TEST-A" for p in data["points"])
    assert "n2_pv" in data["points"][0] and "co_pv" in data["points"][0]


async def test_trends_cross_test_and_downsample(db_session):
    await _seed(db_session)
    res = await status_route.get_trends(db_session, max_points=5)
    data = res["data"]
    assert data["total"] == 20  # 跨两个试验
    assert data["stride"] >= 4
    assert len(data["points"]) <= 5


async def test_trends_filter_by_test(db_session):
    await _seed(db_session)
    res = await status_route.get_trends(db_session, test_id="TEST-B")
    data = res["data"]
    assert data["total"] == 10
    assert all(p["test_id"] == "TEST-B" for p in data["points"])
