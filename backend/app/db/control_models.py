"""每台后台一个持久控制权记录；审计存于 operator_action。"""

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.types import UTCISOText


class ControlOwnership(Base):
    __tablename__ = "control_ownership"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    changed_at: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    __table_args__ = (CheckConstraint("id = 1", name="ck_single_controller"),)
