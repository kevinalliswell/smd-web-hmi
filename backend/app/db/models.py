"""SQLAlchemy 2.x ORM 模型 —— 对应开发规格说明书第 2 节全部 9 张表。

约束（安全红线 7）：sample_point / event_log / alarm_log 为只追加表，
应用层不得对其执行 DELETE / UPDATE。device_status 是唯一允许滚动删除的表。
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UTCISOText


class Base(DeclarativeBase):
    """全表声明基类。"""


# ============================================================ 2.1 user_account
class UserAccount(Base):
    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    hashed_pw: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    must_change_password: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[str | None] = mapped_column(UTCISOText(), nullable=True)
    created_at: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    last_login: Mapped[str | None] = mapped_column(UTCISOText(), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('observer','operator','admin','maintainer')",
            name="ck_user_role",
        ),
    )


# ============================================================ 2.2 test_session
class TestSession(Base):
    __tablename__ = "test_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    operator_id: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    end_time: Mapped[str | None] = mapped_column(UTCISOText(), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_at_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_ts_end_time", "end_time"),)


# ============================================================ 2.3 sample_point
class SamplePoint(Base):
    """曲线数据点。只追加表。"""

    __tablename__ = "sample_point"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="live_poll")
    # 温度
    furnace_pv: Mapped[float | None] = mapped_column(Float, nullable=True)
    furnace_sv: Mapped[float | None] = mapped_column(Float, nullable=True)
    burden_temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    burden_temp_v: Mapped[int] = mapped_column(Integer, default=1)
    temp_output_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    program_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 气体
    n2_sp: Mapped[float | None] = mapped_column(Float, nullable=True)
    n2_pv: Mapped[float | None] = mapped_column(Float, nullable=True)
    co_sp: Mapped[float | None] = mapped_column(Float, nullable=True)
    co_pv: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 过程量
    drip_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_p_v: Mapped[int] = mapped_column(Integer, default=1)
    displacement: Mapped[float | None] = mapped_column(Float, nullable=True)
    displacement_v: Mapped[int] = mapped_column(Integer, default=1)
    # 状态
    current_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    safety_relay: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 扩展
    ext_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_sp_test_ts", "test_id", "ts"),)


# ============================================================ 2.4 event_log
class EventLog(Base):
    """事件日志。只追加表。"""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    event_code: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_el_ts", "ts"),
        Index("idx_el_test", "test_id"),
    )


# ============================================================ 2.5 alarm_log
class AlarmLog(Base):
    """报警日志。只追加表。"""

    __tablename__ = "alarm_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alarm_code: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    occur_time: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    clear_time: Mapped[str | None] = mapped_column(UTCISOText(), nullable=True)
    ack_time: Mapped[str | None] = mapped_column(UTCISOText(), nullable=True)
    ack_operator: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    latched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_al_occur", "occur_time"),
        Index("idx_al_active_level", "clear_time", "level"),
        Index("idx_al_code_active", "alarm_code", "clear_time"),
        Index("idx_al_test_occur", "test_id", "occur_time"),
    )


# ====================================================== 2.6 parameter_snapshot
class ParameterSnapshot(Base):
    __tablename__ = "parameter_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    operator_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    fw_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    param_crc: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_ps_test_source_id", "test_id", "source", "id"),)


# ======================================================= 2.7 operator_action
class OperatorAction(Base):
    __tablename__ = "operator_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    operator_id: Mapped[str] = mapped_column(String, nullable=False)
    operator_role: Mapped[str] = mapped_column(String, nullable=False)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    test_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_oa_ts", "ts"),
        Index("idx_oa_test_ts", "test_id", "ts"),
    )


# ======================================================= 2.8 device_status
class DeviceStatus(Base):
    """设备状态快照滚动缓冲。唯一允许 DELETE 旧记录的表。"""

    __tablename__ = "device_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    status_json: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_ds_ts", "ts"),)


# ======================================================== 2.9 report_export
class ReportExport(Base):
    __tablename__ = "report_export"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    operator_id: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False, default="html")
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_re_test_generated", "test_id", "generated_at"),)
