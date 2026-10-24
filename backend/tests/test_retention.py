import os, sqlite3, subprocess, sys, json
from datetime import datetime, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from app import retention

NY=ZoneInfo('America/New_York')
ALL=retention.PRUNED_TABLES+retention.KEPT_TABLES

@pytest.fixture(scope='module')
def migrated(tmp_path_factory):
    path=tmp_path_factory.mktemp('ret')/'base.sqlite3'
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=Path(__file__).resolve().parents[1],env={**os.environ,'DATABASE_PATH':str(path)},check=True,capture_output=True)
    return path

def fill(path,stamps):
    db=sqlite3.connect(path)
    for t in retention.PRUNED_TABLES+('frozen_observations','observation_outcomes'):
        for i,at in enumerate(stamps):
            if t=='result_outbox': db.execute('INSERT INTO result_outbox(payload,created_at) VALUES(?,?)',('{}',at))
            else:
                extra={'raw_snapshots':('fingerprint','f'),'pair_results':('snapshot_id',i),'aggregate_results':('snapshot_id',i)}.get(t)
                cols='monitor_id,config_id,created_at,payload'+(','+extra[0] if extra else ''); vals=['SIM-HISTORY','c',at,'{}']+([extra[1]] if extra else [])
                db.execute(f'INSERT INTO {t}({cols}) VALUES({",".join("?"*len(vals))})',vals)
    db.execute("INSERT INTO monitors(id,active,config_id,refresh_requested) VALUES('SIM-HISTORY',1,'c',0)")
    db.execute("INSERT INTO run_configs(id,monitor_id,version,hash,payload,created_at) VALUES('c','SIM-HISTORY',1,'h','{}','2020-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO worker_state(id,payload) VALUES('singleton','{}')"); db.commit(); return db

def counts(db): return {t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ALL}

@pytest.fixture
def dbpath(migrated,tmp_path):
    p=tmp_path/'db.sqlite3'; sqlite3.connect(migrated).backup(c:=sqlite3.connect(p)); c.close(); return p

@pytest.mark.parametrize('now,cut',[
    (datetime(2026,7,15,13,0,tzinfo=timezone.utc),'2026-07-15T04:00:00+00:00'),   # EDT, UTC-4
    (datetime(2026,12,15,13,0,tzinfo=timezone.utc),'2026-12-15T05:00:00+00:00'),  # EST, UTC-5
    (datetime(2026,3,8,13,0,tzinfo=timezone.utc),'2026-03-08T05:00:00+00:00'),    # spring-forward day: midnight still EST
    (datetime(2026,11,1,13,0,tzinfo=timezone.utc),'2026-11-01T04:00:00+00:00'),   # fall-back day: midnight still EDT
    (datetime(2026,3,9,3,30,tzinfo=timezone.utc),'2026-03-08T05:00:00+00:00'),    # 23:30 ET previous local day
])
def test_boundary_across_dst(now,cut): assert retention.boundary(now,NY,1)==cut

def test_boundary_days_two(): assert retention.boundary(datetime(2026,3,9,13,tzinfo=timezone.utc),NY,2)=='2026-03-08T05:00:00+00:00'

def test_due_at_0800_et_once_per_day():
    assert not retention.due(datetime(2026,11,2,12,59,tzinfo=timezone.utc),None,NY)  # 07:59 EST
    assert retention.due(datetime(2026,11,2,13,0,tzinfo=timezone.utc),None,NY)
    assert not retention.due(datetime(2026,11,2,20,0,tzinfo=timezone.utc),date(2026,11,2),NY)
    assert retention.due(datetime(2026,7,2,12,0,tzinfo=timezone.utc),date(2026,7,1),NY)  # 08:00 EDT

def test_cleanup_deletes_old_keeps_today_and_kept_tables(dbpath):
    now=datetime(2026,11,1,13,0,tzinfo=timezone.utc)
    db=fill(dbpath,['2026-10-31T12:00:00+00:00','2026-11-01T03:59:59.999999+00:00','2026-11-01T04:00:00+00:00','2026-11-01T12:00:00.5+00:00'])
    before=counts(db)
    s=retention.cleanup(db,now,1,NY,batch=1)
    after=counts(db)
    for t in retention.PRUNED_TABLES: assert after[t]==2+(t=='result_outbox'), t
    for t in retention.KEPT_TABLES: assert after[t]==before[t], t
    assert s['deleted']['raw_snapshots']==2 and s['type']=='retention'
    ev=json.loads(db.execute('SELECT payload FROM result_outbox ORDER BY id DESC').fetchone()[0]); assert ev['type']=='retention' and ev['boundary']=='2026-11-01T04:00:00+00:00'
    # append-only guards restored
    trig={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {'immutable_raw_snapshots_delete','immutable_aggregate_results_delete','immutable_frozen_observations_delete'}<=trig
    with pytest.raises(sqlite3.IntegrityError,match='append-only'): db.execute('DELETE FROM raw_snapshots')
    # idempotent: second run deletes nothing beyond its own previous event (which is today's)
    s2=retention.cleanup(db,now,1,NY)
    assert all(v==0 for t,v in s2['deleted'].items() if t!='result_outbox')  # previous event row uses wall-clock time
    assert {t:v for t,v in counts(db).items() if t!='result_outbox'}=={t:v for t,v in after.items() if t!='result_outbox'}

def test_disabled_switch(dbpath,monkeypatch):
    db=fill(dbpath,['2020-01-01T00:00:00+00:00']); before=counts(db)
    monkeypatch.setenv('RETENTION_DAYS','0')
    assert retention.cleanup(db,datetime(2026,11,1,13,tzinfo=timezone.utc)) is None
    assert counts(db)==before

def test_env_default_is_one(monkeypatch):
    monkeypatch.delenv('RETENTION_DAYS',raising=False); assert retention.retention_days()==1
