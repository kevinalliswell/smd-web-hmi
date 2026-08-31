"""UTC 时间戳规范化、存储与查询边界测试。"""

from __future__ import annotations

import pytest

from app.api.routes.status import get_trends
from app.core.time import normalize_utc_iso
from app.db.models import SamplePoint, UserAccount
from app.hostcomm.protocol import now_iso


def test_now_iso_is_always_utc() -> None:
    assert now_iso().endswith("+00:00")


def test_normalize_utc_iso_converts_offsets_and_legacy_naive_values() -> None:
    assert normalize_utc_iso("2026-08-30T20:15:16+08:00") == "2026-08-30T12:15:16+00:00"
    assert normalize_utc_iso("2026-08-30T12:15:16Z") == "2026-08-30T12:15:16+00:00"
    assert normalize_utc_iso("2026-08-30T12:15:16") == "2026-08-30T12:15:16+00:00"

    with pytest.raises(ValueError):
        normalize_utc_iso("not-a-timestamp")


async def test_timestamp_columns_normalize_values_on_write(db_session) -> None:
    user = UserAccount(
        username="utc-user",
        hashed_pw="hash",
        role="observer",
        is_active=1,
        locked_until="2026-08-30T21:15:16+08:00",
        created_at="2026-08-30T20:15:16+08:00",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.created_at == "2026-08-30T12:15:16+00:00"
    assert user.locked_until == "2026-08-30T13:15:16+00:00"


async def test_trend_query_normalizes_time_window_to_utc(db_session) -> None:
    db_session.add_all(
        [
            SamplePoint(test_id="TEST-UTC", ts="2026-08-30T11:59:59+00:00", source="test"),
            SamplePoint(test_id="TEST-UTC", ts="2026-08-30T12:00:00+00:00", source="test"),
        ]
    )
    await db_session.commit()

    response = await get_trends(
        db_session,
        from_ts="2026-08-30T20:00:00+08:00",
        test_id="TEST-UTC",
    )

    assert response["data"]["total"] == 1
    assert response["data"]["points"][0]["ts"] == "2026-08-30T12:00:00+00:00"
