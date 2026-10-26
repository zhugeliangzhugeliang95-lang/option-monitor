"""Remove storage dedicated to the retired history/trace panel."""
from alembic import op
revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None

def upgrade():
    for table in ('observation_outcomes','frozen_observations'):
        for action in ('update','delete'):
            op.execute(f'DROP TRIGGER IF EXISTS immutable_{table}_{action}')
        op.drop_table(table)

def downgrade():
    raise RuntimeError('Removed research observations cannot be restored by downgrade. Restore a database backup instead.')
