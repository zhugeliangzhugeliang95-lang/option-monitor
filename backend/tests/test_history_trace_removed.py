import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.api import app


@pytest.mark.parametrize('method,path', [
    ('get', '/api/snapshots/1'),
    ('get', '/api/exports/test'),
    ('get', '/api/exports/test?format=csv'),
    ('get', '/api/research/frozen-observations?monitorId=test'),
    ('post', '/api/research/frozen-observations'),
    ('post', '/api/research/frozen-observations/1/outcomes'),
])
def test_retired_routes_are_absent(method, path):
    assert getattr(TestClient(app), method)(path).status_code == 404


def test_upgrade_removes_only_trace_storage(tmp_path):
    path = tmp_path / 'upgrade.sqlite3'
    env = {**os.environ, 'DATABASE_PATH': str(path)}
    cwd = Path(__file__).resolve().parents[1]

    def upgrade(revision):
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', revision],
                       cwd=cwd, env=env, check=True, capture_output=True)

    upgrade('0004')
    with sqlite3.connect(path) as db:
        for table in ('frozen_observations', 'observation_outcomes'):
            db.execute(f"INSERT INTO {table}(monitor_id,config_id,created_at,payload) VALUES('m','c','2026-10-26','{{}}')")
        db.execute("INSERT INTO raw_snapshots(monitor_id,config_id,created_at,payload,fingerprint) VALUES('m','c','2026-10-26','{}','fingerprint')")
        db.execute("INSERT INTO aggregate_results(monitor_id,config_id,created_at,payload,snapshot_id) VALUES('m','c','2026-10-26','{}',1)")
        db.execute("INSERT INTO run_configs(id,monitor_id,version,hash,payload,created_at) VALUES('c','m',1,'hash','{}','2026-10-26')")
    upgrade('head')
    with sqlite3.connect(path) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {'frozen_observations', 'observation_outcomes'} & tables
        for table in ('raw_snapshots', 'aggregate_results', 'run_configs'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            db.execute('DELETE FROM raw_snapshots')
