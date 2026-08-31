"""add login lockout fields

Revision ID: 7d2a6f4b9c01
Revises: 0fbeec2c89e7
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7d2a6f4b9c01"
down_revision: Union[str, None] = "0fbeec2c89e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_account") as batch_op:
        batch_op.add_column(
            sa.Column(
                "failed_login_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("locked_until", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_account") as batch_op:
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_attempts")
