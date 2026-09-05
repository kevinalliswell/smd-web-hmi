"""持久控制权；原始实验与历史审计不变。

Revision ID: control001
Revises: experiment001
"""

import sqlalchemy as sa
from alembic import op

revision = "control001"
down_revision = "experiment001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "control_ownership",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(32), nullable=True),
        sa.Column("changed_at", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_single_controller"),
    )


def downgrade():
    op.drop_table("control_ownership")
