"""Initial immutable audit schema."""
from alembic import op
import sqlalchemy as sa
from app.db import Base
revision='0001'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    Base.metadata.create_all(op.get_bind())
    for name in ('frozen_observations','observation_outcomes','minute_blocks','statistical_results'):
        legacy=sa.Table(name,sa.MetaData(),
            sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
            sa.Column('monitor_id',sa.String(),nullable=False,index=True),
            sa.Column('config_id',sa.String(),nullable=False,index=True),
            sa.Column('created_at',sa.String(),nullable=False,index=True),
            sa.Column('payload',sa.JSON(),nullable=False))
        legacy.create(op.get_bind(),checkfirst=True)
    for table in ('run_configs','raw_snapshots','pair_results','aggregate_results','minute_blocks','statistical_results','frozen_observations','observation_outcomes'):
        op.execute(f"CREATE TRIGGER immutable_{table}_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only table'); END")
        op.execute(f"CREATE TRIGGER immutable_{table}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only table'); END")
def downgrade():
    raise RuntimeError('Audit data cannot be destructively downgraded. Restore a database backup instead.')
