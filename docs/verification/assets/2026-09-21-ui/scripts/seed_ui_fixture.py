"""PR #84 UI 真机验收夹具：向验收用独立 SQLite 追加合成历史试验与采样。

用途：让数据分析页能勾选 8 个试验、历史页有双栏内容可看。数据为合成夹具，
仅写入验收专用数据库（SMD_DB_PATH 指向 .cache 下的临时库），不接真实设备、
不改动任何现场数据库。只做 INSERT，不删除也不更新既有记录。

运行：
    SMD_DB_PATH=<验收库> python seed_ui_fixture.py
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# backend 包路径（本脚本位于 docs/verification/assets/2026-09-21-ui/scripts/）
REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.db.database import get_sessionmaker  # noqa: E402
from app.db.models import AlarmLog, EventLog, SamplePoint, TestSession  # noqa: E402

TEST_COUNT = 8
POINTS_PER_TEST = 180
SAMPLE_STEP_S = 20


def _curve(i: int, k: int) -> dict[str, float]:
    """按试验序号做出彼此可区分、但都符合熔滴试验形状的曲线。

    升温指数随试验序号变化，使 8 条叠加曲线在同一坐标系里明显分开——
    否则叠加图会几乎重合，无法用于“8 色可区分”的目检。
    """
    frac = k / (POINTS_PER_TEST - 1)
    rng = random.Random(i * 9973 + k)
    # 炉温：室温 → 1600 ℃ 分段升温；指数 0.62…1.32 对应不同升温速度
    exponent = 0.62 + 0.10 * i
    furnace = 25 + 1575 * min(1.0, frac * 1.05) ** exponent
    burden = furnace - 40 - 25 * math.sin(frac * math.pi)
    # 压差：软化-熔融区间出现峰
    peak_pos = 0.62 + 0.03 * (i - 4)
    delta_p = 300 + 26000 * math.exp(-(((frac - peak_pos) / 0.085) ** 2)) + rng.uniform(-120, 120)
    # 位移：收缩到 -12 ~ -18 mm
    displacement = -(1 + 15 * (1 / (1 + math.exp(-(frac - 0.58) * 16)))) - 0.25 * i
    # 滴落重量：熔滴后累积
    drip = max(0.0, 420 * (1 / (1 + math.exp(-(frac - 0.78) * 18))) - 2) + i * 3
    return {
        "furnace_pv": round(furnace, 2),
        "furnace_sv": round(furnace + 8, 2),
        "burden_temp": round(burden, 2),
        "delta_p": round(max(0.0, delta_p), 1),
        "displacement": round(displacement, 3),
        "drip_weight": round(drip, 2),
        "n2_pv": 3.5 if frac > 0.3 else 5.0,
        "co_pv": 1.5 if frac > 0.3 else 0.0,
        "temp_output_pct": round(min(100.0, 30 + 60 * frac), 1),
    }


async def main() -> None:
    maker = get_sessionmaker()
    batch = int(os.environ.get("SEED_BATCH", "1"))
    base = datetime.now(timezone.utc) - timedelta(days=(TEST_COUNT + 1) * batch)
    async with maker() as db:
        for i in range(TEST_COUNT):
            day = base + timedelta(days=i)
            test_id = f"T{day.strftime('%Y%m%d')}-{i + 1:03d}"
            start = day.replace(hour=8, minute=30, second=0, microsecond=0)
            end = start + timedelta(seconds=POINTS_PER_TEST * SAMPLE_STEP_S)
            db.add(
                TestSession(
                    test_id=test_id,
                    operator_id="admin",
                    start_time=start.isoformat(),
                    end_time=end.isoformat(),
                    end_reason="completed",
                    state_at_end="End",
                    original_height_mm=round(72.0 + i * 0.8, 1),
                    sample_label=f"合成夹具试样 S-{i + 1:02d}（烧结矿 A 批）",
                    notes="PR #84 UI 验收合成夹具，非真实试验数据。",
                    phase="completed",
                    mode="standard",
                    measurement_completed_at=(end - timedelta(minutes=12)).isoformat(),
                    safety_completed_at=end.isoformat(),
                    data_integrity="complete",
                )
            )
            for k in range(POINTS_PER_TEST):
                ts = start + timedelta(seconds=k * SAMPLE_STEP_S)
                db.add(
                    SamplePoint(
                        test_id=test_id,
                        ts=ts.isoformat(),
                        source="live_poll",
                        current_state="Heating" if k < POINTS_PER_TEST * 0.8 else "N2Replace",
                        safety_relay=1,
                        **_curve(i, k),
                    )
                )
            db.add(
                EventLog(
                    test_id=test_id,
                    ts=start.isoformat(),
                    source="hmi",
                    event_code="test_start",
                    level=0,
                    text="试验启动（UI 验收夹具）",
                    operator_id="admin",
                    detail_json='{"note": "UI 验收夹具"}',
                )
            )
            if i % 3 == 0:
                db.add(
                    AlarmLog(
                        test_id=test_id,
                        alarm_code="ALM-DP-HIGH",
                        level=2,
                        text="料层压差高：> 30 kPa（合成夹具）",
                        occur_time=(start + timedelta(minutes=40)).isoformat(),
                        clear_time=(start + timedelta(minutes=46)).isoformat(),
                    )
                )
        await db.commit()
    print(f"seeded {TEST_COUNT} tests x {POINTS_PER_TEST} points")


if __name__ == "__main__":
    if not os.environ.get("SMD_DB_PATH"):
        raise SystemExit("必须显式设置 SMD_DB_PATH，避免误写默认库")
    asyncio.run(main())
