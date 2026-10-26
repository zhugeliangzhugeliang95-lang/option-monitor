"""PR-D: every connection sets PRAGMA journal_size_limit; after retention the WAL stays within the limit."""
import os, sqlite3
from datetime import datetime, timezone
from app import retention
from app.db import engine, JOURNAL_SIZE_LIMIT
from test_retention import migrated, dbpath  # noqa: F401 (fixtures)

NOW = datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)
OLD = '2026-10-30T12:00:00+00:00'


def test_engine_connections_set_64mb_limit():
    assert JOURNAL_SIZE_LIMIT == 67108864
    with engine.connect() as c:
        assert c.exec_driver_sql('PRAGMA journal_size_limit').scalar() == 67108864
        assert c.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'


def test_run_on_path_sets_limit(dbpath, monkeypatch):
    seen = []
    real = retention.cleanup
    def spy(conn, now=None, **kw):
        seen.append(conn.execute('PRAGMA journal_size_limit').fetchone()[0]); return real(conn, now, **kw)
    monkeypatch.setattr(retention, 'cleanup', spy)
    retention.run_on_path(dbpath, NOW)
    assert seen == [67108864]


def _big_wal_after_retention(path, limit):
    db = sqlite3.connect(path); db.execute('PRAGMA journal_mode=WAL')
    pad = 'x' * 4000
    db.executemany('INSERT INTO result_outbox(payload,created_at) VALUES(?,?)', [(pad, OLD)] * 3000); db.commit()
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    reader = sqlite3.connect(path)  # keeps the WAL file in place after run_on_path closes its connection
    reader.execute('SELECT 1').fetchone()
    try:
        s = retention.run_on_path(path, NOW, journal_size_limit=limit) if limit is not None else None
        if limit is None:
            conn = sqlite3.connect(path, timeout=15); conn.execute('PRAGMA journal_size_limit=-1')
            s = retention.cleanup(conn, NOW); conn.close()
        assert s['deleted']['result_outbox'] == 3000
        size = os.path.getsize(str(path) + '-wal')
        reader.execute('PRAGMA wal_checkpoint(PASSIVE)')
        return size, os.path.getsize(str(path) + '-wal')
    finally:
        reader.close(); db.close()


def test_wal_truncated_to_limit_after_retention(dbpath, tmp_path):
    limit = 256 * 1024  # small limit in the test to see the mechanism; production uses 64 MB
    unlimited = tmp_path / 'u.sqlite3'
    sqlite3.connect(dbpath).backup(c := sqlite3.connect(unlimited)); c.close()
    _, grown = _big_wal_after_retention(unlimited, None)
    _, capped = _big_wal_after_retention(dbpath, limit)
    assert grown > 4 * limit           # one 2000-row batch inflates the WAL and it never shrinks
    assert capped <= limit             # with the limit, a completed checkpoint truncates it
