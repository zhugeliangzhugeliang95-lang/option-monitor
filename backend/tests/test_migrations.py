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
    db.execute("INSERT INTO paper_runs(id,created_at,payload) VALUES('paper','2026-10-19T00:00:00Z','{}')")
    db.commit()
    for sql in ("UPDATE paper_runs SET payload='{}' WHERE id='paper'", "DELETE FROM paper_runs WHERE id='paper'"):
        with __import__('pytest').raises(sqlite3.IntegrityError, match='append-only'):
            db.execute(sql)
    db.close()
