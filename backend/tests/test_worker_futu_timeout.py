"""Plan item 1 (PR 1): background futu task, E/E.1 statuses, lifecycle, retention exclusion, status contract.

No live OpenD: FutuProvider runs on the fake context from test_futu_cache with an injected monotonic clock.
"""
import asyncio
import logging
import re
import threading
from pathlib import Path
import pytest
from sqlalchemy import select
from app import worker as worker_mod
from app.worker import Worker, NON_FAILURE, NO_BACKOFF
from app.futu import FutuProvider
from app.futu_config import FutuSettings
from app.providers import ProviderError
from app.models import Config
from app.service import create_monitor
from app.db import Session, Monitor, ProviderStatus
from test_futu_cache import Fake, Mono, CLOCK23, EXPIRY, configs as futu_configs

DOCS = Path(__file__).resolve().parents[2] / 'docs' / 'API.md'


def futu_cfg(symbol='SPY'):
    return futu_configs([symbol])[symbol]


@pytest.fixture
def make_worker():
    made = []
    def make(n_futu=2, n_mock=0, settings=None):
        w = Worker()
        mono = Mono(); ctx = Fake(mono)
        w.providers['futu'] = FutuProvider(ctx, clock=CLOCK23, mono=mono, settings=settings or FutuSettings())
        ids = {'futu': [], 'mock': []}
        with Session() as db:
            for m in db.scalars(select(Monitor).where(Monitor.active == True)):
                m.active = False
            db.commit()
            for s in ['SPY', 'QQQ', 'IWM', 'DIA'][:n_futu]:
                ids['futu'].append(create_monitor(db, futu_cfg(s)).id)
            for _ in range(n_mock):
                ids['mock'].append(create_monitor(db, Config(provider='mock', dataMode='MOCK', nBelow=1, timeRule='days365', remainingDays=2)).id)
        made.append((w, ids))
        return w, ids, ctx, mono
    yield make
    with Session() as db:
        for m in db.scalars(select(Monitor).where(Monitor.active == True)):
            m.active = False
        db.commit()


async def tick(w):
    """One worker cycle with every monitor due, then wait for the background futu task."""
    w.next_due.clear()
    await w.cycle()
    if w.futu_task is not None:
        await asyncio.wait({w.futu_task})


def last_status(mid):
    with Session() as db:
        row = db.scalars(select(ProviderStatus).where(ProviderStatus.monitor_id == mid).order_by(ProviderStatus.id.desc())).first()
        return row.payload if row else None


async def close(w):
    await w.stop()


# --- Test 3: a failed futu batch never re-collects ---------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize('raised,status', [(ProviderError('TIMEOUT'), 'TIMEOUT'), (ProviderError('OPEND_OFFLINE'), 'OPEND_OFFLINE'),
                                           (RuntimeError('boom'), 'INVALID_PROVIDER_DATA')])
async def test_3_failed_batch_never_recollects(make_worker, raised, status, monkeypatch):
    w, ids, ctx, mono = make_worker()
    calls = []
    async def batch(configs):
        calls.append(1); raise raised
    w.providers['futu'].collect_batch = batch
    for name in ('get_underlying_snapshots', 'list_expirations', 'list_option_contracts', 'get_option_quotes', 'get_market_state'):
        monkeypatch.setattr(w.providers['futu'], name, lambda *a, **k: pytest.fail('futu re-collected individually'))
    try:
        await tick(w)
        assert calls == [1] and ctx.log == []
        assert all(last_status(m)['status'] == status for m in ids['futu'])
    finally:
        await close(w)


# --- Test 10: hanging SDK call, E.1 escalation (FUTU_WATCHDOG=0) ----------------------
async def hang_until(p, mono, gate):
    """Start a call that hangs (real thread, fake clock) and let the batch timeout give up on it."""
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(p.run(gate.wait, 30), .05)


@pytest.mark.asyncio
async def test_10_hang_busy_then_unresponsive_one_warning_one_error_no_rebuild(caplog):
    caplog.set_level(logging.INFO, 'app.futu')
    mono = Mono(); ctx = Fake(mono); gate = threading.Event()
    p = FutuProvider(ctx, clock=CLOCK23, mono=mono, settings=FutuSettings())
    executor = p.executor
    try:
        await hang_until(p, mono, gate)
        seen = []
        for t in range(10, 311, 10):          # one batch per 10 s on the fake clock, up to 310 s
            mono.t = 1000.0 + t
            with pytest.raises(ProviderError) as e:
                await p.run(lambda: pytest.fail('a second SDK call started'))
            seen.append((t, e.value.code, getattr(e.value, 'pending_seconds', None), e.value.operation, e.value.retry_after))
        # (i) BUSY below 60 s, then OPEND_UNRESPONSIVE with operation and pendingSeconds
        assert [c for t, c, *_ in seen if t < 60] == ['FUTU_BUSY'] * 5
        assert all(c == 'OPEND_UNRESPONSIVE' for t, c, *_ in seen if t >= 60)
        assert all(op == 'busy' for t, c, _, op, _ in seen if c == 'FUTU_BUSY')
        assert all(op == 'wait' for t, c, _, op, _ in seen if c == 'OPEND_UNRESPONSIVE')  # fn name, no SDK method yet
        assert seen[-1][2] == 310
        assert {r for *_, r in seen} == {10}                             # (iv) fixed retry 10 throughout
        # (ii) one WARNING at entry, one ERROR at 300 s, nothing secret
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warns) == 1 and len(errors) == 1 and 'still pending after 300s' in errors[0].getMessage()
        assert not any('PRIVATE' in r.getMessage() for r in caplog.records)
        # (iii) no rebuild: no SDK call, no close, same context and executor
        assert ctx.log == [] and not ctx.closed and p.context is ctx and p.executor is executor
        # (v) the call returns -> INFO with the stuck duration; next batch runs normally
        gate.set(); await asyncio.wait({p.pending})
        await asyncio.sleep(0)
        assert any(r.levelno == logging.INFO and 'returned after 310s' in r.getMessage() for r in caplog.records)
        snaps, errors_ = await p.collect_batch(futu_configs(['SPY']))
        assert errors_['SPY'].code == 'CHAIN_WARMING' and p.busy_streak == 0
    finally:
        gate.set(); await p.close()


@pytest.mark.asyncio
async def test_10_vi_busy_streak_triggers_only_when_hang_threshold_exceeds_streak_duration():
    """(vi) The streak is reachable only if FUTU_HANG_SECONDS > FUTU_BUSY_ALERT_CYCLES*10+9 (10 s cadence).

    With HANG=300 and cycles=6, the 6th busy batch (pending ~60 s, far below 300 s) escalates.
    With the defaults (60, 6) the streak and the hang threshold coincide at 60 s.
    """
    mono = Mono(); ctx = Fake(mono); gate = threading.Event()
    p = FutuProvider(ctx, clock=CLOCK23, mono=mono, settings=FutuSettings(hang_seconds=300, busy_alert_cycles=6))
    try:
        await hang_until(p, mono, gate)
        codes = []
        for i in range(1, 7):
            mono.t = 1000.0 + 10 * i
            with pytest.raises(ProviderError) as e:
                await p.run(lambda: None)
            codes.append(e.value.code)
        assert codes == ['FUTU_BUSY'] * 5 + ['OPEND_UNRESPONSIVE']
        assert e.value.busy_cycles == 6 and e.value.pending_seconds == 60 < 300
    finally:
        gate.set(); await p.close()


@pytest.mark.asyncio
async def test_10_vii_maintenance_busy_never_counts_toward_streak(make_worker):
    w, ids, ctx, mono = make_worker()
    try:
        w.maintenance = True
        for _ in range(8):
            await tick(w)
        st = last_status(ids['futu'][0])
        assert st['status'] == 'FUTU_BUSY' and st['operation'] == 'maintenance'
        assert w.providers['futu'].busy_streak == 0 and ctx.log == []
    finally:
        await close(w)


@pytest.mark.asyncio
async def test_10_mock_monitors_complete_while_futu_hangs(make_worker):
    w, ids, ctx, mono = make_worker(n_futu=1, n_mock=1)
    gate = threading.Event()
    async def hung(configs):
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
    w.providers['futu'].collect_batch = hung
    try:
        w.next_due.clear(); await w.cycle()                 # futu task starts and hangs
        for _ in range(3):
            w.next_due.clear(); await w.cycle()
            assert last_status(ids['mock'][0])['status'] != 'TIMEOUT'
        assert last_status(ids['futu'][0]) is None          # in-flight futu monitors are skipped, not duplicated
        assert not w.futu_task.done()
    finally:
        gate.set(); await close(w)


# --- Test 11: non-failure statuses ------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize('sequence', [['FUTU_BUSY', 'FUTU_BUSY'], ['CHAIN_WARMING', 'FUTU_BUSY'],
                                      ['OPEND_UNRESPONSIVE', 'CHAIN_UNAVAILABLE']])
async def test_11_non_failure_and_no_backoff_statuses_keep_base_backoff(make_worker, sequence):
    w, ids, ctx, mono = make_worker(n_futu=1)
    mid = ids['futu'][0]; queue = list(sequence) + ['TIMEOUT']
    async def batch(configs):
        code = queue.pop(0); e = ProviderError(code, 10 if code != 'TIMEOUT' else 0)
        if code == 'CHAIN_WARMING': e.chain = {'state': 'warming', 'reason': 'queued'}
        if code == 'OPEND_UNRESPONSIVE': e.pending_seconds, e.busy_cycles, e.operation = 61.0, 1, 'get_market_snapshot'
        return {}, {m: e for m in configs}
    w.providers['futu'].collect_batch = batch
    try:
        for code in sequence:
            await tick(w)
            st = last_status(mid)
            assert st['status'] == code and st['retryAfter'] == 10 and w.failures.get(mid, 0) == 0
        await tick(w)
        st = last_status(mid)
        assert st['status'] == 'TIMEOUT' and st['retryAfter'] == 10 and w.failures[mid] == 1  # not 40 s
    finally:
        await close(w)


@pytest.mark.asyncio
async def test_11_status_row_carries_pending_seconds_and_chain(make_worker):
    w, ids, ctx, mono = make_worker(n_futu=1)
    mid = ids['futu'][0]
    try:
        await tick(w)
        st = last_status(mid)
        assert st['status'] == 'CHAIN_WARMING' and st['operation'] == 'get_option_chain'
        assert st['chain']['state'] == 'warming' and st['chain']['reason'] == 'queued' and 'waitingSeconds' in st['chain']
        mono.t += 10; await tick(w)
        assert last_status(mid)['status'] not in ('CHAIN_WARMING',)  # quoted in the next batch
        e = ProviderError('OPEND_UNRESPONSIVE', 10); e.operation = 'get_market_snapshot'; e.pending_seconds = 75.0; e.busy_cycles = 2
        async def batch(configs): raise e
        w.providers['futu'].collect_batch = batch
        await tick(w)
        st = last_status(mid)
        assert (st['status'], st['operation'], st['pendingSeconds'], st['busyCycles']) == ('OPEND_UNRESPONSIVE', 'get_market_snapshot', 75.0, 2)
    finally:
        await close(w)


@pytest.mark.asyncio
async def test_11_info_logged_on_status_change_only(make_worker, caplog):
    caplog.set_level(logging.INFO, 'app.worker')
    w, ids, ctx, mono = make_worker(n_futu=1)
    w.maintenance = True
    try:
        for _ in range(3):
            await tick(w)
        assert len([r for r in caplog.records if 'status FUTU_BUSY' in r.getMessage()]) == 1
    finally:
        await close(w)


# --- Test 11b: the docs status table is the frontend contract ----------------------
def contract_rows():
    text = DOCS.read_text(encoding='utf-8')
    section = text.split('## Futu 用户可见状态（前端契约）', 1)[1].split('\n## ', 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        m = re.match(r'`([A-Z_]+)`', cells[0]) if len(cells) >= 6 else None
        if m: rows[m.group(1)] = cells
    return rows


def test_11b_status_contract_matches_worker_sets():
    rows = contract_rows()
    for status in ('OPEND_OFFLINE', 'OPEND_UNRESPONSIVE', 'TIMEOUT', 'FUTU_BUSY', 'CHAIN_WARMING', 'CHAIN_UNAVAILABLE'):
        assert status in rows, status
        name, where, cls, fields, label_key, label = rows[status][:6]
        assert label_key and label, status                          # every status has a label key and text
        assert ('非失败' in cls) == (status in NON_FAILURE), status
        assert ('不退避' in cls) == (status in NO_BACKOFF), status
        assert '60 秒' not in label and '11111' not in label           # no hard-coded thresholds/ports
    assert '{pendingSeconds}' in rows['OPEND_UNRESPONSIVE'][5] and 'pendingSeconds' in rows['OPEND_UNRESPONSIVE'][3]
    assert set(NON_FAILURE) | set(NO_BACKOFF) <= set(rows)


# --- Test 12: lifecycle -----------------------------------------------------------
@pytest.mark.asyncio
async def test_12_stop_order_cancel_task_await_pending_then_close(make_worker, caplog):
    caplog.set_level(logging.INFO)
    w, ids, ctx, mono = make_worker(n_futu=1)
    p = w.providers['futu']; gate = threading.Event(); order = []
    async def batch(configs):
        try:
            await p.run(gate.wait, 30)
        except asyncio.CancelledError:
            order.append('task cancelled'); raise
    p.collect_batch = batch
    original_close = ctx.close
    ctx.close = lambda: (order.append('context.close'), original_close())
    try:
        w.next_due.clear(); await w.cycle(); await asyncio.sleep(.05)
        stopping = asyncio.create_task(w.stop())
        await asyncio.sleep(.05)
        assert order == ['task cancelled'] and not ctx.closed      # close waits for the pending call
        order.append('pending returns'); gate.set()
        await stopping
        assert order == ['task cancelled', 'pending returns', 'context.close']
        assert any(r.getMessage() == 'worker stopped' for r in caplog.records)
    finally:
        gate.set()


@pytest.mark.asyncio
async def test_12_close_with_hung_call_logs_error_and_completes(caplog):
    caplog.set_level(logging.ERROR, 'app.futu')
    mono = Mono(); ctx = Fake(mono); gate = threading.Event()
    p = FutuProvider(ctx, clock=CLOCK23, mono=mono, settings=FutuSettings(close_wait=.1))
    try:
        await hang_until(p, mono, gate)
        await asyncio.wait_for(p.close(), 2)
        assert not ctx.closed
        assert any('context not closed' in r.getMessage() for r in caplog.records)
    finally:
        gate.set()


# --- Test 13: retention exclusion ------------------------------------------------
@pytest.mark.asyncio
async def test_13_retention_waits_for_task_and_blocks_new_ones(make_worker, monkeypatch, tmp_path):
    w, ids, ctx, mono = make_worker(n_futu=1)
    p = w.providers['futu']; release = asyncio.Event(); events = []
    async def batch(configs):
        events.append('batch start'); await release.wait(); events.append('batch end'); return {}, {}
    p.collect_batch = batch
    def retention_job(path):
        events.append('retention')
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / 'r.sqlite3'), timeout=15)  # temp DB, never the live one
        conn.execute('create table if not exists t(x)'); conn.execute('insert into t values (1)'); conn.commit(); conn.close()
    monkeypatch.setattr(worker_mod.retention, 'run_on_path', retention_job)
    try:
        w.next_due.clear(); await w.cycle(); await asyncio.sleep(0)
        job = asyncio.create_task(w.run_retention())
        await asyncio.sleep(.05)
        assert w.maintenance and events == ['batch start']          # retention waits for the running task
        w.next_due.clear(); await w.cycle()                             # in-flight monitor skipped, no new task
        release.set(); await job
        assert events == ['batch start', 'batch end', 'retention'] and not w.maintenance
        w.maintenance = True
        w.next_due.clear(); await w.cycle()
        st = last_status(ids['futu'][0])
        assert st['status'] == 'FUTU_BUSY' and st['operation'] == 'maintenance' and events.count('batch start') == 1
    finally:
        w.maintenance = False; await close(w)


@pytest.mark.asyncio
async def test_13_slow_retention_warns_once(make_worker, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, 'app.worker')
    w, ids, ctx, mono = make_worker(n_futu=0)
    import time as _t
    monkeypatch.setattr(worker_mod.retention, 'run_on_path', lambda path: _t.sleep(.3))
    monkeypatch.setattr(worker_mod.futu_config, 'settings', lambda: FutuSettings(maint_alert_seconds=.1))
    try:
        await w.run_retention()
        assert len([r for r in caplog.records if 'retention still running' in r.getMessage()]) == 1
    finally:
        await close(w)
