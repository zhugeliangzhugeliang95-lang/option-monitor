"""Daily retention: keep only the current exchange (US/Eastern) trading day of high-volume result tables.

Runs inside the worker process between collection cycles, once per day at 08:00 exchange time.
Configuration and worker tables are never touched. No backup/export is produced and
VACUUM is not run automatically; to reclaim disk space stop the worker/API and run manually:
    sqlite3 data/monitor.sqlite3 'PRAGMA wal_checkpoint(TRUNCATE); VACUUM;'
"""
import json, logging, os, sqlite3, time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo
from .models import Config

log=logging.getLogger('app.retention')
PRUNED_TABLES=('raw_snapshots','aggregate_results','pair_results','provider_status','result_outbox')
KEPT_TABLES=('monitors','run_configs','worker_state')
BATCH=2000
RUN_AT=dtime(8,0)

def exchange_zone(): return ZoneInfo(Config.model_fields['exchangeTimezone'].default)
def retention_days():
    try: return max(0,int(os.getenv('RETENTION_DAYS','1')))
    except ValueError: return 1

def boundary(now,zone=None,days=1):
    """UTC ISO string of local midnight starting the oldest kept trading day (created_at is UTC isoformat)."""
    zone=zone or exchange_zone(); day=now.astimezone(zone).date()-timedelta(days=days-1)
    return datetime.combine(day,dtime(0),zone).astimezone(timezone.utc).isoformat()

def due(now,last_day,zone=None):
    local=now.astimezone(zone or exchange_zone())
    return local.time()>=RUN_AT and local.date()!=last_day

def _count(conn,table): return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]

def cleanup(conn:sqlite3.Connection,now=None,days=None,zone=None,batch=BATCH):
    """Delete rows with created_at before the boundary in id batches, committing per batch. Idempotent."""
    days=retention_days() if days is None else days
    if days<=0:
        log.info('retention disabled (RETENTION_DAYS=0)'); return None
    now=now or datetime.now(timezone.utc); cut=boundary(now,zone,days); started=time.monotonic()
    conn.isolation_level=None
    tables=[t for t in PRUNED_TABLES if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone()]
    before={t:_count(conn,t) for t in tables}; deleted={}
    log.info('retention start boundary=%s days=%s counts_before=%s',cut,days,before)
    for t in tables:
        trig=conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND name LIKE 'immutable_%_delete'",(t,)).fetchall()
        deleted[t]=0
        while True:
            # Append-only delete guards are lifted only inside this transaction and restored before COMMIT.
            conn.execute('BEGIN IMMEDIATE')
            try:
                ids=[r[0] for r in conn.execute(f'SELECT id FROM {t} WHERE created_at<? ORDER BY id LIMIT ?',(cut,batch))]
                if not ids: conn.execute('COMMIT'); break
                for name,_ in trig: conn.execute(f'DROP TRIGGER {name}')
                conn.execute(f'DELETE FROM {t} WHERE id IN ({",".join("?"*len(ids))})',ids)
                for _,sql in trig: conn.execute(sql)
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK'); raise
            deleted[t]+=len(ids)
    after={t:_count(conn,t) for t in tables}; duration=time.monotonic()-started
    summary={'type':'retention','boundary':cut,'days':days,'deleted':deleted,'before':before,'after':after,'durationSeconds':round(duration,3),'at':now.isoformat()}
    conn.execute('INSERT INTO result_outbox(payload,created_at) VALUES(?,?)',(json.dumps(summary),datetime.now(timezone.utc).isoformat()))
    log.info('retention done boundary=%s deleted=%s counts_after=%s duration=%.3fs',cut,deleted,after,duration)
    return summary

def run_on_path(path,now=None,journal_size_limit=None):
    # Dedicated connection so pooled SQLAlchemy connections keep their transaction settings.
    # Same journal_size_limit as db.pragmas: the batched deletes are the largest transactions we write.
    if journal_size_limit is None:
        from .db import JOURNAL_SIZE_LIMIT as journal_size_limit
    conn=sqlite3.connect(str(path),timeout=15)
    try:
        conn.execute('PRAGMA busy_timeout=15000'); conn.execute(f'PRAGMA journal_size_limit={int(journal_size_limit)}')
        return cleanup(conn,now)
    finally: conn.close()
