"""Strict message models for the 2.0 design baseline, independent of application 1.0."""

from typing import Annotated, Literal, Union

from pydantic import Field, StringConstraints, TypeAdapter, create_model, model_validator

from .types import (
    U64,
    Code,
    Digest,
    Identifier,
    PositiveInt,
    SafeInt,
    SampleRef,
    ShortText,
    StrictModel,
    UInt,
    UtcMillis,
)


class Empty(StrictModel):
    pass


class Hello(StrictModel):
    design_revision: Literal["2.0-design.1"]
    controller_id: Identifier
    controller_epoch: Identifier
    expected_device_id: Identifier
    client_name: ShortText
    client_version: ShortText


Capability = Literal["durable_operations", "atomic_recipe", "sample_log", "alarm_log"]


class HelloAck(StrictModel):
    design_revision: Literal["2.0-design.1"]
    device_id: Identifier
    controller_epoch: Identifier
    fw_version: ShortText
    hw_version: ShortText
    capabilities: Annotated[list[Capability], Field(min_length=1, max_length=4)]
    profile_digest: Digest
    last_command_seq: U64
    control_ready: bool
    granted_role: Literal["control", "diagnostic"]

    @model_validator(mode="after")
    def baseline(self):
        if (
            set(self.capabilities) != {"durable_operations", "atomic_recipe", "sample_log", "alarm_log"}
            or len(self.capabilities) != 4
        ):
            raise ValueError("all four baseline capabilities are required exactly once")
        return self


class Heartbeat(StrictModel):
    lease_id: Identifier | None


class HeartbeatAck(StrictModel):
    lease_id: Identifier | None
    lease_expires_uptime_ms: U64 | None
    state_revision: U64


class AcquireLease(StrictModel):
    lease_ms: Literal[8000]


class ReleaseLease(StrictModel):
    reason: ShortText


class StartRun(StrictModel):
    run_id: Identifier
    recipe_digest: Digest
    safety_profile_digest: Digest


class StopRun(StrictModel):
    run_id: Identifier
    reason: Code


class ActivateRecipe(StrictModel):
    transfer_id: Identifier
    recipe_digest: Digest
    expected_active_digest: Digest | None


class AckRun(StrictModel):
    run_id: Identifier


class AckAlarm(StrictModel):
    alarm_id: Identifier
    occurrence_seq: U64


class ResetFault(StrictModel):
    fault_revision: U64
    reason: ShortText


class CommandBase(StrictModel):
    operation_id: Identifier
    controller_epoch: Identifier
    command_seq: U64
    lease_id: Identifier | None
    expected_boot_id: Identifier
    expected_state_revision: U64 | None
    request_digest: Digest

    @model_validator(mode="after")
    def guards(self):
        if self.command_seq == "0":
            raise ValueError("command_seq starts at 1")
        if self.command in {"stop_run", "acquire_lease"}:
            if self.lease_id is not None or self.expected_state_revision is not None:
                raise ValueError("stop/acquire require null lease and state precondition")
        elif self.lease_id is None or self.expected_state_revision is None:
            raise ValueError("mutating command requires lease and exact state revision")
        return self


COMMAND_PARAMS = {
    "acquire_lease": AcquireLease,
    "release_lease": ReleaseLease,
    "start_run": StartRun,
    "stop_run": StopRun,
    "activate_recipe": ActivateRecipe,
    "ack_run": AckRun,
    "ack_alarm": AckAlarm,
    "reset_fault": ResetFault,
}
COMMAND_MODELS = tuple(
    create_model(
        "Command" + "".join(part.title() for part in name.split("_")),
        __base__=CommandBase,
        command=(Literal[name], ...),
        params=(params, ...),
    )
    for name, params in COMMAND_PARAMS.items()
)
Command = Annotated[Union[COMMAND_MODELS], Field(discriminator="command")]


class OperationQuery(StrictModel):
    controller_epoch: Identifier
    operation_id: Identifier
    command_seq: U64


OperationState = Literal["accepted", "applied", "rejected", "interrupted", "unknown", "result_expired", "not_found"]


class OperationResult(OperationQuery):
    result_boot_id: Identifier | None
    request_digest: Digest | None
    status: OperationState
    reason: Code
    state_revision: U64
    run_id: Identifier | None
    lease_id: Identifier | None
    lease_expires_uptime_ms: U64 | None

    @model_validator(mode="after")
    def known_result(self):
        if self.status in {"accepted", "applied", "rejected", "interrupted"} and (
            self.request_digest is None or self.result_boot_id is None
        ):
            raise ValueError("retained operation result requires request digest")
        if self.status in {"accepted", "applied"} and self.reason != "ok":
            raise ValueError("accepted/applied reason is ok")
        if self.status not in {"accepted", "applied"} and self.reason == "ok":
            raise ValueError("non-success result requires an explicit reason")
        if (self.lease_id is None) != (self.lease_expires_uptime_ms is None):
            raise ValueError("lease identity and expiration are provided together")
        return self


class RunStatus(StrictModel):
    run_id: Identifier | None
    state: Literal[
        "booting", "idle", "preparing", "measuring", "safe_disposal", "cooling", "completed", "fault", "maintenance"
    ]
    state_revision: U64
    outcome: Literal["pending", "valid_candidate", "aborted", "invalid"]
    recipe_digest: Digest | None
    safety_profile_digest: Digest | None
    stage_index: Annotated[int, Field(ge=0, le=63)] | None
    measurement_complete: bool
    safe_complete: bool
    measurement_start: SampleRef | None
    measurement_end: SampleRef | None
    safe_boundary: SampleRef | None
    fault_revision: U64

    @model_validator(mode="after")
    def run_boundaries(self):
        if self.run_id is None and any(
            item is not None
            for item in (
                self.recipe_digest,
                self.safety_profile_digest,
                self.stage_index,
                self.measurement_start,
                self.measurement_end,
                self.safe_boundary,
            )
        ):
            raise ValueError("run-specific fields require a run_id")
        if self.state in {"preparing", "measuring", "safe_disposal", "cooling", "completed"} and self.run_id is None:
            raise ValueError("active or terminal run state requires run_id")
        if self.state in {"preparing", "measuring"} or self.outcome == "valid_candidate":
            if self.recipe_digest is None or self.safety_profile_digest is None:
                raise ValueError("known executing run requires pinned recipe and engineering configuration")
        if self.state == "measuring" and self.measurement_start is None:
            raise ValueError("measuring requires an original source boundary")
        if self.measurement_complete and (self.measurement_start is None or self.measurement_end is None):
            raise ValueError("natural measurement completion requires original boundaries")
        if self.outcome == "valid_candidate" and not self.measurement_complete:
            raise ValueError("valid_candidate requires natural measurement completion")
        if self.safe_complete != (self.safe_boundary is not None):
            raise ValueError("safe_complete and original safe boundary must agree")
        if self.state in {"preparing", "measuring"} and self.safe_complete:
            raise ValueError("preparing or measuring cannot claim safe completion")
        if self.state == "completed" and not self.safe_complete:
            raise ValueError("completed requires original safe boundary")
        refs = [ref for ref in (self.measurement_start, self.measurement_end, self.safe_boundary) if ref is not None]
        by_boot = {}
        for ref in refs:
            if int(ref.sample_seq) < by_boot.get(ref.boot_id, -1):
                raise ValueError("same-boot boundaries must be ordered")
            by_boot[ref.boot_id] = int(ref.sample_seq)
        return self


class Point(StrictModel):
    value: SafeInt | None
    quality: Literal["good", "invalid", "stale", "unavailable", "uncalibrated"]
    age_ms: UInt

    @model_validator(mode="after")
    def good_value(self):
        if self.quality == "good" and self.value is None:
            raise ValueError("good point requires a value")
        return self


class Measurements(StrictModel):
    furnace_mc: Point
    burden_mc: Point
    furnace_setpoint_mc: Point
    pressure_drop_pa: Point
    displacement_um: Point
    drip_mass_mg: Point
    n2_measured_ml_min: Point
    n2_setpoint_ml_min: Point
    co_measured_ml_min: Point
    co_setpoint_ml_min: Point


class SafetyStatus(StrictModel):
    hardwired_permit: bool
    emergency_stop: bool
    exhaust_ok: bool
    co_alarm: bool
    overtemperature: bool
    profile_approved: bool


class LogCatalog(StrictModel):
    log_id: Identifier
    oldest_record_seq: U64 | None
    newest_record_seq: U64 | None


class StatusSnapshot(StrictModel):
    log: LogCatalog
    run: RunStatus
    safety: SafetyStatus
    latest_sample: SampleRef | None
    active_recipe_digest: Digest | None
    profile_digest: Digest
    lease_id: Identifier | None
    lease_owner_controller_id: Identifier | None
    lease_owner_session_id: Identifier | None
    lease_expires_uptime_ms: U64 | None
    active_alarm_revision: U64

    @model_validator(mode="after")
    def lease_owner(self):
        fields = [
            self.lease_id,
            self.lease_owner_controller_id,
            self.lease_owner_session_id,
            self.lease_expires_uptime_ms,
        ]
        if any(value is None for value in fields) and any(value is not None for value in fields):
            raise ValueError("lease identity, owner, session and expiration must be present together")
        return self


class Telemetry(StrictModel):
    sample: SampleRef
    sample_uptime_ms: U64
    sample_timestamp: UtcMillis | None
    run_id: Identifier | None
    state_revision: U64
    values: Measurements
    first_drip_latched: bool
    first_drip_detector_quality: Literal["good", "invalid", "stale", "unavailable", "uncalibrated"]


class FlowReference(StrictModel):
    temperature_mk: PositiveInt
    pressure_pa: PositiveInt
    gas_model: Literal["dry_ideal", "vendor_calibrated"]
    source: ShortText


class Limits(StrictModel):
    temperature_max_mc: PositiveInt
    ramp_max_mc_per_min: PositiveInt
    n2_max_ml_min: PositiveInt
    co_max_ml_min: PositiveInt
    co_min_furnace_mc: UInt
    safe_end_burden_mc: Annotated[int, Field(gt=0, le=200000)]
    minimum_n2_ml_min: PositiveInt
    purge_duration_ms: PositiveInt
    cooling_timeout_ms: PositiveInt


class ChannelFreshness(StrictModel):
    furnace_mc: PositiveInt
    burden_mc: PositiveInt
    furnace_setpoint_mc: PositiveInt
    pressure_drop_pa: PositiveInt
    displacement_um: PositiveInt
    drip_mass_mg: PositiveInt
    n2_measured_ml_min: PositiveInt
    n2_setpoint_ml_min: PositiveInt
    co_measured_ml_min: PositiveInt
    co_setpoint_ml_min: PositiveInt


class Resources(StrictModel):
    max_frame_bytes: Literal[8192]
    max_recipe_bytes: Literal[65536]
    max_chunk_bytes: Literal[1536]
    max_stages: Annotated[int, Field(ge=1, le=64)]
    max_sample_rate_millihz: PositiveInt
    default_sample_period_ms: PositiveInt
    channel_freshness_ms: ChannelFreshness
    sample_log_capacity_bytes: UInt
    sample_log_durable: bool
    command_result_slots: Annotated[int, Field(ge=128, le=65536)]
    command_unresolved_slots: Annotated[int, Field(ge=1, le=65536)]

    @model_validator(mode="after")
    def capacity(self):
        if self.command_unresolved_slots > self.command_result_slots:
            raise ValueError("unresolved operation capacity exceeds total result slots")
        if self.default_sample_period_ms * self.max_sample_rate_millihz < 1000000:
            raise ValueError("default sampling rate exceeds advertised maximum")
        return self


class ProfileSnapshot(StrictModel):
    profile_id: ShortText
    engineering_config_digest: Digest
    profile_digest: Digest
    approved: bool
    rules_reference: ShortText | None
    gas_reference: FlowReference
    limits: Limits
    resources: Resources


class RecipeBegin(StrictModel):
    transfer_id: Identifier
    lease_id: Identifier
    recipe_digest: Digest
    byte_length: Annotated[int, Field(gt=0, le=65536)]


class RecipeChunk(StrictModel):
    transfer_id: Identifier
    offset: Annotated[int, Field(ge=0, le=65535)]
    data_b64: Annotated[str, StringConstraints(min_length=4, max_length=2048, pattern=r"^[A-Za-z0-9+/]+={0,2}$")]


class RecipeTransferResult(StrictModel):
    transfer_id: Identifier
    recipe_digest: Digest
    status: Literal["receiving", "validated", "rejected", "expired"]
    next_offset: Annotated[int, Field(ge=0, le=65536)]
    reason: Code


class RecipeQuery(StrictModel):
    recipe_digest: Digest
    offset: Annotated[int, Field(ge=0, le=65535)]


class RecipeSnapshot(StrictModel):
    recipe_digest: Digest
    byte_length: Annotated[int, Field(gt=0, le=65536)]
    offset: Annotated[int, Field(ge=0, le=65535)]
    data_b64: Annotated[str, StringConstraints(min_length=4, max_length=2048, pattern=r"^[A-Za-z0-9+/]+={0,2}$")]
    active: bool


class EventBase(StrictModel):
    event_id: Identifier
    event_seq: U64
    run_id: Identifier | None
    event_uptime_ms: U64
    event_timestamp: UtcMillis | None


class FirstDripEvent(EventBase):
    kind: Literal["first_drip"]
    sample: SampleRef
    burden_mc: Point
    is_valid: bool

    @model_validator(mode="after")
    def real_event(self):
        if self.run_id is None or (self.is_valid and self.burden_mc.quality != "good"):
            raise ValueError("first drip requires run identity and a good event-time burden temperature")
        return self


class RunChangedEvent(EventBase):
    kind: Literal["run_changed"]
    run: RunStatus

    @model_validator(mode="after")
    def same_run(self):
        if self.run_id != self.run.run_id:
            raise ValueError("event run identity mismatch")
        return self


AlarmCode = Literal[
    "emergency_stop",
    "co_leak",
    "exhaust_lost",
    "furnace_overtemperature",
    "burden_overtemperature",
    "temperature_sensor_invalid",
    "measurement_sensor_invalid",
    "n2_mfc_fault",
    "co_mfc_fault",
    "gas_supply_lost",
    "actuator_feedback_fault",
    "lease_lost",
    "storage_unavailable",
    "log_capacity_low",
    "log_capacity_exhausted",
    "recipe_stage_timeout",
    "clock_unsynced",
    "engineering_profile_invalid",
]


def check_alarm_level(code: str, severity: str) -> None:
    warning_codes = {"measurement_sensor_invalid", "log_capacity_low", "clock_unsynced"}
    if severity == "info" or (code not in warning_codes and severity != "trip"):
        raise ValueError("core alarm severity is below its required protection level")


class AlarmEvent(EventBase):
    kind: Literal["alarm"]
    alarm_id: Identifier
    occurrence_seq: U64
    code: AlarmCode
    transition: Literal["raised", "acknowledged", "cleared"]
    severity: Literal["info", "warning", "trip"]
    active: bool
    acknowledged: bool
    sample: SampleRef | None

    @model_validator(mode="after")
    def protection_level(self):
        check_alarm_level(self.code, self.severity)
        if self.transition == "raised" and (not self.active or self.acknowledged):
            raise ValueError("new alarm occurrence must be active and unacknowledged")
        if self.transition == "cleared" and self.active:
            raise ValueError("cleared alarm condition cannot remain active")
        if self.transition == "acknowledged" and not self.acknowledged:
            raise ValueError("acknowledgement event must retain its acknowledgement")
        return self


class OperationChangedEvent(EventBase):
    kind: Literal["operation_changed"]
    controller_epoch: Identifier
    operation_id: Identifier
    command_seq: U64


Event = Annotated[
    Union[FirstDripEvent, RunChangedEvent, AlarmEvent, OperationChangedEvent], Field(discriminator="kind")
]


class AlarmsQuery(StrictModel):
    expected_revision: U64 | None
    page_offset: UInt
    limit: Literal[16]

    @model_validator(mode="after")
    def page_cut(self):
        if self.page_offset > 0 and self.expected_revision is None:
            raise ValueError("subsequent alarm pages require a fixed revision")
        return self


class AlarmOccurrence(StrictModel):
    alarm_id: Identifier
    occurrence_seq: U64
    code: AlarmCode
    severity: Literal["info", "warning", "trip"]
    active: bool
    acknowledged: bool
    raised_boot_id: Identifier
    raised_event_seq: U64
    raised_sample: SampleRef | None
    raised_timestamp: UtcMillis | None
    raised_uptime_ms: U64

    @model_validator(mode="after")
    def protection_level(self):
        check_alarm_level(self.code, self.severity)
        return self


class AlarmsSnapshot(StrictModel):
    revision: U64
    page_offset: UInt
    items: Annotated[list[AlarmOccurrence], Field(max_length=16)]
    next_offset: UInt | None

    @model_validator(mode="after")
    def forward(self):
        if self.next_offset is not None and (not self.items or self.next_offset != self.page_offset + len(self.items)):
            raise ValueError("alarm pagination must advance by the returned item count")
        identities = [(alarm.alarm_id, alarm.occurrence_seq) for alarm in self.items]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate alarm occurrence in page")
        return self


class LogRange(StrictModel):
    log_id: Identifier
    first_record_seq: U64
    last_record_seq: U64

    @model_validator(mode="after")
    def ordered(self):
        if int(self.first_record_seq) > int(self.last_record_seq):
            raise ValueError("log range is inclusive and ordered")
        return self


class LogRequest(StrictModel):
    transfer_id: Identifier
    requested: LogRange
    max_records: Annotated[int, Field(ge=1, le=10000)]
    max_bytes: Annotated[int, Field(ge=1, le=16777216)]

    @model_validator(mode="after")
    def range_budget(self):
        if int(self.requested.last_record_seq) - int(self.requested.first_record_seq) + 1 > self.max_records:
            raise ValueError("requested inclusive range exceeds record budget")
        return self


class MissingRange(LogRange):
    reason: Literal["retention_expired", "not_recorded", "storage_fault", "log_unknown"]


class LogChunk(StrictModel):
    transfer_id: Identifier
    log_id: Identifier
    chunk_index: UInt
    snapshot_highwater: U64
    offset: UInt
    data_b64: Annotated[str, StringConstraints(min_length=4, max_length=2048, pattern=r"^[A-Za-z0-9+/]+={0,2}$")]
    chunk_digest: Digest


class LogAck(StrictModel):
    transfer_id: Identifier
    log_id: Identifier
    chunk_index: UInt
    chunk_digest: Digest
    next_offset: UInt
    committed_record_seq: U64 | None


class LogResult(StrictModel):
    transfer_id: Identifier
    requested: LogRange
    snapshot_highwater: U64
    available_first_seq: U64 | None
    available_last_seq: U64 | None
    status: Literal["complete", "partial", "unavailable"]
    byte_length: UInt
    content_digest: Digest
    record_count: UInt
    missing: Annotated[list[MissingRange], Field(max_length=32)]

    @model_validator(mode="after")
    def gaps(self):
        if (self.status == "complete") != (not self.missing):
            raise ValueError("only complete results have no missing ranges")
        if (self.available_first_seq is None) != (self.available_last_seq is None):
            raise ValueError("available endpoints are both null or both present")
        if self.available_first_seq is not None and int(self.available_first_seq) > int(self.available_last_seq):
            raise ValueError("available range is reversed")
        previous = int(self.requested.first_record_seq) - 1
        for gap in self.missing:
            if (
                gap.log_id != self.requested.log_id
                or int(gap.first_record_seq) <= previous
                or int(gap.first_record_seq) < int(self.requested.first_record_seq)
                or int(gap.last_record_seq) > int(self.requested.last_record_seq)
            ):
                raise ValueError("missing ranges must be disjoint, ordered, and inside requested log/range")
            previous = int(gap.last_record_seq)
        if self.status == "unavailable" and (self.record_count != 0 or self.byte_length != 0):
            raise ValueError("unavailable transfer cannot contain records")
        count = int(self.requested.last_record_seq) - int(self.requested.first_record_seq) + 1
        missing_count = sum(int(gap.last_record_seq) - int(gap.first_record_seq) + 1 for gap in self.missing)
        if self.record_count + missing_count != count:
            raise ValueError("records plus explicit missing ranges must account for the complete cut")
        if int(self.snapshot_highwater) < int(self.requested.last_record_seq):
            raise ValueError("requested end exceeds fixed committed highwater")
        if self.status == "partial" and not 0 < self.record_count < count:
            raise ValueError("partial requires both returned records and explicit missing records")
        return self


class SampleLogRecord(StrictModel):
    record_type: Literal["sample"]
    log_id: Identifier
    record_seq: U64
    data: Telemetry


class EventLogRecord(StrictModel):
    record_type: Literal["event"]
    log_id: Identifier
    record_seq: U64
    boot_id: Identifier
    timestamp: UtcMillis | None
    uptime_ms: U64
    data: Event

    @model_validator(mode="after")
    def event_source(self):
        if self.data.kind == "first_drip" and self.data.sample.boot_id != self.boot_id:
            raise ValueError("first drip log sample must belong to original event boot")
        return self


LOG_RECORD_ADAPTER = TypeAdapter(Annotated[Union[SampleLogRecord, EventLogRecord], Field(discriminator="record_type")])


class Error(StrictModel):
    code: Literal[
        "invalid_frame",
        "unsupported_version",
        "unsupported_capability",
        "identity_mismatch",
        "stale_session",
        "boot_mismatch",
        "lease_required",
        "state_conflict",
        "busy",
        "operation_conflict",
        "result_expired",
        "profile_unapproved",
        "profile_mismatch",
        "recipe_invalid",
        "digest_mismatch",
        "offset_mismatch",
        "storage_unavailable",
        "range_unavailable",
        "internal_error",
        "permission_denied",
    ]
    message: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    retryable: bool


RESPONSES = {
    "hello_ack",
    "heartbeat_ack",
    "status_snapshot",
    "command_result",
    "operation_snapshot",
    "profile_snapshot",
    "recipe_transfer_result",
    "recipe_snapshot",
    "log_chunk",
    "log_result",
    "error",
    "alarms_snapshot",
}


class Envelope(StrictModel):
    protocol_version: Literal["2.0"]
    msg_id: Identifier
    reply_to: Identifier | None
    session_id: Identifier | None
    boot_id: Identifier | None
    timestamp: UtcMillis | None
    uptime_ms: U64

    @model_validator(mode="after")
    def context(self):
        if self.type == "hello":
            if self.session_id is not None or self.boot_id is not None or self.uptime_ms != "0":
                raise ValueError("hello has null session/boot and uptime_ms=0")
        elif self.session_id is None or self.boot_id is None:
            raise ValueError("post-hello message requires negotiated session and boot")
        if (self.type in RESPONSES) != (self.reply_to is not None):
            raise ValueError("responses require reply_to; requests and notifications require null")
        if self.reply_to == self.msg_id:
            raise ValueError("response has a new msg_id distinct from reply_to")
        if self.type == "telemetry" and self.payload.sample.boot_id != self.boot_id:
            raise ValueError("live telemetry sample boot must equal envelope boot")
        if self.type == "event" and self.payload.kind == "first_drip":
            if self.payload.sample.boot_id != self.boot_id:
                raise ValueError("first drip sample must belong to live event boot")
        if self.type == "command" and self.payload.expected_boot_id != self.boot_id:
            raise ValueError("command boot precondition differs from session boot")
        return self


PAYLOADS = {
    "hello": Hello,
    "hello_ack": HelloAck,
    "heartbeat": Heartbeat,
    "heartbeat_ack": HeartbeatAck,
    "get_status": Empty,
    "status_snapshot": StatusSnapshot,
    "command": Command,
    "command_result": OperationResult,
    "get_operation": OperationQuery,
    "operation_snapshot": OperationResult,
    "get_profile": Empty,
    "profile_snapshot": ProfileSnapshot,
    "get_alarms": AlarmsQuery,
    "alarms_snapshot": AlarmsSnapshot,
    "recipe_begin": RecipeBegin,
    "recipe_chunk": RecipeChunk,
    "recipe_transfer_result": RecipeTransferResult,
    "get_recipe": RecipeQuery,
    "recipe_snapshot": RecipeSnapshot,
    "telemetry": Telemetry,
    "event": Event,
    "log_request": LogRequest,
    "log_chunk": LogChunk,
    "log_ack": LogAck,
    "log_result": LogResult,
    "error": Error,
}
MESSAGE_MODELS = tuple(
    create_model(
        "Message" + "".join(part.title() for part in name.split("_")),
        __base__=Envelope,
        type=(Literal[name], ...),
        payload=(payload, ...),
    )
    for name, payload in PAYLOADS.items()
)
MESSAGE_ADAPTER = TypeAdapter(Annotated[Union[MESSAGE_MODELS], Field(discriminator="type")])
