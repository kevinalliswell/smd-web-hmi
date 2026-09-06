"""Persist HostComm v2 pairing, operations and source-log evidence."""

from alembic import op
import sqlalchemy as sa

revision = "hostcommv2001"
down_revision = "recipe001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('v2_controller_identity',
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('controller_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('controller_epoch', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('last_seq', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('created_at', sa.Text(), nullable=False, primary_key=False),
    )
    op.create_table('v2_log_chunk',
        sa.Column('transfer_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('chunk_index', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('metadata_json', sa.Text(), nullable=False, primary_key=False),
        sa.Column('raw_bytes', sa.LargeBinary(), nullable=False, primary_key=False),
        sa.Column('ack_json', sa.Text(), nullable=False, primary_key=False),
    )
    op.create_table('v2_log_cursor',
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('log_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('verified_from_seq', sa.String(length=20), nullable=True, primary_key=False),
        sa.Column('verified_through_seq', sa.String(length=20), nullable=True, primary_key=False),
        sa.Column('scanned_through_seq', sa.String(length=20), nullable=True, primary_key=False),
        sa.Column('updated_at', sa.Text(), nullable=False, primary_key=False),
    )
    op.create_table('v2_log_gap',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('transfer_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('log_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('first_record_seq', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('last_record_seq', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('reason', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('created_at', sa.Text(), nullable=False, primary_key=False),
    )
    op.create_table('v2_log_transfer',
        sa.Column('transfer_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('request_json', sa.Text(), nullable=False, primary_key=False),
        sa.Column('request_msg_id', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('session_id', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('boot_id', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('status', sa.String(length=24), nullable=False, primary_key=False),
        sa.Column('next_offset', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('next_index', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('committed_record_seq', sa.String(length=20), nullable=True, primary_key=False),
        sa.Column('snapshot_highwater', sa.String(length=20), nullable=True, primary_key=False),
        sa.Column('pending_bytes', sa.LargeBinary(), nullable=False, primary_key=False),
        sa.Column('record_count', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('result_json', sa.Text(), nullable=True, primary_key=False),
        sa.Column('created_at', sa.Text(), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.Text(), nullable=False, primary_key=False),
    )
    op.create_table('v2_operation',
        sa.Column('operation_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('controller_epoch', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('command_seq', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('msg_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('command', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('business_digest', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('request_digest', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('actor', sa.String(length=128), nullable=False, primary_key=False),
        sa.Column('role', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=24), nullable=False, primary_key=False),
        sa.Column('reason', sa.String(length=128), nullable=False, primary_key=False),
        sa.Column('request_json', sa.Text(), nullable=False, primary_key=False),
        sa.Column('result_json', sa.Text(), nullable=True, primary_key=False),
        sa.Column('reconciled', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.Text(), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.Text(), nullable=False, primary_key=False),
        sa.UniqueConstraint('msg_id', name=None),
        sa.UniqueConstraint('device_id', 'controller_epoch', 'command_seq', name='uq_v2_operation_sequence'),
    )
    op.create_index('ix_v2_operation_device_status', 'v2_operation', ['device_id', 'status'], unique=False)
    op.create_table('v2_operation_review',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('operation_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('actor', sa.String(length=128), nullable=False, primary_key=False),
        sa.Column('reason', sa.Text(), nullable=False, primary_key=False),
        sa.Column('evidence_json', sa.Text(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.Text(), nullable=False, primary_key=False),
    )
    op.create_table('v2_recipe_binding',
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('recipe_digest', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('recipe_id', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('recipe_version', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('source_digest', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('recipe_json', sa.Text(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.Text(), nullable=False, primary_key=False),
    )
    op.create_table('v2_run_binding',
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('run_id', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('test_id', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('recipe_digest', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('profile_digest', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('created_at', sa.Text(), nullable=False, primary_key=False),
        sa.UniqueConstraint('test_id', name=None),
    )
    op.create_table('v2_source_record',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('device_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('record_type', sa.String(length=8), nullable=False, primary_key=False),
        sa.Column('boot_id', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('source_seq', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('event_id', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('run_id', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('log_id', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('record_seq', sa.String(length=20), nullable=True, primary_key=False),
        sa.Column('payload_bytes', sa.LargeBinary(), nullable=False, primary_key=False),
        sa.Column('record_bytes', sa.LargeBinary(), nullable=True, primary_key=False),
        sa.Column('archived', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('received_at', sa.Text(), nullable=False, primary_key=False),
        sa.UniqueConstraint('device_id', 'event_id', name='uq_v2_source_event_id'),
        sa.UniqueConstraint('device_id', 'log_id', 'record_seq', name='uq_v2_log_position'),
        sa.UniqueConstraint('device_id', 'record_type', 'boot_id', 'source_seq', name='uq_v2_source_identity'),
    )
    op.create_index('ix_v2_source_run', 'v2_source_record', ['device_id', 'run_id'], unique=False)
    op.add_column('sample_point', sa.Column('source_boot_id', sa.String(32), nullable=True))
    op.add_column('sample_point', sa.Column('source_sequence', sa.String(20), nullable=True))
    op.add_column('sample_point', sa.Column('source_run_id', sa.String(32), nullable=True))
    op.add_column('sample_point', sa.Column('source_uptime_ms', sa.String(20), nullable=True))
    op.create_index('idx_sp_test_source_identity', 'sample_point',
                    ['test_id', 'source_boot_id', 'source_sequence'], unique=False)
    op.create_table('v2_alarm_projection',
        sa.Column('device_id', sa.String(32), primary_key=True),
        sa.Column('alarm_id', sa.String(32), primary_key=True),
        sa.Column('occurrence_seq', sa.String(20), primary_key=True),
        sa.Column('alarm_log_id', sa.Integer(), nullable=False, unique=True),
        sa.Column('raised_boot_id', sa.String(32), nullable=True),
        sa.Column('raised_event_seq', sa.String(20), nullable=True),
        sa.Column('latest_boot_id', sa.String(32), nullable=True),
        sa.Column('latest_event_seq', sa.String(20), nullable=True),
        sa.Column('latest_uptime_ms', sa.String(20), nullable=True),
        sa.Column('active', sa.Integer(), nullable=False),
        sa.Column('acknowledged', sa.Integer(), nullable=False),
        sa.Column('source_json', sa.Text(), nullable=False),
    )
    op.create_table('v2_alarm_snapshot',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.String(32), nullable=False),
        sa.Column('boot_id', sa.String(32), nullable=False),
        sa.Column('revision', sa.String(20), nullable=False),
        sa.Column('snapshot_uptime_ms', sa.String(20), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.UniqueConstraint('device_id', 'boot_id', 'revision', name='uq_v2_alarm_snapshot_revision'),
    )


def downgrade() -> None:
    raise RuntimeError("Protocol audit history is retained; restore a verified database backup to roll back")
