"""Archive immutable process recipes and their exact definition digests."""

from alembic import op
import sqlalchemy as sa

revision = "recipe001"
down_revision = "control001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recipe_version",
        sa.Column("recipe_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(32), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_recipe_version_positive"),
    )


def downgrade() -> None:
    raise RuntimeError("配方历史不得删除；请使用受控备份恢复")
