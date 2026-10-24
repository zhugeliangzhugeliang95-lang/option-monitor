"""Remove the local paper replay feature and its paper_runs table."""
from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    for action in ('update','delete'):
        op.execute(f'DROP TRIGGER IF EXISTS immutable_paper_runs_{action}')
    op.drop_table('paper_runs')

def downgrade():
    # Recreate exactly what 0002 created (table, created_at index, append-only triggers).
    op.create_table('paper_runs',
        sa.Column('id',sa.String(),nullable=False),
        sa.Column('created_at',sa.String(),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_paper_runs_created_at','paper_runs',['created_at'])
    for action in ('UPDATE','DELETE'):
        op.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_paper_runs_{action.lower()} BEFORE {action} ON paper_runs BEGIN SELECT RAISE(ABORT, 'append-only table'); END")
