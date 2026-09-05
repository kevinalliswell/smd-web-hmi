"""Archive explicit lifecycle and recipe provenance without rewriting raw data."""
from alembic import op
import sqlalchemy as sa

revision = 'experiment001'
down_revision = 'd74293c580aa'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('test_session', sa.Column('phase', sa.String(32), nullable=False, server_default='needs_review'))
    op.add_column('test_session', sa.Column('mode', sa.String(16), nullable=False, server_default='custom'))
    op.add_column('test_session', sa.Column('data_integrity', sa.String(16), nullable=False, server_default='unknown'))
    for name in ('stop_requested_at', 'measurement_completed_at', 'safety_completed_at', 'recipe_snapshot_json', 'measurement_basis_json'):
        op.add_column('test_session', sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('test_session') as batch:
        for name in ('phase', 'mode', 'data_integrity', 'stop_requested_at', 'measurement_completed_at',
                     'safety_completed_at', 'recipe_snapshot_json', 'measurement_basis_json'):
            batch.drop_column(name)
