"""报告生成与日志导出测试（规格 3.7 / 3.8 / SOP §13）。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.db.models import AlarmLog, ParameterSnapshot, ReportExport, SamplePoint, TestSession
from app.services import log_export_service, report_service
from app.services.test_id import InvalidTestIdError


def _sample(furnace, burden, dp, drip, disp):
    return SimpleNamespace(furnace_pv=furnace, burden_temp=burden, delta_p=dp, drip_weight=drip, displacement=disp)


# ----------------------------------------------------- 指标计算（单元）
def test_legacy_samples_without_quality_do_not_produce_standard_results():
    samples = [_sample(600, 590, 20, 10, 30), _sample(1000, 990, 500, 20, 20)]
    m = report_service.compute_metrics(samples, original_height_mm=20.0)
    assert m["t10"] is None
    assert m["td_drip_temp"] is None
    assert "invalid_or_unknown_quality" in m["limitations"]


def test_compute_metrics_no_height():
    samples = [_sample(200, 180, 5.0, 1.0, 2.0)]
    m = report_service.compute_metrics(samples, original_height_mm=None)
    assert m["t10"] is None and m["t40"] is None  # 缺 H 不臆造


async def _seed(db, test_id="TEST-R-1"):
    db.add(
        TestSession(
            test_id=test_id,
            operator_id="adm",
            start_time="2026-06-10T00:00:00",
            original_height_mm=5.0,
            sample_label="SAMPLE-01",
        )
    )
    for i in range(6):
        db.add(
            SamplePoint(
                test_id=test_id,
                ts=f"2026-06-10T00:00:{i:02d}",
                source="live_poll",
                furnace_pv=[100, 600, 900, 1100, 1300, 1600][i],
                burden_temp=[90, 590, 890, 1090, 1290, 1580][i],
                delta_p=[0, 0, 100, 500, 1000, 800][i],
                drip_weight=0.0 if i < 3 else float(i),
                displacement=[6, 5.5, 5, 3.5, 3, 2][i],
                ext_json=json.dumps({"measurement": {"first_drip": i == 4, "drip_weight_valid": True}}),
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


async def test_generate_report_uses_unique_files_for_same_test(db_session, monkeypatch, tmp_path):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "reports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session, "TEST-CONCURRENT-REPORT")

    first = await report_service.generate_report(db_session, test_id, operator_id="adm")
    second = await report_service.generate_report(db_session, test_id, operator_id="adm")

    assert first.file_path != second.file_path
    assert Path(first.file_path).exists()
    assert Path(second.file_path).exists()


@pytest.mark.parametrize(
    ("fmt", "suffix", "magic"),
    [
        ("pdf", ".pdf", b"%PDF"),
        ("xlsx", ".xlsx", b"PK"),
    ],
)
async def test_generate_commercial_report_formats(db_session, monkeypatch, tmp_path, fmt, suffix, magic):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "reports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session, f"TEST-{fmt.upper()}")

    record = await report_service.generate_report(db_session, test_id, operator_id="adm", fmt=fmt)

    path = Path(record.file_path)
    assert record.format == fmt
    assert path.suffix == suffix
    assert path.read_bytes().startswith(magic)
    assert record.file_size_bytes > 100


async def test_report_prefers_stored_original_height(db_session, monkeypatch, tmp_path):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "reports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session, "TEST-STORED-H")

    record = await report_service.generate_report(
        db_session,
        test_id,
        operator_id="adm",
        options={"original_height_mm": 999},
    )

    assert '"original_height_mm": 5.0' in record.notes


async def test_xlsx_escapes_formula_like_external_text(db_session, monkeypatch, tmp_path):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "reports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session, "TEST-XLSX-SAFE")
    test = await db_session.scalar(select(TestSession).where(TestSession.test_id == test_id))
    test.sample_label = '=HYPERLINK("https://example.invalid")'
    await db_session.commit()

    record = await report_service.generate_report(db_session, test_id, operator_id="adm", fmt="xlsx")
    workbook = load_workbook(record.file_path, data_only=False)
    cell = workbook["试验摘要"]["B7"]

    assert cell.data_type == "s"
    assert cell.value.startswith("'=HYPERLINK")


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


async def test_log_export_enforces_uncompressed_size_limit(db_session, monkeypatch, tmp_path):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "exports_dir", property(lambda self: tmp_path))
    test_id = await _seed(db_session, "TEST-LIMIT")

    with pytest.raises(log_export_service.ExportSizeLimitError):
        await log_export_service.export_test_logs(
            db_session,
            test_id,
            task_id="a" * 32,
            max_uncompressed_bytes=10,
        )

    assert not (tmp_path / f"{'a' * 32}.zip").exists()


async def test_log_export_rejects_unsafe_test_id_before_creating_archive(db_session, monkeypatch, tmp_path):
    from app.core import config

    monkeypatch.setattr(type(config.get_settings()), "exports_dir", property(lambda self: tmp_path))

    with pytest.raises(InvalidTestIdError):
        await log_export_service.export_test_logs(db_session, "../outside", task_id="a" * 32)

    assert list(tmp_path.glob("*.zip")) == []


async def test_report_metrics_are_computed_in_database(db_session):
    test_id = await _seed(db_session, "TEST-SQL-METRICS")

    metrics = await report_service.compute_metrics_from_database(db_session, test_id, original_height_mm=2.0)

    assert metrics["furnace_pv_max"] == 1600.0
    assert metrics["burden_temp_max"] == 1580.0
    assert metrics["delta_p_max"] == 1000.0
    assert metrics["delta_p_max_temp"] == 1290.0
    assert metrics["td_drip_temp"] == 1290.0
    assert metrics["t10"] == 890.0
    assert metrics["t40"] == 1090.0
