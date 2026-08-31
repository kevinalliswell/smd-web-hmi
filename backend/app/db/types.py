"""SQLite 领域类型。"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.time import normalize_utc_iso


class UTCISOText(TypeDecorator[str]):
    """以可排序的 UTC ISO 文本保存时间，并在 ORM 边界强制归一化。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return normalize_utc_iso(value) if value is not None else None

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return normalize_utc_iso(value) if value is not None else None
