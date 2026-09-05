"""试验编号建议接口回归测试（issue #14）。"""

from app.api.routes.tests import next_test_id
from app.db.models import TestSession


async def test_next_test_id_increments_highest_sequence_for_day(db_session):
    db_session.add_all(
        [
            TestSession(test_id="TEST-20260610-001", operator_id="op", start_time="2026-06-10T01:00:00Z"),
            TestSession(test_id="TEST-20260610-003", operator_id="op", start_time="2026-06-10T02:00:00Z"),
            TestSession(test_id="TEST-20260609-099", operator_id="op", start_time="2026-06-09T01:00:00Z"),
        ]
    )
    await db_session.commit()

    result = await next_test_id(db_session, day="20260610")

    assert result["data"]["test_id"] == "TEST-20260610-004"
