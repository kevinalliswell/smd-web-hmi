"""HostComm v2 durable identities, operations and immutable source evidence."""

from sqlalchemy import Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class V2ControllerIdentity(Base):
    __tablename__ = "v2_controller_identity"
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    controller_id: Mapped[str] = mapped_column(String(32), nullable=False)
    controller_epoch: Mapped[str] = mapped_column(String(32), nullable=False)
    last_seq: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class V2Operation(Base):
    __tablename__ = "v2_operation"
    operation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), nullable=False)
    controller_epoch: Mapped[str] = mapped_column(String(32), nullable=False)
    command_seq: Mapped[str] = mapped_column(String(20), nullable=False)
    msg_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    command: Mapped[str] = mapped_column(String(32), nullable=False)
    business_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    reconciled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint("device_id", "controller_epoch", "command_seq", name="uq_v2_operation_sequence"),
        Index("ix_v2_operation_device_status", "device_id", "status"),
    )


class V2OperationReview(Base):
    __tablename__ = "v2_operation_review"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class V2SourceRecord(Base):
    __tablename__ = "v2_source_record"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), nullable=False)
    record_type: Mapped[str] = mapped_column(String(8), nullable=False)
    boot_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source_seq: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(32))
    log_id: Mapped[str | None] = mapped_column(String(32))
    record_seq: Mapped[str | None] = mapped_column(String(20))
    payload_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    record_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    archived: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint("device_id", "record_type", "boot_id", "source_seq", name="uq_v2_source_identity"),
        UniqueConstraint("device_id", "log_id", "record_seq", name="uq_v2_log_position"),
        UniqueConstraint("device_id", "event_id", name="uq_v2_source_event_id"),
        Index("ix_v2_source_run", "device_id", "run_id"),
    )


class V2LogTransfer(Base):
    __tablename__ = "v2_log_transfer"
    transfer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_msg_id: Mapped[str | None] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(String(32))
    boot_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    next_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    committed_record_seq: Mapped[str | None] = mapped_column(String(20))
    snapshot_highwater: Mapped[str | None] = mapped_column(String(20))
    pending_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, default=b"")
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class V2LogChunk(Base):
    __tablename__ = "v2_log_chunk"
    transfer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ack_json: Mapped[str] = mapped_column(Text, nullable=False)


class V2LogCursor(Base):
    __tablename__ = "v2_log_cursor"
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    log_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    verified_from_seq: Mapped[str | None] = mapped_column(String(20))
    verified_through_seq: Mapped[str | None] = mapped_column(String(20))
    scanned_through_seq: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class V2LogGap(Base):
    __tablename__ = "v2_log_gap"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), nullable=False)
    transfer_id: Mapped[str] = mapped_column(String(32), nullable=False)
    log_id: Mapped[str] = mapped_column(String(32), nullable=False)
    first_record_seq: Mapped[str] = mapped_column(String(20), nullable=False)
    last_record_seq: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class V2RunBinding(Base):
    __tablename__ = "v2_run_binding"
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    recipe_digest: Mapped[str | None] = mapped_column(String(64))
    profile_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class V2RecipeBinding(Base):
    __tablename__ = "v2_recipe_binding"
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    recipe_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(String(64), nullable=False)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recipe_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class V2AlarmProjection(Base):
    __tablename__ = "v2_alarm_projection"
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alarm_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    occurrence_seq: Mapped[str] = mapped_column(String(20), primary_key=True)
    alarm_log_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    raised_boot_id: Mapped[str | None] = mapped_column(String(32))
    raised_event_seq: Mapped[str | None] = mapped_column(String(20))
    latest_boot_id: Mapped[str | None] = mapped_column(String(32))
    latest_event_seq: Mapped[str | None] = mapped_column(String(20))
    latest_uptime_ms: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[int] = mapped_column(Integer, nullable=False)
    acknowledged: Mapped[int] = mapped_column(Integer, nullable=False)
    source_json: Mapped[str] = mapped_column(Text, nullable=False)


class V2AlarmSnapshot(Base):
    __tablename__ = "v2_alarm_snapshot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), nullable=False)
    boot_id: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_uptime_ms: Mapped[str | None] = mapped_column(String(20))
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("device_id", "boot_id", "revision", name="uq_v2_alarm_snapshot_revision"),)


class V2RunRecovery(Base):
    __tablename__ = "v2_run_recovery"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    review_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unreviewed")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    test_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    replay_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_bound")
    replay_through_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replay_error: Mapped[str | None] = mapped_column(String(128))
    __table_args__ = (UniqueConstraint("device_id", "run_id", name="uq_v2_recovery_run"),)


class V2RecoveryReview(Base):
    __tablename__ = "v2_recovery_review"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recovery_id: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("recovery_id", "idempotency_key", name="uq_v2_recovery_request"),)


class V2RecoveryAssociation(Base):
    """Associate previously archived global events/alarms without cloning or rewriting them."""

    __tablename__ = "v2_recovery_association"
    recovery_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_log_id: Mapped[int | None] = mapped_column(Integer)
    alarm_log_id: Mapped[int | None] = mapped_column(Integer)
