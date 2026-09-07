"""Preserve unknown device runs and reviewed replay progress without invented start times."""

import sqlalchemy as sa
from alembic import op

revision = "hostcommv2002"
down_revision = "hostcommv2001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("test_session") as batch:
        batch.alter_column("start_time", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("discovered_at", sa.Text(), nullable=True))
    with op.batch_alter_table("v2_run_binding") as batch:
        batch.alter_column("recipe_digest", existing_type=sa.String(64), nullable=True)
        batch.alter_column("profile_digest", existing_type=sa.String(64), nullable=True)
    op.create_table(
        "v2_run_recovery",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("device_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("test_id", sa.String(64), unique=True),
        sa.Column("replay_status", sa.String(24), nullable=False),
        sa.Column("replay_through_id", sa.Integer(), nullable=False),
        sa.Column("replay_error", sa.String(128)),
        sa.UniqueConstraint("device_id", "run_id", name="uq_v2_recovery_run"),
    )
    op.create_table(
        "v2_recovery_review",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recovery_id", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("recovery_id", "idempotency_key", name="uq_v2_recovery_request"),
    )
    op.create_table(
        "v2_recovery_association",
        sa.Column("recovery_id", sa.String(32), primary_key=True),
        sa.Column("source_record_id", sa.Integer(), primary_key=True),
        sa.Column("event_log_id", sa.Integer()),
        sa.Column("alarm_log_id", sa.Integer()),
    )


def downgrade():
    # A downgrade cannot invent timestamps or discard reviewed source ownership.
    raise RuntimeError("Restore the verified pre-upgrade database backup to downgrade run recovery")
