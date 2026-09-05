"""GB/T 34211-2017 §9：有来源、保留缺口的流式指标及附录 B 判定。"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.snapshot_data import capabilities, json_object, object_value
from app.services.state_policy import normalize_state

ALGORITHM_VERSION = "gb34211-2017/2"


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


class MetricAccumulator:
    """按采集顺序消费数据，内存不随试验长度增长。位移采用国标 H600-Ht 方向。"""

    def __init__(
        self,
        original_height_mm,
        *,
        measurement_complete=False,
        detector_verified=False,
        data_complete=False,
        measurement_end_sample_id=None,
        test_id=None,
    ):
        self.height = number(original_height_mm)
        self.complete = measurement_complete and detector_verified and data_complete
        self.previous = None
        self.measurement_finished = False
        self.measurement_end_sample_id = measurement_end_sample_id
        self.test_id = test_id
        self.detector_healthy = True
        self.previous_callbacks = None
        self.hs = self.hd = self.height_at_1580 = None
        self.limitations: set[str] = set()
        self.result = dict.fromkeys(
            [
                "furnace_pv_max",
                "burden_temp_max",
                "delta_p_max",
                "delta_p_max_temp",
                "drip_weight_total",
                "td_drip_temp",
                "displacement_max",
                "t10",
                "t40",
                "ts",
                "delta_h_mm",
                "reference_displacement_mm",
                "reference_source",
            ]
        )
        self.result.update(
            algorithm_version=ALGORITHM_VERSION,
            original_height_mm=self.height,
            invalid_sample_count=0,
            sample_count=0,
            measurement_sample_count=0,
            excluded_sample_count=0,
        )

    def _max(self, key, value):
        if value is not None and (self.result[key] is None or value > self.result[key]):
            self.result[key] = value
            return True
        return False

    def add(self, sample) -> None:
        r = self.result
        r["sample_count"] += 1
        extra, metadata_ok = json_object(getattr(sample, "ext_json", None))
        nested = ("measurement", "state_machine", "_hostcomm", "_hmi")
        metadata_ok = metadata_ok and all(key not in extra or isinstance(extra[key], dict) for key in nested)
        measurement = object_value(extra.get("measurement"))
        transport = object_value(extra.get("_hostcomm"))
        hmi = object_value(extra.get("_hmi"))
        state = object_value(extra.get("state_machine"))
        caps = capabilities(extra)
        after_boundary = (
            self.measurement_end_sample_id is not None and getattr(sample, "id", 0) > self.measurement_end_sample_id
        )
        disposal = normalize_state(state.get("current_state") or getattr(sample, "current_state", None)) in {
            "n2replace",
            "replace",
            "cooling",
            "cool",
            "endpurge",
            "purge2",
            "cooldown",
            "done",
            "finished",
            "complete",
            "completed",
        }
        if self.measurement_finished or after_boundary or disposal:
            r["excluded_sample_count"] += 1
            return
        if not metadata_ok:
            self.limitations.add("malformed_sample_metadata")
            self.detector_healthy = False
        if (
            "run_lifecycle_v1" in caps
            and state.get("measurement_complete") is True
            and (self.test_id is None or state.get("test_id") == self.test_id)
        ):
            self.measurement_finished = True  # 包含测定终帧，排除其后的安全处置。
        furnace = number(sample.furnace_pv)
        temp = number(sample.burden_temp) if getattr(sample, "burden_temp_v", None) == 1 else None
        disp = number(sample.displacement) if getattr(sample, "displacement_v", None) == 1 else None
        dp = number(sample.delta_p) if getattr(sample, "delta_p_v", None) == 1 else None
        callbacks = transport.get("dropped_callbacks")
        callback_gap = (
            type(callbacks) is int and self.previous_callbacks is not None and callbacks > self.previous_callbacks
        )
        if type(callbacks) is int:
            self.previous_callbacks = callbacks
        issues = hmi.get("telemetry_issues")
        if callback_gap or hmi.get("persistence_failures") or (isinstance(issues, list) and issues):
            self.limitations.add("transport_data_gap")
            self.previous = None
        if isinstance(issues, list) and any(code in issues for code in ["sequence_duplicate", "sequence_reordered"]):
            r["excluded_sample_count"] += 1
            return
        if r["reference_displacement_mm"] is None and furnace is not None and disp is not None:
            if furnace == 600:
                r.update(reference_displacement_mm=disp, reference_source="sample_at_600")
            elif self.previous is not None:
                old_furnace, old_disp = self.previous
                if old_furnace < 600 < furnace:
                    fraction = (600 - old_furnace) / (furnace - old_furnace)
                    r.update(
                        reference_displacement_mm=old_disp + fraction * (disp - old_disp),
                        reference_source="interpolated_at_600",
                    )
                    self.limitations.add("reference_600_interpolated")
        self.previous = (furnace, disp) if furnace is not None and disp is not None else None
        # 气密试验和升温至600℃之前的数据保留原曲线，不进入测定指标。
        if furnace is None or furnace < 600:
            return
        r["measurement_sample_count"] += 1
        if None in (temp, disp, dp):
            r["invalid_sample_count"] += 1
        self._max("furnace_pv_max", furnace)
        self._max("burden_temp_max", temp)
        self._max("displacement_max", disp)
        if measurement.get("drip_weight_valid") is True:
            self._max("drip_weight_total", number(sample.drip_weight))
        detector_valid = (
            "measurement_events_v1" in caps
            and measurement.get("first_drip_valid") is True
            and type(measurement.get("first_drip")) is bool
        )
        if not detector_valid:
            self.detector_healthy = False
        if self._max("delta_p_max", dp):
            r["delta_p_max_temp"] = temp
        if dp is not None and dp >= 500 and temp is not None and r["ts"] is None:
            r["ts"], self.hs = temp, disp
        reference = r["reference_displacement_mm"]
        if reference is not None and disp is not None and temp is not None and self.height and self.height > 0:
            shrink = (reference - disp) / self.height
            for key, threshold in [("t10", 0.1), ("t40", 0.4)]:
                if r[key] is None and shrink >= threshold:
                    r[key] = temp
        if temp is not None and temp >= 1580 and self.height_at_1580 is None:
            self.height_at_1580 = disp
        # first_drip 必须来自已确认的检测事件，重量超过固定阈值不等价于首滴。
        if detector_valid and measurement.get("first_drip") is True and temp is not None and r["td_drip_temp"] is None:
            r["td_drip_temp"], self.hd = temp, disp

    def finish(self) -> dict[str, Any]:
        r = dict(self.result)
        if r["reference_displacement_mm"] is None:
            self.limitations.add("reference_600_missing")
        if not self.height or self.height <= 0:
            self.limitations.add("original_height_missing")
        if not r["measurement_sample_count"]:
            self.limitations.add("measurement_samples_missing")
        if r["invalid_sample_count"]:
            self.limitations.add("invalid_or_unknown_quality")
        if r["td_drip_temp"] is None:
            if (
                self.complete
                and self.detector_healthy
                and self.height_at_1580 is not None
                and not r["invalid_sample_count"]
                and "transport_data_gap" not in self.limitations
                and "malformed_sample_metadata" not in self.limitations
            ):
                r["td_drip_temp"] = 1580
                self.hd = self.height_at_1580
                r["td_source"] = "completed_without_drip"
            else:
                self.limitations.add("first_drip_not_verified")
        else:
            r["td_source"] = "first_drip_event"
        r["delta_h_mm"] = self.hs - self.hd if self.hs is not None and self.hd is not None else None
        for key, high, low in [
            ("t40_minus_t10", "t40", "t10"),
            ("td_minus_ts", "td_drip_temp", "ts"),
            ("td_minus_t10", "td_drip_temp", "t10"),
        ]:
            r[key] = r[high] - r[low] if r[high] is not None and r[low] is not None else None
        r["limitations"] = sorted(self.limitations)
        return r


def evaluate_repeatability(metric: str, values: list[float]) -> dict[str, Any]:
    """附录 B 顺序判定；values 保持实际测定顺序，最后按十进制四舍五入到个位。"""
    tolerances = {"t10": (10, 15, 20), "t40": (10, 15, 20), "ts": (10, 15, 20), "td_drip_temp": (20, 25, 30)}
    if metric not in tolerances or not 2 <= len(values) <= 4:
        raise ValueError("仅接受 T10/T40/Ts/Td 的 2–4 次顺序测定")
    if any(number(value) is None for value in values):
        raise ValueError("重复性判定要求全部测定值有效且有限")
    a, b, c = tolerances[metric]
    delta = abs(values[0] - values[1])
    chosen = values[:2]
    needed = 0
    if delta > a:
        if delta <= b:
            needed = 3 - len(values)
            if len(values) >= 3:
                chosen = values[:3]
                if max(chosen) - min(chosen) > b:
                    needed = 4 - len(values)
                    chosen = values[:4]
        else:
            needed = 4 - len(values)
            chosen = values[:4]
        if len(chosen) == 4 and (delta > c or max(chosen) - min(chosen) > c):
            chosen = sorted(chosen)[1:3]
    mean = None
    if needed <= 0:
        mean = int((sum(Decimal(str(v)) for v in chosen) / len(chosen)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {
        "metric": metric,
        "values": values,
        "result": mean,
        "additional_runs": max(0, needed),
        "used_values": chosen if mean is not None else [],
        "tolerances": {"A": a, "B": b, "C": c},
        "rules_version": ALGORITHM_VERSION,
    }
