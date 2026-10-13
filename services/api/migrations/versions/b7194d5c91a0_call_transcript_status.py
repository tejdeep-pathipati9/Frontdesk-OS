"""Add explicit transcript retention state without changing business records."""
from alembic import op
import sqlalchemy as sa

revision = 'b7194d5c91a0'
down_revision = 'f42a8b610c34'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('calls', sa.Column('transcript_status', sa.String(20), nullable=False, server_default='disabled'))


def downgrade():
    with op.batch_alter_table('calls') as batch:
        batch.drop_column('transcript_status')
