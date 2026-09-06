"""GB/T 34211-2017 §9：有来源、保留缺口的流式指标及附录 B 判定。"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.hostcomm.v2_contract.codec import digest
from app.hostcomm.v2_contract.messages import FirstDripEvent, ProfileSnapshot, Telemetry
from app.hostcomm.v2_contract.types import SampleRef
from app.services.snapshot_data import capabilities, json_object, object_value
from app.services.state_policy import normalize_state

ALGORITHM_VERSION = "gb34211-2017/2"


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
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
        if "v2" in extra or getattr(sample, "source_boot_id", None) is not None:
            self.limitations.add("v2_measurement_basis_missing")
            r["excluded_sample_count"] += 1
            self.detector_healthy = False
            return
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
        terminal_measurement = (
            self.measurement_end_sample_id is not None and getattr(sample, "id", None) == self.measurement_end_sample_id
        ) or (
            "run_lifecycle_v1" in caps
            and state.get("measurement_complete") is True
            and (self.test_id is None or state.get("test_id") == self.test_id)
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
        # A trusted measurement boundary includes this frame even when the board
        # reports its new disposal state at the same time. Later frames stay closed.
        if disposal and not terminal_measurement:
            self.measurement_finished = True
        if self.measurement_finished or after_boundary:
            r["excluded_sample_count"] += 1
            return
        if not metadata_ok:
            self.limitations.add("malformed_sample_metadata")
            self.detector_healthy = False
        if terminal_measurement:
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
        detector_valid = (
            "measurement_events_v1" in caps
            and measurement.get("first_drip_valid") is True
            and type(measurement.get("first_drip")) is bool
        )
        self._add_values(
            furnace,
            temp,
            disp,
            dp,
            number(sample.drip_weight) if measurement.get("drip_weight_valid") is True else None,
            detector_valid,
            measurement.get("first_drip") is True,
        )

    def _add_values(self, furnace, temp, disp, dp, drip_weight, detector_valid, first_drip):
        """Shared scalar calculation; transport-specific evidence is checked before entry."""
        r = self.result
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
            r["excluded_sample_count"] += 1
            return
        r["measurement_sample_count"] += 1
        if None in (temp, disp, dp):
            r["invalid_sample_count"] += 1
        self._max("furnace_pv_max", furnace)
        self._max("burden_temp_max", temp)
        self._max("displacement_max", disp)
        self._max("drip_weight_total", drip_weight)
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
        if detector_valid and first_drip and temp is not None and r["td_drip_temp"] is None:
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


class V2MetricAccumulator(MetricAccumulator):
    """Reduce immutable fixed-point sources within one proven, inclusive measurement window.

    Caller orders rows by source boot and numeric sample sequence. Arrival IDs, receive
    timestamps and legacy display projections are never experiment evidence here.
    """

    def __init__(self, original_height_mm, *, v2_basis, profile_snapshot=None, **kwargs):
        super().__init__(original_height_mm, **kwargs)
        self.result["algorithm_version"] = "gb34211-2017/3-source-ref"
        self.basis = v2_basis if isinstance(v2_basis, dict) else {}
        self.start = self.end = self.last_sequence = None
        self.first_drip_event = None
        self.event_sample_seen = False
        self.event_detector_valid = True
        self.any_latched = False
        self.freshness = {}
        self.source_measurement_invalid = False
        try:
            self.start = SampleRef.model_validate(self.basis.get("measurement_start"))
            self.end = SampleRef.model_validate(self.basis.get("measurement_end"))
            if self.start.boot_id != self.end.boot_id:
                self.limitations.add("measurement_boundary_cross_boot")
                self.start = self.end = None
            elif int(self.end.sample_seq) < int(self.start.sample_seq):
                raise ValueError("reversed source window")
        except (ValueError, TypeError):
            self.start = self.end = None
            self.limitations.add("measurement_source_boundary_missing_or_invalid")
        self.complete = self.complete and (
            self.basis.get("measurement_complete") is True and self.basis.get("outcome") == "valid_candidate"
        )
        raw_profile = self.basis.get("profile_snapshot", profile_snapshot)
        try:
            profile = ProfileSnapshot.model_validate(raw_profile)
            content = profile.model_dump(exclude={"profile_digest"})
            expected = self.basis.get("safety_profile_digest")
            if expected != profile.profile_digest or digest(content) != profile.profile_digest:
                raise ValueError("profile digest does not match the immutable run")
            self.freshness = profile.resources.channel_freshness_ms.model_dump()
        except (ValueError, TypeError):
            self.limitations.add("freshness_profile_missing_or_invalid")
            self.complete = False
        self._read_events()

    def _inside(self, reference):
        return (
            self.start is not None
            and reference.boot_id == self.start.boot_id
            and int(self.start.sample_seq) <= int(reference.sample_seq) <= int(self.end.sample_seq)
        )

    def _point(self, value, name, divisor=1):
        limit = self.freshness.get(name, 0)
        if value.quality != "good" or value.value is None or value.age_ms > limit:
            return None
        return value.value / divisor

    def _read_events(self):
        records = self.basis.get("first_drip_events", [])
        if not isinstance(records, list):
            self.limitations.add("malformed_first_drip_event")
            self.complete = False
            return
        candidates = []
        for raw in records:
            try:
                if not isinstance(raw, dict):
                    raise ValueError("event must be an object")
                event = FirstDripEvent.model_validate({key: value for key, value in raw.items() if key != "boot_id"})
                if raw.get("boot_id") != event.sample.boot_id or event.run_id != self.basis.get("run_id"):
                    raise ValueError("event identity conflicts with the run")
                if not self._inside(event.sample):
                    self.limitations.add("first_drip_outside_measurement")
                    self.complete = False
                    continue
                if not event.is_valid or self._point(event.burden_mc, "burden_mc", 1000) is None:
                    self.limitations.add("invalid_first_drip_event")
                    self.complete = False
                    continue
                candidates.append(event)
            except (ValueError, TypeError):
                self.limitations.add("malformed_first_drip_event")
                self.complete = False
        if candidates:
            # Duplicate sources are deduplicated by the archive. Conflicting physical
            # first-event declarations must not silently select whichever arrived first.
            signatures = {
                (event.sample.boot_id, event.sample.sample_seq, event.burden_mc.value) for event in candidates
            }
            if len(signatures) != 1:
                self.limitations.add("conflicting_first_drip_events")
                self.complete = False
                return
            self.first_drip_event = min(candidates, key=lambda item: int(item.event_seq))

    def add(self, sample) -> None:
        r = self.result
        r["sample_count"] += 1
        extra, valid = json_object(getattr(sample, "ext_json", None))
        try:
            if not valid or not isinstance(extra.get("v2"), dict):
                raise ValueError("missing v2 source")
            telemetry = Telemetry.model_validate(extra["v2"].get("telemetry"))
            reference = telemetry.sample
            if (
                getattr(sample, "source_boot_id", None) != reference.boot_id
                or getattr(sample, "source_sequence", None) != reference.sample_seq
                or getattr(sample, "source_run_id", None) != telemetry.run_id
                or getattr(sample, "source_uptime_ms", None) != telemetry.sample_uptime_ms
            ):
                raise ValueError("archive index conflicts with immutable telemetry")
        except (ValueError, TypeError):
            self.limitations.add("malformed_v2_source")
            self.complete = self.detector_healthy = False
            self.previous = None
            r["invalid_sample_count"] += 1
            r["excluded_sample_count"] += 1
            return
        if telemetry.run_id != self.basis.get("run_id") or not self._inside(reference):
            r["excluded_sample_count"] += 1
            return
        sequence = int(reference.sample_seq)
        expected = int(self.start.sample_seq) if self.last_sequence is None else self.last_sequence + 1
        if self.last_sequence is not None and sequence <= self.last_sequence:
            self.limitations.add("source_sequence_duplicate_or_reordered")
            self.complete = False
            r["excluded_sample_count"] += 1
            return
        if sequence != expected:
            self.limitations.add("transport_data_gap")
            self.complete = False
            self.previous = None
        self.last_sequence = sequence
        values = telemetry.values
        furnace = self._point(values.furnace_mc, "furnace_mc", 1000)
        temperature = self._point(values.burden_mc, "burden_mc", 1000)
        displacement = self._point(values.displacement_um, "displacement_um", 1000)
        pressure = self._point(values.pressure_drop_pa, "pressure_drop_pa")
        if None in (furnace, temperature, displacement, pressure):
            self.source_measurement_invalid = True
            self.complete = False
        if furnace is None:
            r["invalid_sample_count"] += 1
        detector_valid = telemetry.first_drip_detector_quality == "good"
        if not detector_valid:
            self.detector_healthy = False
        self.any_latched = self.any_latched or telemetry.first_drip_latched
        self._add_values(
            furnace,
            temperature,
            displacement,
            pressure,
            self._point(values.drip_mass_mg, "drip_mass_mg", 1000),
            detector_valid,
            False,  # A latch never substitutes for the original event.
        )
        if self.first_drip_event and reference == self.first_drip_event.sample:
            self.event_sample_seen = True
            self.event_detector_valid = detector_valid
            self.hd = displacement

    def finish(self) -> dict[str, Any]:
        if self.end is None or self.last_sequence != int(self.end.sample_seq):
            self.limitations.add("measurement_end_sample_missing")
            self.complete = False
        if self.first_drip_event and self.event_detector_valid:
            self.result["td_drip_temp"] = self._point(self.first_drip_event.burden_mc, "burden_mc", 1000)
            if not self.event_sample_seen:
                self.limitations.add("first_drip_sample_missing")
        elif self.any_latched:
            self.limitations.add(
                "first_drip_event_missing" if self.first_drip_event is None else "first_drip_detector_conflict"
            )
            self.complete = False
        result = super().finish()
        result["source_measurement_invalid"] = bool(
            self.source_measurement_invalid
            or (self.end is not None and self.last_sequence != int(self.end.sample_seq))
            or not self.detector_healthy
            or {
                "transport_data_gap",
                "malformed_v2_source",
                "source_sequence_duplicate_or_reordered",
                "first_drip_event_missing",
                "first_drip_detector_conflict",
                "invalid_first_drip_event",
                "conflicting_first_drip_events",
                "malformed_first_drip_event",
                "first_drip_outside_measurement",
            }
            & self.limitations
        )
        result["measurement_start_ref"] = self.start.model_dump() if self.start else None
        result["measurement_end_ref"] = self.end.model_dump() if self.end else None
        result["td_event_id"] = (
            self.first_drip_event.event_id if result.get("td_source") == "first_drip_event" else None
        )
        return result


def evaluate_repeatability(metric: str, values: list[float]) -> dict[str, Any]:
    """附录 B 顺序判定；values 保持实际测定顺序，最后按十进制四舍五入到个位。"""
    tolerances = {"t10": (10, 15, 20), "t40": (10, 15, 20), "ts": (10, 15, 20), "td_drip_temp": (20, 25, 30)}
    if metric not in tolerances or not 2 <= len(values) <= 4:
        raise ValueError("仅接受 T10/T40/Ts/Td 的 2–4 次顺序测定")
    if any(number(value) is None for value in values):
        raise ValueError("重复性判定要求全部测定值有效且有限")
    raw_values = values
    values = [Decimal(str(value)) for value in values]
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
        "values": raw_values,
        "result": mean,
        "additional_runs": max(0, needed),
        "used_values": [float(value) for value in chosen] if mean is not None else [],
        "tolerances": {"A": a, "B": b, "C": c},
        "rules_version": ALGORITHM_VERSION,
    }
