import os,subprocess,sys,sqlite3
from pathlib import Path

def test_migration_wal_and_immutable_triggers(tmp_path):
    path=tmp_path/'audit.sqlite3';env={**os.environ,'DATABASE_PATH':str(path)}
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=Path(__file__).resolve().parents[1],env=env,check=True,capture_output=True)
    db=sqlite3.connect(path)
    assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    db.execute("INSERT INTO run_configs(id,monitor_id,version,hash,payload,created_at) VALUES('c','m',1,'hash','{}','2026-10-19T00:00:00Z')")
    db.commit()
    for sql in ("UPDATE run_configs SET hash='changed' WHERE id='c'","DELETE FROM run_configs WHERE id='c'"):
        try: db.execute(sql)
        except sqlite3.IntegrityError as e: assert 'append-only' in str(e)
        else: raise AssertionError('immutable trigger did not reject mutation')
    assert db.execute("SELECT name FROM sqlite_master WHERE name='paper_runs'").fetchone() is None
    assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0003'
    db.close()

def test_migration_0003_downgrade_recreates_paper_runs(tmp_path):
    path=tmp_path/'audit.sqlite3';env={**os.environ,'DATABASE_PATH':str(path)};cwd=Path(__file__).resolve().parents[1]
    run=lambda *a: subprocess.run([sys.executable,'-m','alembic',*a],cwd=cwd,env=env,check=True,capture_output=True)
    run('upgrade','0002'); db=sqlite3.connect(path)
    schema=lambda: sorted(r for r in db.execute("SELECT type,name,sql FROM sqlite_master WHERE tbl_name='paper_runs'"))
    before=schema(); db.close(); run('upgrade','head'); run('downgrade','0002'); db=sqlite3.connect(path)
    assert schema()==before and before
    db.execute("INSERT INTO paper_runs(id,created_at,payload) VALUES('paper','2026-10-19T00:00:00Z','{}')"); db.commit()
    with __import__('pytest').raises(sqlite3.IntegrityError, match='append-only'):
        db.execute("DELETE FROM paper_runs WHERE id='paper'")
    db.close()
