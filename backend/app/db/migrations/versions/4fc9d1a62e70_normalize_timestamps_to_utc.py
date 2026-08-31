"""normalize timestamp values to UTC

Revision ID: 4fc9d1a62e70
Revises: 2bb3d7e98f41
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4fc9d1a62e70"
down_revision: Union[str, None] = "2bb3d7e98f41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TIMESTAMP_COLUMNS = {
    "user_account": ("created_at", "last_login", "locked_until"),
    "test_session": ("start_time", "end_time"),
    "sample_point": ("ts",),
    "event_log": ("ts",),
    "alarm_log": ("occur_time", "clear_time", "ack_time"),
    "parameter_snapshot": ("ts",),
    "operator_action": ("ts",),
    "device_status": ("ts",),
    "report_export": ("generated_at",),
}


def upgrade() -> None:
    connection = op.get_bind()
    for table, columns in _TIMESTAMP_COLUMNS.items():
        for column in columns:
            invalid_count = connection.scalar(
                sa.text(
                    f"SELECT count(*) FROM {table} "
                    f"WHERE {column} IS NOT NULL "
                    f"AND strftime('%Y-%m-%dT%H:%M:%S+00:00', {column}) IS NULL"
                )
            )
            if invalid_count:
                raise RuntimeError(f"{table}.{column} 包含 {invalid_count} 个非法时间戳，无法安全迁移")
            op.execute(
                sa.text(
                    f"UPDATE {table} "
                    f"SET {column} = strftime('%Y-%m-%dT%H:%M:%S+00:00', {column}) "
                    f"WHERE {column} IS NOT NULL"
                )
            )


def downgrade() -> None:
    # UTC 归一化是不可逆的数据修正；降级代码版本无需恢复原时区表示。
    pass
