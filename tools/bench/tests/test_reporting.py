import pytest
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
