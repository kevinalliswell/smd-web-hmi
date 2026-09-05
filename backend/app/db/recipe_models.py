"""不可变的配方版本；更新只能追加版本，不覆盖旧定义。"""

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.types import UTCISOText


class RecipeVersion(Base):
    __tablename__ = "recipe_version"

    recipe_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(UTCISOText(), nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (CheckConstraint("version > 0", name="ck_recipe_version_positive"),)
