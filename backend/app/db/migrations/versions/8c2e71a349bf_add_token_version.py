"""add token version

Revision ID: 8c2e71a349bf
Revises: 4fc9d1a62e70
Create Date: 2026-09-01
"""

import sqlalchemy as sa
from alembic import op

revision = "8c2e71a349bf"
down_revision = "4fc9d1a62e70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("user_account", "token_version")
