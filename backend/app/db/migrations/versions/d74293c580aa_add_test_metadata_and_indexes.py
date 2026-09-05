"""add test metadata and operational indexes

Revision ID: d74293c580aa
Revises: 8c2e71a349bf
Create Date: 2026-09-01
"""

import sqlalchemy as sa
from alembic import op

revision = "d74293c580aa"
down_revision = "8c2e71a349bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_session", sa.Column("original_height_mm", sa.Float(), nullable=True))
    op.create_index("idx_ts_end_time", "test_session", ["end_time"])
    op.create_index("idx_al_active_level", "alarm_log", ["clear_time", "level"])
    op.create_index("idx_al_code_active", "alarm_log", ["alarm_code", "clear_time"])
    op.create_index("idx_al_test_occur", "alarm_log", ["test_id", "occur_time"])
    op.create_index("idx_ps_test_source_id", "parameter_snapshot", ["test_id", "source", "id"])
    op.create_index("idx_oa_ts", "operator_action", ["ts"])
    op.create_index("idx_oa_test_ts", "operator_action", ["test_id", "ts"])
    op.create_index("idx_re_test_generated", "report_export", ["test_id", "generated_at"])


def downgrade() -> None:
    op.drop_index("idx_re_test_generated", table_name="report_export")
    op.drop_index("idx_oa_test_ts", table_name="operator_action")
    op.drop_index("idx_oa_ts", table_name="operator_action")
    op.drop_index("idx_ps_test_source_id", table_name="parameter_snapshot")
    op.drop_index("idx_al_test_occur", table_name="alarm_log")
    op.drop_index("idx_al_code_active", table_name="alarm_log")
    op.drop_index("idx_al_active_level", table_name="alarm_log")
    op.drop_index("idx_ts_end_time", table_name="test_session")
    op.drop_column("test_session", "original_height_mm")
