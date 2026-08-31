"""历史试验接口测试：详情摘要 / 曲线降采样 / 事件 / 报警（规格 3.4）。"""

from __future__ import annotations

from app.api.routes import tests as tests_route
from app.api.routes.status import get_trends
from app.db.models import AlarmLog, EventLog, SamplePoint, TestSession


async def _seed(db, test_id="TEST-H-1", n=250):
    db.add(
        TestSession(
            test_id=test_id,
            operator_id="adm",
            start_time="2026-06-10T00:00:00",
            end_time="2026-06-10T01:00:00",
            end_reason="normal",
        )
    )
    for i in range(n):
        db.add(
            SamplePoint(
                test_id=test_id,
                ts=f"2026-06-10T00:{i // 60:02d}:{i % 60:02d}",
                source="live_poll",
                furnace_pv=100.0 + i,
                burden_temp=90.0 + i,
                delta_p=float(i),
                displacement=i * 0.1,
                drip_weight=0.0,
                current_state="Heating",
            )
        )
    db.add(
        EventLog(test_id=test_id, ts="2026-06-10T00:00:05", source="stm32_event", event_code="EVT-1", level=1, text="x")
    )
    db.add(AlarmLog(test_id=test_id, alarm_code="ALM-1", level=2, occur_time="2026-06-10T00:00:06", text="压差高"))
    await db.commit()
    return test_id


async def test_detail_summary(db_session):
    tid = await _seed(db_session, n=30)
    res = await tests_route.test_detail(tid, db_session)
    data = res["data"]
    assert data["sample_count"] == 30
    assert data["alarm_count"] == 1
    assert data["end_reason"] == "normal"


async def test_samples_downsampling(db_session):
    tid = await _seed(db_session, n=250)
    res = await tests_route.test_samples(tid, db_session, max_points=100)
    data = res["data"]
    assert data["total"] == 250
    assert data["stride"] >= 3  # 250/100 -> 3
    assert len(data["points"]) <= 100
    # 字段齐全
    p = data["points"][0]
    assert "furnace_pv" in p and "delta_p" in p and "ts" in p


async def test_cross_test_trends_downsample_in_database(db_session):
    tid = await _seed(db_session, test_id="TEST-TREND-SQL", n=250)

    result = await get_trends(db_session, test_id=tid, max_points=80)
    data = result["data"]

    assert data["total"] == 250
    assert data["stride"] == 4
    assert len(data["points"]) <= 80
    assert data["points"][0]["test_id"] == tid


async def test_events_and_alarms(db_session):
    tid = await _seed(db_session, n=5)
    ev = await tests_route.test_events(tid, db_session)
    al = await tests_route.test_alarms(tid, db_session)
    assert len(ev["data"]) == 1 and ev["data"][0]["event_code"] == "EVT-1"
    assert len(al["data"]) == 1 and al["data"][0]["level"] == 2
