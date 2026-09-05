"""GB/T 34211 §9 / 附录 B 的人工核算用例。"""

import json
from types import SimpleNamespace

import pytest

from app.services.standard_metrics import MetricAccumulator, evaluate_repeatability


def point(furnace, temp, height, dp=0, *, first_drip=False, valid=1):
    return SimpleNamespace(
        furnace_pv=furnace,
        burden_temp=temp,
        burden_temp_v=valid,
        displacement=height,
        displacement_v=valid,
        delta_p=dp,
        delta_p_v=valid,
        drip_weight=1 if first_drip else 0,
        ext_json=json.dumps({"measurement": {"first_drip": first_drip, "drip_weight_valid": True}}),
    )


def metrics(points, **kwargs):
    reducer = MetricAccumulator(20, **kwargs)
    for row in points:
        reducer.add(row)
    return reducer.finish()


def test_reference_600_and_height_decrease_not_absolute_displacement():
    result = metrics(
        [
            point(500, 490, 31),
            point(600, 590, 30),
            point(900, 890, 28),
            point(1100, 1090, 22, 500),
            point(1300, 1290, 20, 1000, first_drip=True),
        ]
    )
    assert result["reference_displacement_mm"] == 30
    assert result["t10"] == 890
    assert result["t40"] == 1090
    assert result["ts"] == 1090
    assert result["td_drip_temp"] == 1290
    assert result["delta_h_mm"] == 2
    assert result["t40_minus_t10"] == 200
    assert result["td_minus_ts"] == 200
    assert result["td_minus_t10"] == 400


def test_invalid_channels_and_unverified_weight_do_not_make_metrics():
    result = metrics([point(600, 590, 30), point(1000, 990, 20, 2000, first_drip=True, valid=0)])
    assert result["t10"] is None
    assert result["delta_p_max"] == 0
    assert result["td_drip_temp"] is None
    assert result["invalid_sample_count"] == 1
    weight_only = point(1000, 990, 20)
    weight_only.drip_weight = 100
    assert metrics([weight_only])["td_drip_temp"] is None


def test_missing_reference_and_gap_are_explicit():
    result = metrics([point(700, 690, 20), point(1000, 990, 10)])
    assert result["t10"] is None
    assert "reference_600_missing" in result["limitations"]
    gap = metrics([point(590, 580, 30), point(600, 590, 29, valid=0), point(610, 600, 28)])
    assert gap["reference_displacement_mm"] is None


def test_reference_interpolation_is_identified_as_estimate():
    result = metrics([point(590, 580, 31), point(610, 600, 29)])
    assert result["reference_displacement_mm"] == 30
    assert result["reference_source"] == "interpolated_at_600"
    assert "reference_600_interpolated" in result["limitations"]


def test_no_drip_1580_requires_valid_completed_measurement_and_detector():
    rows = [point(600, 590, 30), point(1600, 1580, 20)]
    assert metrics(rows)["td_drip_temp"] is None
    assert metrics(rows, measurement_complete=True, detector_verified=True, data_complete=True)["td_drip_temp"] == 1580
    assert metrics(rows, measurement_complete=True, detector_verified=True, data_complete=False)["td_drip_temp"] is None


@pytest.mark.parametrize(
    "values,expected,required",
    [
        ([1000, 1010], 1005, 0),
        ([1000, 1011], None, 1),
        ([1000, 1015, 1005], 1007, 0),
        ([1000, 1016], None, 2),
        ([1000, 1021], None, 2),
        ([1000, 1021, 1002, 1004], 1003, 0),
        ([1000, 1011, 1030], None, 1),
        ([1000, 1011, 1030, 1005], 1008, 0),
    ],
)
def test_appendix_b_repeat_branches(values, expected, required):
    result = evaluate_repeatability("t10", values)
    assert result["result"] == expected
    assert result["additional_runs"] == required


def test_td_has_its_own_tolerances_and_rounding_is_half_up():
    assert evaluate_repeatability("td_drip_temp", [1000, 1020])["result"] == 1010
    assert evaluate_repeatability("t10", [1000, 1001])["result"] == 1001
    with pytest.raises(ValueError):
        evaluate_repeatability("t10", [float("nan"), 1000])
