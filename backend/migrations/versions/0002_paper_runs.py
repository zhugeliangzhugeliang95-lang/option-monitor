"""Append-only local replay reports, entirely separate from real brokerage state."""
from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    # Self-contained schema (the PaperRun ORM model was removed in 0003); identical DDL to the original model.
    if not sa.inspect(op.get_bind()).has_table('paper_runs'):
        op.create_table('paper_runs',
            sa.Column('id',sa.String(),nullable=False),
            sa.Column('created_at',sa.String(),nullable=False),
            sa.Column('payload',sa.JSON(),nullable=False),
            sa.PrimaryKeyConstraint('id'))
        op.create_index('ix_paper_runs_created_at','paper_runs',['created_at'])
    for action in ('UPDATE','DELETE'):
        op.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_paper_runs_{action.lower()} BEFORE {action} ON paper_runs BEGIN SELECT RAISE(ABORT, 'append-only table'); END")

def downgrade():
    raise RuntimeError('Replay audit records are append-only.')
