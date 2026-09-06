"""Inert device state and durable operations; this module never accesses physical I/O."""

import copy
from uuid import uuid4

from app.hostcomm.v2_contract.codec import validate_recipe_bytes
from app.hostcomm.v2_contract.messages import (
    LOG_RECORD_ADAPTER,
    AlarmOccurrence,
    Measurements,
    ProfileSnapshot,
    RunStatus,
    Telemetry,
    check_alarm_level,
)

from .storage import DeviceStore, StorageUnavailable


class DeviceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def idle_run(revision="0", fault_revision="0"):
    return {
        "run_id": None,
        "state": "idle",
        "state_revision": revision,
        "outcome": "pending",
        "recipe_digest": None,
        "safety_profile_digest": None,
        "stage_index": None,
        "measurement_complete": False,
        "safe_complete": False,
        "measurement_start": None,
        "measurement_end": None,
        "safe_boundary": None,
        "fault_revision": fault_revision,
    }


class DeviceState:
    def __init__(self, store: DeviceStore, profile: dict, device_id: str, now):
        self.store, self.now = store, now
        self.notifications = []
        self.pending_records = []
        self.data = store.load() or {
            "device_id": device_id,
            "profile": profile,
            "run": idle_run(),
            "lease": None,
            "boot_id": uuid4().hex,
            "log_id": uuid4().hex,
            "record_seq": 0,
            "sample_seq": 0,
            "event_seq": 0,
            "latest_sample": None,
            "active_recipe": None,
            "highwater": {},
            "operations": {},
            "alarms": {},
            "alarm_revision": 0,
            "first_drip_latched": False,
            "detector_quality": "good",
            "stage_started": 0,
            "stable_started": None,
            "disposal_started": None,
            "cooling_started": None,
            "values": {
                name: {
                    "value": 25000 if name in {"furnace_mc", "burden_mc", "furnace_setpoint_mc"} else 0,
                    "quality": "good",
                    "age_ms": 0,
                }
                for name in Measurements.model_fields
            },
        }
        if self.data["device_id"] != device_id:
            raise ValueError("storage belongs to a different simulator device")
        ProfileSnapshot.model_validate(self.data["profile"])
        self.data.setdefault("fault_reset_required", False)
        self.recover()

    @property
    def run(self):
        return self.data["run"]

    @property
    def profile(self):
        return self.data["profile"]

    def revision(self):
        self.run["state_revision"] = str(int(self.run["state_revision"]) + 1)

    def commit(self):
        RunStatus.model_validate(self.run)
        for record in self.pending_records:
            LOG_RECORD_ADAPTER.validate_python(record)
        self.store.save(self.data, self.pending_records)
        self.pending_records.clear()

    def recover(self):
        self.data.update(boot_id=uuid4().hex, lease=None, sample_seq=0, event_seq=0, latest_sample=None)
        self.revision()
        for result in self.data["operations"].values():
            if result["status"] == "accepted":
                result.update(status="unknown", reason="device_restarted")
        if self.run["run_id"] and not self.run["safe_complete"]:
            if not self.run["measurement_complete"]:
                self.run["outcome"] = "invalid"
            self.run["state"] = "safe_disposal"
            self.data["disposal_started"] = self.now()
            self._safe_values()
        self.commit()

    def catalog(self):
        oldest, newest = self.store.catalog()
        return {
            "log_id": self.data["log_id"],
            "oldest_record_seq": str(oldest) if oldest else None,
            "newest_record_seq": str(self.data["record_seq"]) if self.data["record_seq"] else None,
        }

    def status(self):
        lease = self.data["lease"]
        active_codes = {alarm["code"] for alarm in self.data["alarms"].values() if alarm["active"]}
        return {
            "log": self.catalog(),
            "run": copy.deepcopy(self.run),
            "safety": {
                "hardwired_permit": not bool(
                    active_codes - {"clock_unsynced", "log_capacity_low", "measurement_sensor_invalid"}
                ),
                "emergency_stop": "emergency_stop" in active_codes,
                "exhaust_ok": "exhaust_lost" not in active_codes,
                "co_alarm": "co_leak" in active_codes,
                "overtemperature": bool(active_codes & {"furnace_overtemperature", "burden_overtemperature"}),
                "profile_approved": self.profile["approved"],
            },
            "latest_sample": self.data["latest_sample"],
            "active_recipe_digest": self.data["active_recipe"]["digest"] if self.data["active_recipe"] else None,
            "profile_digest": self.profile["profile_digest"],
            "lease_id": lease["id"] if lease else None,
            "lease_owner_controller_id": lease["controller_id"] if lease else None,
            "lease_owner_session_id": lease["session_id"] if lease else None,
            "lease_expires_uptime_ms": str(lease["expires"]) if lease else None,
            "active_alarm_revision": str(self.data["alarm_revision"]),
        }

    def record(self, kind, payload):
        self.data["record_seq"] += 1
        row = {
            "record_type": "sample" if kind == "telemetry" else "event",
            "log_id": self.data["log_id"],
            "record_seq": str(self.data["record_seq"]),
            "data": copy.deepcopy(payload),
        }
        if kind == "event":
            row.update(boot_id=self.data["boot_id"], timestamp=None, uptime_ms=str(self.now()))
        self.pending_records.append(row)
        self.notifications.append((kind, copy.deepcopy(payload)))

    def event(self, kind, **fields):
        self.data["event_seq"] += 1
        payload = {
            "event_id": uuid4().hex,
            "event_seq": str(self.data["event_seq"]),
            "run_id": self.run["run_id"],
            "event_uptime_ms": str(self.now()),
            "event_timestamp": None,
            "kind": kind,
            **fields,
        }
        self.record("event", payload)
        return payload

    def run_changed(self):
        self.event("run_changed", run=copy.deepcopy(self.run))

    def sample(self):
        self.data["sample_seq"] += 1
        ref = {"boot_id": self.data["boot_id"], "sample_seq": str(self.data["sample_seq"])}
        payload = {
            "sample": ref,
            "sample_uptime_ms": str(self.now()),
            "sample_timestamp": None,
            "run_id": self.run["run_id"],
            "state_revision": self.run["state_revision"],
            "values": copy.deepcopy(self.data["values"]),
            "first_drip_latched": self.data["first_drip_latched"],
            "first_drip_detector_quality": self.data["detector_quality"],
        }
        Telemetry.model_validate(payload)
        self.data["latest_sample"] = ref
        self.record("telemetry", payload)
        return ref

    def valid_point(self, name):
        point = self.data["values"][name]
        return (
            point["value"]
            if point["quality"] == "good" and point["age_ms"] <= self.profile["resources"]["channel_freshness_ms"][name]
            else None
        )

    def _safe_values(self):
        for name in ("co_setpoint_ml_min", "co_measured_ml_min"):
            self.data["values"][name] = {"value": 0, "quality": "good", "age_ms": 0}
        for name in ("n2_setpoint_ml_min", "n2_measured_ml_min"):
            self.data["values"][name] = {
                "value": self.profile["limits"]["minimum_n2_ml_min"],
                "quality": "good",
                "age_ms": 0,
            }

    def stop(self, outcome="aborted"):
        if not self.run["run_id"] or self.run["safe_complete"]:
            return
        if self.run["state"] in {"preparing", "measuring"}:
            if self.run["state"] == "measuring":
                self.run["measurement_end"] = self.sample()
            if not self.run["measurement_complete"]:
                self.run["outcome"] = outcome
            self.run["state"] = "safe_disposal"
            self.data["disposal_started"] = self.now()
            self.revision()
            for result in self.data["operations"].values():
                if result["status"] == "accepted" and result["run_id"] == self.run["run_id"]:
                    result.update(status="interrupted", reason="stop_latched")
            self.run_changed()
        self._safe_values()

    def lose_lease(self, session_id=None):
        lease = self.data["lease"]
        if not lease or (session_id is not None and lease["session_id"] != session_id):
            return
        self.data["lease"] = None
        self.revision()
        if self.run["run_id"] and not self.run["safe_complete"]:
            self.stop()
            self.raise_alarm("lease_lost", commit=False)
        self.commit()

    def lease_valid(self, session_id, lease_id):
        lease = self.data["lease"]
        return bool(
            lease and lease["id"] == lease_id and lease["session_id"] == session_id and self.now() < lease["expires"]
        )

    def heartbeat(self, session_id, lease_id):
        if self.lease_valid(session_id, lease_id):
            self.data["lease"]["expires"] = self.now() + 8000
        lease = self.data["lease"]
        return {
            "lease_id": lease["id"] if lease else None,
            "lease_expires_uptime_ms": str(lease["expires"]) if lease else None,
            "state_revision": self.run["state_revision"],
        }

    def lookup(self, query):
        epoch, seq = query["controller_epoch"], query["command_seq"]
        result = self.data["operations"].get(f"{epoch}:{seq}")
        if result and result["operation_id"] != query["operation_id"]:
            raise DeviceError("operation_conflict")
        if result:
            return copy.deepcopy(result)
        for retained in self.data["operations"].values():
            if retained["operation_id"] == query["operation_id"]:
                raise DeviceError("operation_conflict")
        expired = int(seq) <= self.data["highwater"].get(epoch, 0)
        return {
            **query,
            "result_boot_id": None,
            "request_digest": None,
            "status": "result_expired" if expired else "not_found",
            "reason": "result_expired" if expired else "not_found",
            "state_revision": self.run["state_revision"],
            "run_id": None,
            "lease_id": None,
            "lease_expires_uptime_ms": None,
        }

    def validate_recipe(self, raw):
        recipe = validate_recipe_bytes(raw)
        if recipe.safety_profile_digest != self.profile["profile_digest"]:
            raise DeviceError("profile_mismatch")
        limits = self.profile["limits"]
        if len(recipe.stages) > self.profile["resources"]["max_stages"]:
            raise DeviceError("recipe_invalid")
        for stage in recipe.stages:
            if (
                (stage.target_mc or 0) > limits["temperature_max_mc"]
                or (stage.rate_mc_per_min or 0) > limits["ramp_max_mc_per_min"]
                or stage.n2_ml_min > limits["n2_max_ml_min"]
                or stage.co_ml_min > limits["co_max_ml_min"]
            ):
                raise DeviceError("recipe_invalid")
        final = recipe.stages[-1]
        if final.n2_ml_min < limits["minimum_n2_ml_min"] or final.exit.value > limits["safe_end_burden_mc"]:
            raise DeviceError("recipe_invalid")
        return recipe

    def command(self, session, payload, activate_raw=None):
        if session.role != "control" or payload["controller_epoch"] != session.epoch:
            raise DeviceError("permission_denied")
        query = {key: payload[key] for key in ("controller_epoch", "operation_id", "command_seq")}
        prior = self.lookup(query)
        if prior["status"] != "not_found":
            if prior["request_digest"] is not None and prior["request_digest"] != payload["request_digest"]:
                raise DeviceError("operation_conflict")
            return prior
        before = copy.deepcopy(self.data)
        try:
            self._command_permission(session, payload, activate_raw)
            rejected = None
        except DeviceError as exc:
            rejected = exc.code
        result = {
            **query,
            "result_boot_id": self.data["boot_id"],
            "request_digest": payload["request_digest"],
            "status": "rejected" if rejected else "accepted",
            "reason": rejected or "ok",
            "state_revision": self.run["state_revision"],
            "run_id": None,
            "lease_id": None,
            "lease_expires_uptime_ms": None,
        }
        if not rejected and payload["command"] == "start_run":
            self.run.update(
                run_id=payload["params"]["run_id"],
                state="preparing",
                outcome="pending",
                recipe_digest=payload["params"]["recipe_digest"],
                safety_profile_digest=self.profile["profile_digest"],
                stage_index=0,
            )
            self.data["first_drip_latched"] = False
            self.data["stable_started"] = None
            self.data.pop("ramp_initial_mc", None)
            self.revision()
            result.update(run_id=self.run["run_id"], state_revision=self.run["state_revision"])
        key = f"{session.epoch}:{payload['command_seq']}"
        self.data["operations"][key] = result
        self.data["highwater"][session.epoch] = int(payload["command_seq"])
        try:
            self._evict_history()
        except DeviceError:
            self.data = before
            raise
        try:
            self.commit()  # Intent, sequence waterline and start reservation share this transaction.
        except StorageUnavailable:
            self.data, self.pending_records = before, []
            if payload["command"] == "stop_run" and not rejected:
                self.stop()
                self.run["outcome"] = "invalid" if not self.run["measurement_complete"] else self.run["outcome"]
                result.update(status="unknown", reason="storage_unavailable")
                return result
            raise DeviceError("storage_unavailable")
        if rejected:
            return copy.deepcopy(result)
        if payload["command"] == "start_run":
            self.run_changed()
        else:
            self._apply(session, payload, activate_raw)
            result.update(status="applied", state_revision=self.run["state_revision"])
            if payload["command"] == "acquire_lease":
                lease = self.data["lease"]
                result.update(lease_id=lease["id"], lease_expires_uptime_ms=str(lease["expires"]))
            result["run_id"] = payload["params"].get("run_id")
        try:
            self.commit()
        except StorageUnavailable:
            result.update(status="unknown", reason="storage_unavailable")
        return copy.deepcopy(result)

    def _command_permission(self, session, payload, raw):
        name, params = payload["command"], payload["params"]
        if payload["expected_boot_id"] != self.data["boot_id"]:
            raise DeviceError("boot_mismatch")
        if name not in {"acquire_lease", "stop_run"}:
            if not self.lease_valid(session.id, payload["lease_id"]):
                raise DeviceError("lease_required")
            if payload["expected_state_revision"] != self.run["state_revision"]:
                raise DeviceError("state_conflict")
        if name == "acquire_lease" and self.data["lease"]:
            raise DeviceError("busy")
        if name in {"start_run", "activate_recipe"}:
            if not self.profile["approved"]:
                raise DeviceError("profile_unapproved")
            if self.run["state"] != "idle" or self.run["run_id"]:
                raise DeviceError("state_conflict")
            if any(a["active"] and a["severity"] == "trip" for a in self.data["alarms"].values()):
                raise DeviceError("state_conflict")
        if name == "start_run":
            active = self.data["active_recipe"]
            if not active or active["digest"] != params["recipe_digest"]:
                raise DeviceError("recipe_invalid")
            if params["safety_profile_digest"] != self.profile["profile_digest"]:
                raise DeviceError("profile_mismatch")
            if self.valid_point("furnace_mc") is None or self.valid_point("burden_mc") is None:
                raise DeviceError("state_conflict")
        if name == "activate_recipe":
            expected = self.data["active_recipe"]["digest"] if self.data["active_recipe"] else None
            if expected != params["expected_active_digest"]:
                raise DeviceError("state_conflict")
            if raw is None:
                raise DeviceError("recipe_invalid")
            self.validate_recipe(raw)
        if name in {"stop_run", "ack_run"} and params["run_id"] != self.run["run_id"]:
            raise DeviceError("state_conflict")
        if name == "ack_run" and (
            not self.run["safe_complete"]
            or self.data["fault_reset_required"]
            or any(a["active"] or not a["acknowledged"] for a in self.data["alarms"].values())
        ):
            raise DeviceError("state_conflict")
        if name == "ack_alarm" and f"{params['alarm_id']}:{params['occurrence_seq']}" not in self.data["alarms"]:
            raise DeviceError("state_conflict")
        if name == "reset_fault" and (
            params["fault_revision"] != self.run["fault_revision"]
            or any(a["active"] and a["severity"] == "trip" for a in self.data["alarms"].values())
        ):
            raise DeviceError("state_conflict")

    def _apply(self, session, payload, raw):
        name, params = payload["command"], payload["params"]
        if name == "acquire_lease":
            self.data["lease"] = {
                "id": uuid4().hex,
                "session_id": session.id,
                "controller_id": session.controller_id,
                "expires": self.now() + 8000,
            }
            self.revision()
        elif name == "release_lease":
            self.data["lease"] = None
            self.revision()
            self.stop()
        elif name == "activate_recipe":
            self.data["active_recipe"] = {"digest": params["recipe_digest"], "raw": raw.decode("utf-8")}
            self.revision()
        elif name == "stop_run":
            self.stop()
        elif name == "ack_run":
            revision, fault = self.run["state_revision"], self.run["fault_revision"]
            self.data["run"] = idle_run(revision, fault)
            self.revision()
            self.run_changed()
        elif name == "ack_alarm":
            self.alarm_transition(params["alarm_id"], params["occurrence_seq"], "acknowledged", commit=False)
        elif name == "reset_fault":
            self.data["fault_reset_required"] = False
            self.run["fault_revision"] = str(int(self.run["fault_revision"]) + 1)
            if self.run["state"] == "fault":
                self.run["state"] = "safe_disposal" if self.run["run_id"] else "idle"
            self.revision()
            self.run_changed()

    def _evict_history(self):
        capacity = self.profile["resources"]["command_result_slots"]
        for key, result in list(self.data["operations"].items()):
            if len(self.data["operations"]) <= capacity:
                return
            if result["status"] != "accepted" and (not result["run_id"] or result["run_id"] != self.run["run_id"]):
                del self.data["operations"][key]
        if len(self.data["operations"]) > capacity:
            raise DeviceError("busy")

    def raise_alarm(self, code, *, severity=None, commit=True):
        severity = severity or (
            "warning" if code in {"measurement_sensor_invalid", "log_capacity_low", "clock_unsynced"} else "trip"
        )
        check_alarm_level(code, severity)
        alarm_id = uuid4().hex
        alarm = {
            "alarm_id": alarm_id,
            "occurrence_seq": "1",
            "code": code,
            "severity": severity,
            "active": True,
            "acknowledged": False,
            "raised_boot_id": self.data["boot_id"],
            "raised_event_seq": str(self.data["event_seq"] + 1),
            "raised_sample": self.data["latest_sample"],
            "raised_timestamp": None,
            "raised_uptime_ms": str(self.now()),
        }
        AlarmOccurrence.model_validate(alarm)
        self.data["alarms"][f"{alarm_id}:1"] = alarm
        self.data["alarm_revision"] += 1
        self.revision()
        self.event(
            "alarm",
            alarm_id=alarm_id,
            occurrence_seq="1",
            code=code,
            transition="raised",
            severity=severity,
            active=True,
            acknowledged=False,
            sample=self.data["latest_sample"],
        )
        if severity == "trip":
            self.data["fault_reset_required"] = True
            self.run["fault_revision"] = str(int(self.run["fault_revision"]) + 1)
            self.stop("invalid" if code != "lease_lost" else "aborted")
        if commit:
            self.commit()
        return alarm_id

    def alarm_transition(self, alarm_id, occurrence_seq, transition, *, commit=True):
        alarm = self.data["alarms"].get(f"{alarm_id}:{occurrence_seq}")
        if not alarm:
            raise DeviceError("state_conflict")
        alarm["active" if transition == "cleared" else "acknowledged"] = transition != "cleared"
        self.data["alarm_revision"] += 1
        self.revision()
        self.event(
            "alarm",
            **{k: alarm[k] for k in ("alarm_id", "occurrence_seq", "code", "severity", "active", "acknowledged")},
            transition=transition,
            sample=self.data["latest_sample"],
        )
        if commit:
            self.commit()

    def finish_measurement(self):
        if self.run["state"] != "measuring":
            raise DeviceError("state_conflict")
        self.run.update(
            measurement_end=self.sample(), measurement_complete=True, outcome="valid_candidate", state="safe_disposal"
        )
        self.data["disposal_started"] = self.now()
        self._safe_values()
        self.revision()
        self.run_changed()
        self.commit()

    def tick(self):
        lease = self.data["lease"]
        if lease and self.now() >= lease["expires"]:
            self.lose_lease()
        if self.run["state"] == "preparing" and (
            self.valid_point("furnace_mc") is None
            or self.valid_point("burden_mc") is None
            or self.valid_point("furnace_setpoint_mc") is None
        ):
            self.raise_alarm("temperature_sensor_invalid", commit=False)
        if self.run["state"] == "preparing":
            self.run["measurement_start"] = self.sample()
            self.run["state"] = "measuring"
            self.data["stage_started"] = self.now()
            self.revision()
            for result in self.data["operations"].values():
                if result["status"] == "accepted" and result["run_id"] == self.run["run_id"]:
                    result.update(status="applied", state_revision=self.run["state_revision"])
                    self.event(
                        "operation_changed",
                        **{k: result[k] for k in ("controller_epoch", "operation_id", "command_seq")},
                    )
            self.run_changed()
        self.sample()
        self._process_stage()
        if (
            self.run["state"] == "safe_disposal"
            and self.now() - self.data["disposal_started"] >= self.profile["limits"]["purge_duration_ms"]
        ):
            self.run["state"] = "cooling"
            self.data["cooling_started"] = self.now()
            self.data["stable_started"] = None
            self.revision()
            self.run_changed()
        if self.run["state"] == "cooling":
            self._cooling()
        self.commit()

    def _process_stage(self):
        if self.run["state"] != "measuring":
            return
        recipe = validate_recipe_bytes(self.data["active_recipe"]["raw"].encode())
        stage = recipe.stages[self.run["stage_index"]]
        if stage.kind == "cool":
            self.finish_measurement()
            return
        elapsed = self.now() - self.data["stage_started"]
        if elapsed >= stage.timeout_ms:
            self.raise_alarm("recipe_stage_timeout", commit=False)
            return
        if stage.co_ml_min and (
            self.valid_point("furnace_mc") is None
            or self.valid_point("furnace_mc") < self.profile["limits"]["co_min_furnace_mc"]
        ):
            self.raise_alarm("temperature_sensor_invalid", commit=False)
            return
        if stage.heater_mode == "hold":
            self.data["values"]["furnace_setpoint_mc"]["value"] = stage.target_mc
        elif stage.heater_mode == "ramp":
            target = self.valid_point("furnace_setpoint_mc")
            if target is None:
                self.raise_alarm("temperature_sensor_invalid", commit=False)
                return
            # Setpoint advances from a captured stage start, never leaps to the requested final target.
            start = self.data.setdefault("ramp_initial_mc", target)
            delta = stage.rate_mc_per_min * elapsed // 60000
            self.data["values"]["furnace_setpoint_mc"]["value"] = (
                min(stage.target_mc, start + delta) if stage.target_mc >= start else max(stage.target_mc, start - delta)
            )
        for gas, value in (("n2", stage.n2_ml_min), ("co", stage.co_ml_min)):
            self.data["values"][f"{gas}_setpoint_ml_min"]["value"] = value
            self.data["values"][f"{gas}_measured_ml_min"]["value"] = value
        observed = elapsed if stage.exit.source == "elapsed_ms" else self.valid_point(stage.exit.source)
        matched = observed is not None and (
            observed >= stage.exit.value if stage.exit.op == "gte" else observed < stage.exit.value
        )
        if not matched:
            self.data["stable_started"] = None
            return
        if self.data["stable_started"] is None:
            self.data["stable_started"] = self.now()
        if self.now() - self.data["stable_started"] < stage.exit.stable_ms:
            return
        self.run["stage_index"] += 1
        self.data.update(stage_started=self.now(), stable_started=None)
        self.data.pop("ramp_initial_mc", None)
        if recipe.stages[self.run["stage_index"]].kind == "cool":
            self.finish_measurement()
        else:
            self.revision()
            self.run_changed()

    def _cooling(self):
        if self.now() - self.data["cooling_started"] >= self.profile["limits"]["cooling_timeout_ms"]:
            self.raise_alarm("recipe_stage_timeout", commit=False)
            self.run["state"] = "fault"
            return
        recipe = validate_recipe_bytes(self.data["active_recipe"]["raw"].encode())
        threshold = min(self.profile["limits"]["safe_end_burden_mc"], recipe.stages[-1].exit.value)
        burden = self.valid_point("burden_mc")
        n2 = self.valid_point("n2_measured_ml_min")
        co = self.valid_point("co_measured_ml_min")
        if (
            burden is None
            or burden >= threshold
            or n2 is None
            or n2 < self.profile["limits"]["minimum_n2_ml_min"]
            or co != 0
            or any(a["active"] and a["severity"] == "trip" for a in self.data["alarms"].values())
        ):
            self.data["stable_started"] = None
            return
        if self.data["stable_started"] is None:
            self.data["stable_started"] = self.now()
        if self.now() - self.data["stable_started"] >= recipe.stages[-1].exit.stable_ms:
            self.run.update(safe_boundary=self.data["latest_sample"], safe_complete=True, state="completed")
            self.revision()
            self.run_changed()

    def first_drip(self, is_valid=True):
        if self.run["state"] != "measuring":
            raise DeviceError("state_conflict")
        sample = self.sample()
        self.data["first_drip_latched"] = True
        self.event(
            "first_drip", sample=sample, burden_mc=copy.deepcopy(self.data["values"]["burden_mc"]), is_valid=is_valid
        )
        self.commit()
