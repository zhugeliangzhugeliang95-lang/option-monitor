"""Plan item 1 (PR 2): day roll (b), stale-while-revalidate (c), pre-warm / warm window / offline (F).

Tests 6 (i-viii), 7 (i-viii), 14 and 11d of plan v5. No live OpenD: the fake context from
test_futu_cache, and a wall clock tied to the fake monotonic clock so chain ages are exact.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from sqlalchemy import select
from app.futu import FutuProvider, CHAIN_TTL, STAGGER, stagger
from app.futu_config import FutuSettings
from app.models import Config
from app.providers import ProviderError
from app.db import Session, Monitor, AggregateResult
from app.service import create_monitor
from app.worker import Worker
from test_futu_cache import Fake, Mono, EXPIRY, configs

UTC = timezone.utc
NY = ZoneInfo('America/New_York')
FRI_11 = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)     # Fri 2026-10-23 11:00 New York (EDT)
LIMIT = CHAIN_TTL + STAGGER


class Wall:
    """Wall clock = base + elapsed fake-monotonic seconds; `jump` moves wall time only (sleep/suspend)."""
    def __init__(self, mono, base):
        self.mono, self.base, self.t0 = mono, base, mono.t
    def __call__(self):
        return self.base + timedelta(seconds=self.mono.t - self.t0)
    def jump(self, seconds):
        self.base += timedelta(seconds=seconds)


class Flaky(Fake):
    """Fake whose chain / expiration calls can fail with a non-rate-limit vendor error."""
    def __init__(self, mono, **kw):
        super().__init__(mono, **kw)
        self.chain_error = None
        self.dates_error = None
        self.wall = None
        self.expiry = {}
    def get_option_chain(self, code, start, end):
        if self.chain_error:
            self._do('get_option_chain'); return -1, self.chain_error
        ret, df = super().get_option_chain(code, start, end)
        if ret == 0:
            self.expiry.update({c: start for c in df['code']})
        return ret, df
    def get_market_snapshot(self, codes):
        # quote time follows the fake wall clock and option rows carry their chain's expiry
        ret, df = super().get_market_snapshot(codes)
        if self.wall is not None:
            df['update_time'] = self.wall().astimezone(NY).strftime('%Y-%m-%d %H:%M:%S')
        df['strike_time'] = [self.expiry.get(c, EXPIRY) for c in df['code']]
        return ret, df
    def get_option_expiration_date(self, code):
        if self.dates_error:
            self._do('get_option_expiration_date'); return -1, self.dates_error
        return super().get_option_expiration_date(code)


def make(base=FRI_11, settings=None, ctx_cls=Flaky):
    mono = Mono(); ctx = ctx_cls(mono); wall = Wall(mono, base); ctx.wall = wall
    return FutuProvider(ctx, clock=wall, mono=mono, settings=settings or FutuSettings()), ctx, mono, wall


def cfg(symbol='SPY', expiration=EXPIRY):
    return {symbol: configs([symbol])[symbol].model_copy(update={'expiration': expiration})}


async def run(p, cfgs, n=1, step=10.0):
    out = None
    for _ in range(n):
        out = await p.collect_batch(cfgs)
        p.mono.t += step
    return out


async def warmed(p, cfgs, rounds=10):
    for _ in range(rounds):
        snaps, errors = await run(p, cfgs)
        if not errors:
            return snaps
    raise AssertionError(f'never warmed: {errors}')


KEY = ('US.SPY', EXPIRY)


# --- Test 6: day roll, warm window, off-hours ---------------------------------------------
@pytest.mark.asyncio
async def test_6i_same_day_chain_never_crosses_the_roll():
    """(b3) A chain for X == D is only served if fetched on D; at the roll it is dropped, not served stale."""
    p, ctx, mono, wall = make(base=datetime(2026, 10, 22, 19, 50, tzinfo=UTC))  # Thu 15:50 NY
    try:
        thu = cfg(expiration='2026-10-23')  # X > D on Thursday, X == D on Friday
        await warmed(p, thu)
        assert p.chains[('US.SPY', '2026-10-23')]['fetchedDay'] == '2026-10-22'
        wall.jump(17 * 3600 + 20 * 60)        # Fri 09:10 NY, inside the window
        snaps, errors = await run(p, thu)
        # the Thursday chain was not served on Friday: refetched (class 1) in this batch, quoted next
        assert p.chains[('US.SPY', '2026-10-23')]['fetchedDay'] == '2026-10-23'
        assert 'SPY' not in snaps and errors['SPY'].code == 'CHAIN_WARMING'
        snaps, errors = await run(p, thu)
        assert 'SPY' in snaps and 'SPY' not in p.chain_meta
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_6ii_future_chain_kept_across_roll_and_served_stale():
    """(b4) X > D survives the roll and is SWR-served (age < MAX_AGE_FUTURE) with stale meta."""
    p, ctx, mono, wall = make(base=datetime(2026, 10, 22, 19, 50, tzinfo=UTC))
    try:
        later = cfg(expiration='2026-10-30')
        await warmed(p, later)
        wall.jump(17 * 3600 + 20 * 60)
        mono.t += LIMIT  # past the fresh TTL as well
        snaps, errors = await run(p, later)
        assert 'SPY' in snaps and not errors
        meta = p.chain_meta['SPY']
        assert meta['stale'] is True and meta['ageSeconds'] >= 17 * 3600 and meta['refreshFailures'] == 0
        assert 'stale' not in snaps['SPY'].model_dump_json()   # metadata never enters the Snapshot
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_6iii_yesterdays_expirations_flagged_stale_when_refetch_fails():
    """(b1) Yesterday's E is only usable through live_expirations and is flagged expirationsStale.
    Offline E is simulated by a failing E call (a real offline fails permissions first)."""
    p, ctx, mono, wall = make(base=datetime(2026, 10, 22, 19, 50, tzinfo=UTC))
    try:
        later = cfg(expiration='2026-10-30')
        await warmed(p, later)
        ctx.dates_error = '内部错误'
        wall.jump(17 * 3600 + 20 * 60)
        snaps, errors = await run(p, later)
        assert 'SPY' in snaps and p.chain_meta['SPY']['expirationsStale'] is True
        assert p.dates['US.SPY']['fetchedDay'] == '2026-10-22'
        ctx.dates_error = None
        mono.t += 60
        await run(p, later)                     # E refetched for the new day
        snaps, errors = await run(p, later)
        assert p.dates['US.SPY']['fetchedDay'] == '2026-10-23'
        assert not p.chain_meta.get('SPY', {}).get('expirationsStale')
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_6iv_sweep_removes_expired_chains_and_their_state(caplog):
    """(b10) First batch of a new NY day deletes X == D chains fetched before the roll and X < D refresh state.
    (SPY 2026-10-22 is not in the fake's expiration list, so it never had a chain; its refresh entry is swept.)"""
    p, ctx, mono, wall = make(base=datetime(2026, 10, 22, 19, 0, tzinfo=UTC))
    try:
        both = {**cfg('SPY', '2026-10-22'), **cfg('QQQ', '2026-10-23')}
        await warmed(p, both)
        p.refresh[('US.SPY', '2026-10-22')] = {'failures': 1, 'next': 0.0}
        caplog.set_level(logging.INFO, logger='app.futu')
        wall.jump(14 * 3600)                      # Fri 09:00 NY
        await run(p, {'IWM': configs(['IWM'])['IWM']})
        assert ('US.SPY', '2026-10-22') not in p.chains and ('US.QQQ', '2026-10-23') not in p.chains
        assert not p.refresh
        assert 'futu day roll 2026-10-23: swept 1 chains' in caplog.text
    finally:
        await p.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('base,label', [
    (datetime(2026, 10, 23, 12, 30, tzinfo=UTC), 'before window'),   # Fri 08:30 NY
    (datetime(2026, 10, 23, 20, 20, tzinfo=UTC), 'after close+15'),  # Fri 16:20 NY
])
async def test_6v_off_hours_same_day_cold_is_warming_offhours_never_unavailable(base, label):
    """(b6) Outside the window no chain call; CHAIN_WARMING reason=offHours never hits the E.2 cap; E still fetched."""
    p, ctx, mono, wall = make(base=base, settings=FutuSettings(chain_warm_max_seconds=60))
    try:
        for _ in range(20):
            snaps, errors = await run(p, cfg(), step=31)
            e = errors['SPY']
            assert e.code == 'CHAIN_WARMING' and e.chain['reason'] == 'offHours' and e.chain['waitingSeconds'] == 0
        assert ctx.count('get_option_chain') == 0
        assert ctx.count('get_option_expiration_date') >= 1
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_6vi_weekend_is_off_hours():
    """(b7) Non-trading day: no window. A same-day manual expiry on Saturday is just offHours."""
    p, ctx, mono, wall = make(base=datetime(2026, 10, 24, 15, 0, tzinfo=UTC))
    try:
        assert p._window(cfg()['SPY'], wall()) is None
        snaps, errors = await run(p, cfg(expiration='2026-10-30'), n=3)
        assert ctx.count('get_option_chain') == 1   # (b5) one off-hours cold fetch for X > D
        assert 'SPY' in snaps
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_6vii_early_close_window_ends_at_1315():
    """(b8) Early close (Fri 2026-11-27, 13:00 close): the window ends 13:15 NY."""
    p, ctx, mono, wall = make(base=datetime(2026, 11, 27, 18, 14, tzinfo=UTC))  # 13:14 EST
    try:
        c = cfg(expiration='2026-11-27')['SPY']
        start, end = p._window(c, wall())
        assert start == datetime(2026, 11, 27, 14, 0, tzinfo=UTC) and end == datetime(2026, 11, 27, 18, 15, tzinfo=UTC)
        assert p.in_window(c, wall())
        wall.jump(120)
        assert not p.in_window(c, wall())
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_6viii_off_hours_future_cold_fetch_one_per_batch_and_no_refresh():
    """(b5) Off-hours: X > D cold keys are fetched at most one per batch; stale X > D is never refreshed off-hours."""
    p, ctx, mono, wall = make(base=datetime(2026, 10, 23, 23, 0, tzinfo=UTC),   # Fri 19:00 NY
                              settings=FutuSettings(warm_per_cycle=3))
    try:
        many = {s: configs([s])[s].model_copy(update={'expiration': '2026-10-30'}) for s in ('SPY', 'QQQ', 'IWM')}
        before = ctx.count('get_option_chain')
        await run(p, many)
        assert ctx.count('get_option_chain') - before == 1
        await run(p, many, n=3)
        assert ctx.count('get_option_chain') == 3
        mono.t += LIMIT
        snaps, errors = await run(p, many, n=3)
        assert ctx.count('get_option_chain') == 3       # stale, served, not refreshed off-hours
        assert set(snaps) == set(many) and all(p.chain_meta[s]['stale'] for s in many)
    finally:
        await p.close()


# --- Test 7: stale-while-revalidate --------------------------------------------------------
@pytest.mark.asyncio
async def test_7i_vii_stale_served_with_meta_fresh_has_none():
    p, ctx, mono, wall = make()
    try:
        await warmed(p, cfg())
        assert 'SPY' not in p.chain_meta                      # (vii) fresh: no metadata at all
        ctx.chain_error = '内部错误'
        mono.t = p.chains[KEY]['expires'] + 1
        snaps, errors = await run(p, cfg(), step=0)
        assert 'SPY' in snaps and not errors                   # (i) served stale
        m = p.chain_meta['SPY']
        assert m['stale'] is True and m['ageSeconds'] >= CHAIN_TTL and m['expirationsStale'] is False
        assert set(m) == {'stale', 'ageSeconds', 'fetchedAt', 'refreshFailures', 'expirationsStale'}
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_7ii_one_refresh_per_key_then_fresh():
    p, ctx, mono, wall = make(settings=FutuSettings(warm_per_cycle=5))
    try:
        await warmed(p, cfg())
        before = ctx.count('get_option_chain')
        mono.t = p.chains[KEY]['expires'] + 1
        await run(p, cfg(), step=0)
        assert ctx.count('get_option_chain') - before == 1     # one refresh, not warm_per_cycle
        snaps, _ = await run(p, cfg())
        assert 'SPY' not in p.chain_meta and KEY not in p.refresh
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_7iii_refresh_failure_backoff_and_info_log(caplog):
    caplog.set_level(logging.INFO, logger='app.futu')
    p, ctx, mono, wall = make()
    try:
        await warmed(p, cfg())
        ctx.chain_error = '内部错误'
        mono.t = p.chains[KEY]['expires'] + 1
        waits = []
        for n in range(1, 7):
            await run(p, cfg(), step=0)
            assert p.refresh[KEY]['failures'] == n
            waits.append(round(p.refresh[KEY]['next'] - mono.t))
            mono.t = p.refresh[KEY]['next']
        assert waits == [10, 20, 40, 80, 160, 300]
        assert 'futu chain refresh US.SPY 2026-10-23 failed' in caplog.text
        snaps, _ = await run(p, cfg(), step=0)
        assert 'SPY' in snaps and p.chain_meta['SPY']['refreshFailures'] == 7  # includes this batch's failure
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_7iv_rate_limited_refresh_is_not_a_failure():
    p, ctx, mono, wall = make()
    try:
        await warmed(p, cfg())
        ctx.chain_error = '请求过于频繁'
        mono.t = p.chains[KEY]['expires'] + 1
        await run(p, cfg(), step=0)
        assert p.refresh[KEY]['failures'] == 0
    finally:
        await p.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('expiry,cap', [(EXPIRY, 5400), ('2026-10-30', 345600)])
async def test_7v_max_age_drop_is_warming_class_one_with_warning(caplog, expiry, cap):
    caplog.set_level(logging.INFO, logger='app.futu')
    p, ctx, mono, wall = make(base=datetime(2026, 10, 19, 14, 0, tzinfo=UTC) if cap > 5400 else FRI_11)
    try:
        c = cfg(expiration=expiry)
        await warmed(p, c)
        ctx.chain_error = '内部错误'
        mono.t += cap - 20
        snaps, _ = await run(p, c, step=0)
        assert 'SPY' in snaps
        mono.t += 30
        if cap > 5400:
            wall.jump(0)
        snaps, errors = await run(p, c, step=0)
        assert 'SPY' not in snaps
        e = errors['SPY']
        assert e.code == 'CHAIN_WARMING' and e.chain['droppedReason'] == 'MAX_AGE'
        assert 'dropped at max age' in caplog.text
        assert any(r.levelno == logging.WARNING and 'max age' in r.getMessage() for r in caplog.records)
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_7vi_swr_off_warms_at_ttl_without_refresh_ahead():
    p, ctx, mono, wall = make(settings=FutuSettings(swr=0))
    try:
        await warmed(p, cfg())
        before = ctx.count('get_option_chain')
        mono.t = p.chains[KEY]['expires'] - 5
        snaps, _ = await run(p, cfg(), step=0)
        assert 'SPY' in snaps and ctx.count('get_option_chain') == before   # no refresh-ahead
        mono.t += 10
        snaps, errors = await run(p, cfg(), step=0)
        assert errors['SPY'].code == 'CHAIN_WARMING' and 'droppedReason' not in errors['SPY'].chain
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_7viii_stale_same_day_not_refreshed_after_window():
    """After close + 15 min a stale X == D chain is still served (until MAX_AGE) but never refreshed."""
    p, ctx, mono, wall = make(base=datetime(2026, 10, 23, 20, 10, tzinfo=UTC))  # 16:10 NY
    try:
        await warmed(p, cfg())
        wall.jump(600)
        before = ctx.count('get_option_chain')
        mono.t = p.chains[KEY]['expires'] + 1
        snaps, _ = await run(p, cfg(), n=3)
        assert 'SPY' in snaps and ctx.count('get_option_chain') == before
    finally:
        await p.close()


# --- Test 11d: pre-warm report and offline log rhythm ------------------------------------
@pytest.mark.asyncio
async def test_11d_prewarm_report(caplog):
    caplog.set_level(logging.INFO, logger='app.futu')
    p, ctx, mono, wall = make(base=datetime(2026, 10, 23, 13, 0, tzinfo=UTC),   # 09:00 NY
                              settings=FutuSettings(warm_per_cycle=1))
    try:
        cs = {s: configs([s])[s] for s in ('SPY', 'QQQ')}
        await run(p, cs, step=10)
        wall.jump(25 * 60)
        await run(p, cs, n=3)
        assert 'futu prewarm complete 2/2' in caplog.text
        caplog.clear()
        await run(p, cs, n=3)
        assert 'prewarm' not in caplog.text                         # once per day
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_11d_prewarm_incomplete_warns_with_cold_symbols(caplog):
    caplog.set_level(logging.INFO, logger='app.futu')
    p, ctx, mono, wall = make(base=datetime(2026, 10, 23, 13, 25, tzinfo=UTC))
    try:
        ctx.chain_error = '内部错误'
        await run(p, {s: configs([s])[s] for s in ('SPY', 'QQQ')})
        assert 'futu prewarm incomplete 0/2, cold: US.QQQ,US.SPY' in caplog.text
    finally:
        await p.close()


class Offline(Flaky):
    """OpenD unreachable: connect() fails with OPEND_OFFLINE inside permissions (simulated at get_global_state)."""
    online = False
    def get_global_state(self):
        self._do('get_global_state')
        if not self.online:
            raise ProviderError('OPEND_OFFLINE')
        return super().get_global_state()


@pytest.mark.asyncio
async def test_11d_offline_tried_every_batch_warned_once_per_minute(caplog):
    caplog.set_level(logging.INFO, logger='app.futu')
    p, ctx, mono, wall = make(ctx_cls=Offline)
    try:
        for _ in range(12):
            with pytest.raises(ProviderError) as e:
                await p.collect_batch(cfg())
            assert e.value.code == 'OPEND_OFFLINE'
            mono.t += 10
        assert ctx.count('get_global_state') == 12
        warns = [r for r in caplog.records if 'offline' in r.getMessage() and r.levelno == logging.WARNING]
        assert len(warns) == 2                                        # t=0 and t=60 of 0..110
        ctx.online = True
        await run(p, cfg())
        assert 'futu OpenD reachable again' in caplog.text
        assert 'Connection lost' not in caplog.text
    finally:
        await p.close()


# --- Test 14: chain metadata lands in provider_status and the aggregate result -------------
@pytest.mark.asyncio
async def test_14_stale_meta_in_status_and_result_absent_when_fresh():
    from test_worker_futu_timeout import last_status, tick
    w = Worker(); p, ctx, mono, wall = make(); w.providers['futu'] = p
    with Session() as db:
        for m in db.scalars(select(Monitor).where(Monitor.active == True)):
            m.active = False
        db.commit()
        mid = create_monitor(db, cfg()['SPY']).id
    try:
        for _ in range(4):
            await tick(w); mono.t += 10
        # the engine status is a session verdict (fake OpenD market state); what matters is a saved snapshot
        warm = ('CHAIN_WARMING', 'CHAIN_UNAVAILABLE')
        assert last_status(mid)['status'] not in warm and 'chain' not in last_status(mid)
        mono.t = p.chains[KEY]['expires'] + 1
        ctx.chain_error = '内部错误'
        await tick(w)
        st = last_status(mid)
        assert st['status'] not in warm and st['chain']['stale'] is True and st['chain']['refreshFailures'] == 1  # this batch's failed refresh
        with Session() as db:
            res = db.scalars(select(AggregateResult).where(AggregateResult.monitor_id == mid).order_by(AggregateResult.id.desc())).first()
        assert res.payload['chain']['stale'] is True
    finally:
        await w.stop()
        with Session() as db:
            db.get(Monitor, mid).active = False; db.commit()
