"""Append durable jobs without rewriting archived experiments or prior migrations."""

import sqlalchemy as sa
from alembic import op

revision = "jobs0001"
down_revision = "hostcommv2002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_job",
        sa.Column("task_id", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text()),
        sa.Column("result_json", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("message", sa.Text()),
        sa.Column("retry_of", sa.String(32)),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        sa.CheckConstraint("status IN ('pending','running','completed','failed','interrupted')", name="ck_job_status"),
    )
    op.create_index("idx_job_owner_created", "background_job", ["operator_id", "created_at"])
    op.create_index("idx_job_status", "background_job", ["status"])


def downgrade() -> None:
    op.drop_table("background_job")
