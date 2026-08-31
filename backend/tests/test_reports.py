"""报告生成与日志导出测试（规格 3.7 / 3.8 / SOP §13）。"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import AlarmLog, ParameterSnapshot, ReportExport, SamplePoint, TestSession
from app.services import log_export_service, report_service
from app.services.test_id import InvalidTestIdError


def _sample(furnace, burden, dp, drip, disp):
    return SimpleNamespace(furnace_pv=furnace, burden_temp=burden, delta_p=dp, drip_weight=drip, displacement=disp)


# ----------------------------------------------------- 指标计算（单元）
def test_compute_metrics():
    samples = [
        _sample(100 + i * 150, 90 + i * 150, i * 10.0, 0.0 if i < 5 else float(i - 4), i * 0.5) for i in range(10)
    ]
    m = report_service.compute_metrics(samples, original_height_mm=10.0)
    assert m["delta_p_max"] == 90.0
    assert m["delta_p_max_temp"] == 1440.0  # i=9
    assert m["td_drip_temp"] == 840.0  # i=5 首次滴落 >0.5g
    assert m["displacement_max"] == 4.5
    assert m["t10"] == 390.0  # 位移≥1.0 (10%*10) 在 i=2
    assert m["t40"] == 1290.0  # 位移≥4.0 (40%*10) 在 i=8


def test_compute_metrics_no_height():
    samples = [_sample(200, 180, 5.0, 1.0, 2.0)]
    m = report_service.compute_metrics(samples, original_height_mm=None)
    assert m["t10"] is None and m["t40"] is None  # 缺 H 不臆造


async def _seed(db, test_id="TEST-R-1"):
    db.add(TestSession(test_id=test_id, operator_id="adm", start_time="2026-06-10T00:00:00"))
    for i in range(6):
        db.add(
            SamplePoint(
                test_id=test_id,
                ts=f"2026-06-10T00:00:{i:02d}",
                source="live_poll",
                furnace_pv=100.0 + i * 200,
                burden_temp=90.0 + i * 200,
                delta_p=float(i * 20),
                drip_weight=0.0 if i < 3 else float(i),
                displacement=float(i * 0.4),
            )
        )
    db.add(AlarmLog(test_id=test_id, alarm_code="ALM-X", level=2, occur_time="2026-06-10T00:00:03", text="压差高"))
    await db.commit()
    return test_id


# ----------------------------------------------------- 报告生成（落盘 + 登记）
async def test_generate_report(db_session, monkeypatch, tmp_path):
    from app.core import config

    # 报告写入临时目录
    monkeypatch.setattr(type(config.get_settings()), "reports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session)

    record = await report_service.generate_report(db_session, test_id, operator_id="adm")
    assert Path(record.file_path).exists()
    assert record.format == "html"
    assert record.file_size_bytes > 0
    # 登记 report_export 且 test_session.report_path 写入
    assert await db_session.scalar(select(func.count()).select_from(ReportExport)) == 1
    test = await db_session.scalar(select(TestSession).where(TestSession.test_id == test_id))
    assert test.report_path == record.file_path
    content = Path(record.file_path).read_text(encoding="utf-8")
    assert test_id in content
    assert "ΔPmax" in content
    assert Path(record.file_path).resolve().is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("test_id", ["../outside", r"..\outside", "bad:name", "bad*name", "x" * 65])
async def test_generate_report_rejects_unsafe_test_id_before_file_access(db_session, monkeypatch, tmp_path, test_id):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "reports_dir", property(lambda self: tmp_path))
    db_session.add(TestSession(test_id=test_id, operator_id="adm", start_time="2026-06-10T00:00:00Z"))
    await db_session.commit()
    html_files_before = set(tmp_path.parent.rglob("*.html"))

    with pytest.raises(InvalidTestIdError):
        await report_service.generate_report(db_session, test_id, operator_id="adm")

    assert set(tmp_path.parent.rglob("*.html")) == html_files_before


async def test_report_never_falls_back_to_unrelated_global_parameter_snapshot(db_session, monkeypatch, tmp_path):
    """缺少本试验快照时明确显示缺失，不能引用另一炉的全局最新参数。"""
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "reports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session, "TEST-NO-PARAMS")
    db_session.add(
        ParameterSnapshot(
            test_id="OTHER-TEST",
            ts="2026-06-10T00:00:00Z",
            operator_id="adm",
            source="test_start",
            params_json='{"marker":"UNRELATED-GLOBAL"}',
        )
    )
    await db_session.commit()

    record = await report_service.generate_report(db_session, test_id, operator_id="adm")
    content = Path(record.file_path).read_text(encoding="utf-8")

    assert "UNRELATED-GLOBAL" not in content
    assert "无本试验参数快照" in content


# ----------------------------------------------------- 日志导出（zip + 条目）
async def test_export_logs(db_session, monkeypatch, tmp_path):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "exports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session, "TEST-EXP-1")

    result = await log_export_service.export_test_logs(db_session, test_id)
    zip_path = Path(result["file_path"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert any("sample_point.csv" in n for n in names)
        assert any("event_log.csv" in n for n in names)
        assert any("alarm_log.csv" in n for n in names)
        assert any("parameter_snapshot.csv" in n for n in names)
        sample_csv = zf.read(f"{test_id}_sample_point.csv").decode("utf-8")
        assert sample_csv.count("\n") >= 7  # 表头 + 6 行
