import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from app.api import app
from app.engine import compute
from app.models import Config


def test_old_statistics_settings_do_not_affect_prices(cfg, sample):
    old = cfg.model_dump(mode='json') | dict(statsAlternative='greater', deltaBps=0,
        alpha=.05, minMinutes=30, maxMinutes=60, minSlots=4, family='primary')
    restored = Config.model_validate(old)
    assert restored.model_dump() == cfg.model_dump()
    snapshot = sample()
    assert compute(restored, snapshot) == compute(cfg, snapshot)
    assert 'minMinutes' not in Config.model_json_schema()['properties']
    assert TestClient(app).get('/api/monitors/any/statistics').status_code == 404


def test_statistics_migration_preserves_price_data(tmp_path):
    path = tmp_path / 'upgrade.sqlite3'
    env = {**os.environ, 'DATABASE_PATH': str(path)}
    cwd = Path(__file__).resolve().parents[1]

    def upgrade(revision):
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', revision],
                       cwd=cwd, env=env, check=True, capture_output=True)

    upgrade('0005')
    with sqlite3.connect(path) as db:
        for table in ('minute_blocks', 'statistical_results'):
            db.execute(f"INSERT INTO {table}(monitor_id,config_id,created_at,payload) VALUES('m','c','2026-10-26','{{}}')")
        db.execute("INSERT INTO raw_snapshots(monitor_id,config_id,created_at,payload,fingerprint) VALUES('m','c','2026-10-26','{}','fingerprint')")
        db.execute("INSERT INTO aggregate_results(monitor_id,config_id,created_at,payload,snapshot_id) VALUES('m','c','2026-10-26','{}',1)")
        db.execute("INSERT INTO run_configs(id,monitor_id,version,hash,payload,created_at) VALUES('c','m',1,'hash','{}','2026-10-26')")
    upgrade('head')
    with sqlite3.connect(path) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {'minute_blocks', 'statistical_results'} & tables
        for table in ('raw_snapshots', 'aggregate_results', 'run_configs'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 1
