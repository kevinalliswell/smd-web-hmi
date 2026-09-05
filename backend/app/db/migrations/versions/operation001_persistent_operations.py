"""Persist command identities and uncertain outcomes before device writes."""

import sqlalchemy as sa
from alembic import op

revision = "operation001"
down_revision = "d74293c580aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation",
        sa.Column("operation_id", sa.String(128), primary_key=True),
        sa.Column("msg_id", sa.String(64), nullable=False, unique=True),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column("operator_role", sa.String(32), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("device_result_json", sa.Text()),
        sa.Column("result_json", sa.Text()),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("client_ip", sa.String(64)),
        sa.CheckConstraint(
            "status IN ('pending','sent','accepted','verified','rejected','unknown')", name="ck_operation_status"
        ),
    )
    op.create_index("idx_operation_operator_created", "operation", ["operator_id", "created_at"])
    op.create_index("idx_operation_status", "operation", ["status"])


def downgrade() -> None:
    raise RuntimeError("操作追溯记录不得删除；请使用受控备份恢复流程")
