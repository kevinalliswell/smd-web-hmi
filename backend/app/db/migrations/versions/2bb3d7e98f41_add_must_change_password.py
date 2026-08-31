"""add must change password flag

Revision ID: 2bb3d7e98f41
Revises: 7d2a6f4b9c01
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2bb3d7e98f41"
down_revision: Union[str, None] = "7d2a6f4b9c01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_account") as batch_op:
        batch_op.add_column(
            sa.Column(
                "must_change_password",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    # 旧版本可能已经创建了默认 admin；升级时强制其完成一次密码轮换。
    op.execute(
        sa.text(
            "UPDATE user_account SET must_change_password = 1 "
            "WHERE username = 'admin'"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("user_account") as batch_op:
        batch_op.drop_column("must_change_password")
