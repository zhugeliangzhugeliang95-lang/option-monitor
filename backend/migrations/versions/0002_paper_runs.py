"""Append-only local replay reports, entirely separate from real brokerage state."""
from alembic import op
from app.db import PaperRun
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    PaperRun.__table__.create(op.get_bind(), checkfirst=True)
    for action in ('UPDATE','DELETE'):
        op.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_paper_runs_{action.lower()} BEFORE {action} ON paper_runs BEGIN SELECT RAISE(ABORT, 'append-only table'); END")

def downgrade():
    raise RuntimeError('Replay audit records are append-only.')
