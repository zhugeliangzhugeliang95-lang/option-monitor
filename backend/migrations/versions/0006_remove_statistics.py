"""Remove retired statistical tests and minute-block storage."""
from alembic import op
revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None

def upgrade():
    for table in ('statistical_results','minute_blocks'):
        for action in ('update','delete'):
            op.execute(f'DROP TRIGGER IF EXISTS immutable_{table}_{action}')
        op.drop_table(table)

def downgrade():
    raise RuntimeError('Removed statistical data cannot be restored by downgrade. Restore a database backup instead.')
