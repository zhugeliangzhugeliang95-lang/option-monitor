"""Composite index for per-monitor/config newest-first scans of aggregate_results.

Ordering note: on main this follows 0002. The remove-paper branch adds 0003 (drop paper_runs);
whichever merges second must set down_revision to the other's revision so there is one head.
"""
from alembic import op
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    # IF NOT EXISTS: fresh databases get the index from 0001's metadata.create_all.
    op.execute('CREATE INDEX IF NOT EXISTS ix_aggregate_results_monitor_config_id ON aggregate_results (monitor_id, config_id, id)')

def downgrade():
    op.execute('DROP INDEX IF EXISTS ix_aggregate_results_monitor_config_id')
