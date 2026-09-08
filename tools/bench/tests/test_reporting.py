from types import SimpleNamespace

import pytest
from smd_bench import reporting
from smd_bench.reporting import check_metrics


def metrics(**changes):
    return {
        "compliance": "not_certified",
        "measurement_data_integrity": "complete",
        "mode": "standard",
        "reference_displacement_mm": 20,
        "t10": 890,
        "t40": 1290,
        "ts": 1290,
        "delta_p_max": 1000,
        "td_drip_temp": 1290,
        "td_source": "first_drip_event",
        "delta_h_mm": 0,
        **changes,
    }


def test_fixed_oracle_detects_late_latched_temperature_and_false_complete():
    check_metrics(metrics(), "valid_drip")
    with pytest.raises(AssertionError):
        check_metrics(metrics(td_drip_temp=1580), "valid_drip")
    with pytest.raises(AssertionError):
        check_metrics(metrics(measurement_data_integrity="complete", td_drip_temp=None), "invalid_sample")


def test_no_drip_requires_source_completeness_and_distinct_origin():
    check_metrics(metrics(td_drip_temp=1580, td_source="completed_without_drip", delta_h_mm=2), "no_drip")
    with pytest.raises(AssertionError):
        check_metrics(
            metrics(td_drip_temp=1580, td_source="completed_without_drip", measurement_data_integrity="unknown"),
            "no_drip",
        )


PDF_ID = "BENCH-PDF-LABELS"
PDF_PROVENANCE = f"{PDF_ID}\n模拟实验\nnot_certified"
PDF_RESULTS = "结果指标\n炉温峰值\nT40 - T10\nTd - Ts\nTd - T10\n生成时间：2026-09-08 | smd-web-hmi 自动生成"


def inspect_pdf_pages(monkeypatch, tmp_path, pages):
    # Stub only the PDF extraction boundary; exercise the public acceptance gate.
    monkeypatch.setattr(
        reporting,
        "PdfReader",
        lambda _path: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda text=text: text) for text in pages]),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"synthetic PDF extraction fixture")
    return reporting.inspect_document(path, PDF_ID)


def test_pdf_acceptance_keeps_result_heading_with_its_first_metric(monkeypatch, tmp_path):
    result = inspect_pdf_pages(monkeypatch, tmp_path, [PDF_PROVENANCE, PDF_RESULTS])
    assert result["format"] == "pdf"


@pytest.mark.parametrize("label", ["T40 - T10", "Td - Ts", "Td - T10"])
@pytest.mark.parametrize("replacement", [" ", " − "])
def test_pdf_acceptance_rejects_missing_or_unsupported_minus(monkeypatch, tmp_path, label, replacement):
    broken = PDF_RESULTS.replace(label, label.replace(" - ", replacement))
    with pytest.raises(AssertionError):
        inspect_pdf_pages(monkeypatch, tmp_path, [PDF_PROVENANCE, broken])


@pytest.mark.parametrize("separator", ["", "·"])
def test_pdf_acceptance_requires_ascii_footer_separator(monkeypatch, tmp_path, separator):
    with pytest.raises(AssertionError):
        inspect_pdf_pages(monkeypatch, tmp_path, [PDF_PROVENANCE, PDF_RESULTS.replace("|", separator)])


def test_pdf_acceptance_rejects_heading_stranded_on_previous_page(monkeypatch, tmp_path):
    with pytest.raises(AssertionError):
        inspect_pdf_pages(
            monkeypatch,
            tmp_path,
            [PDF_PROVENANCE + "\n结果指标", PDF_RESULTS.removeprefix("结果指标\n")],
        )


def test_pdf_acceptance_requires_at_least_one_results_heading(monkeypatch, tmp_path):
    with pytest.raises(AssertionError):
        inspect_pdf_pages(monkeypatch, tmp_path, [PDF_PROVENANCE, PDF_RESULTS.removeprefix("结果指标\n")])


def test_pdf_acceptance_rejects_any_isolated_heading_even_with_another_valid_page(monkeypatch, tmp_path):
    with pytest.raises(AssertionError):
        inspect_pdf_pages(monkeypatch, tmp_path, [PDF_PROVENANCE, PDF_RESULTS, "结果指标"])
