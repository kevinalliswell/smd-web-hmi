"""上位机持久命令事务；与设备msg_id关联，不替代板端执行日志。"""

from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.types import UTCISOText


class Operation(Base):
    __tablename__ = "operation"

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    msg_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    updated_at: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    device_result_json: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    client_ip: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','sent','accepted','verified','rejected','unknown')", name="ck_operation_status"
        ),
        Index("idx_operation_operator_created", "operator_id", "created_at"),
        Index("idx_operation_status", "status"),
    )
