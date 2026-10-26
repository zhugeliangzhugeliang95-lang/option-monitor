"""Plan item 1 (PR 1): cooperative deadline, hot-first, warm queue, stagger, refresh-ahead, E.2.

Everything runs on an injected monotonic clock: the fake OpenD advances it by per-method
'sleeps' instead of sleeping, so budgets and windows are exact and tests are fast.
"""
import logging
import pandas as pd
import pytest
from datetime import datetime, timezone
from app.futu import FutuProvider, DeadlineReached, stagger, CHAIN_TTL, STAGGER, REFRESH_LEAD
from app.futu_config import FutuSettings, load, HEADROOM_DEFAULTS
from app.instruments import FUTU_SYMBOLS
from app.models import Config, Snapshot
from app.providers import ProviderError
from app.service import fingerprint

CLOCK23 = lambda: datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)  # Fri 11:00 New York
EXPIRY = '2026-10-23'
SYMBOLS = FUTU_SYMBOLS[:20]
WORST = {m: v for m, (_, v) in HEADROOM_DEFAULTS.items() if m != '*'}  # sleep exactly the headroom


class Mono:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class Account:
    """Account-level per-method counter shared by every context: documented 10 per 30 s."""
    def __init__(self, limit=10, window=30.0):
        self.limit, self.window, self.hits, self.peak, self.rejected = limit, window, {}, {}, []

    def hit(self, method, t):
        q = [x for x in self.hits.get(method, []) if x > t - self.window]
        if len(q) >= self.limit:
            self.rejected.append((method, t)); self.hits[method] = q
            return False
        q.append(t); self.hits[method] = q
        self.peak[method] = max(self.peak.get(method, 0), len(q))
        return True


class Fake:
    def __init__(self, mono, sleeps=None, account=None, fail_chain=()):
        self.mono, self.sleeps, self.account = mono, dict(sleeps or {}), account
        self.fail_chain = set(fail_chain)
        self.log, self.active, self.overlap, self.closed = [], 0, False, False

    def _do(self, method):
        if self.active: self.overlap = True
        self.active += 1
        self.log.append((method, self.mono.t))
        self.mono.t += self.sleeps.get(method, 0.0)
        self.active -= 1
        return self.account is None or self.account.hit(method, self.mono.t)

    def count(self, method, since=None):
        return sum(1 for m, t in self.log if m == method and (since is None or t >= since))

    def get_global_state(self):
        self._do('get_global_state'); return 0, {'qot_logined': True, 'market_us': 'MORNING'}

    def get_user_info(self):
        self._do('get_user_info'); return 0, {'us_qot_right': 'LV3', 'us_option_qot_right': 'LV1', 'user_id': 'PRIVATE'}

    def get_option_expiration_date(self, code):
        if not self._do('get_option_expiration_date'): return -1, '请求过于频繁'
        return 0, pd.DataFrame([{'strike_time': d} for d in ('2026-10-30', EXPIRY)])

    def get_option_chain(self, code, start, end):
        ok = self._do('get_option_chain')
        if not ok or code.split('.')[1] in self.fail_chain: return -1, '请求过于频繁'
        return 0, pd.DataFrame([dict(code=f'{code}:{r}', stock_owner=code, strike_time=start, strike_price=100,
                                     lot_size=100, option_type=t, option_standard_type='STANDARD')
                                for r, t in (('C', 'CALL'), ('P', 'PUT'))])

    def get_market_state(self, codes):
        self._do('get_market_state'); return 0, pd.DataFrame([{'code': c, 'market_state': 'MORNING'} for c in codes])

    def get_market_snapshot(self, codes):
        self._do('get_market_snapshot')
        rows = []
        for code in codes:
            option = ':' in code
            rows.append(dict(code=code, last_price=2 if option else 101, bid_price=1.9 if option else 100.9,
                             ask_price=2.1 if option else 101.1, update_time='2026-10-23 11:00:00',
                             option_valid=option, stock_owner=code.split(':')[0], strike_time=EXPIRY,
                             option_strike_price=100, option_contract_size=100,
                             option_type='CALL' if code.endswith(':C') else 'PUT'))
        return 0, pd.DataFrame(rows)

    def close(self): self.closed = True


def configs(symbols=SYMBOLS):
    return {s: Config(provider='futu', dataMode='API', symbol=s, underlyingId=s, expiryMode='manual',
                      expiration=EXPIRY, nBelow=1, timeRule='days365', remainingDays=1) for s in symbols}


def provider(sleeps=None, settings=None, **kw):
    mono = Mono()
    ctx = Fake(mono, sleeps, **kw)
    return FutuProvider(ctx, clock=CLOCK23, mono=mono, settings=settings or FutuSettings()), ctx, mono


async def batches(p, cfgs, n, period=10.0, on_batch=None):
    """Run n batches starting every `period` fake seconds; returns per-batch (start, duration, snaps, errors)."""
    out, start = [], p.mono.t
    for i in range(n):
        p.mono.t = max(p.mono.t, start + i * period)
        began = p.mono.t
        snaps, errors = await p.collect_batch(cfgs(i) if callable(cfgs) else cfgs)
        out.append((began, p.mono.t - began, snaps, errors))
        if on_batch: on_batch(i)
    return out


def max_in_window(times, window=30.1):
    return max((sum(1 for u in times if t <= u < t + window) for t in times), default=0)


# --- Test 1: limiter arithmetic -------------------------------------------------------
@pytest.mark.asyncio
async def test_1_twenty_cold_symbols_one_chain_per_batch_worst_case_headroom():
    p, ctx, mono = provider(WORST)
    try:
        runs = await batches(p, configs(), 22)
        chains = [t for m, t in ctx.log if m == 'get_option_chain']
        assert len(chains) == 20
        per_batch = [ctx.count('get_option_chain', b) - ctx.count('get_option_chain', b + d + 1e-9) for b, d, _, _ in runs]
        assert per_batch[:20] == [1] * 20 and per_batch[20:] == [0, 0]
        assert max_in_window(chains) <= 4
        assert max_in_window([t for m, t in ctx.log if m == 'get_market_state']) <= 4
        # symbol i (alphabetical: same waiting time) is fetched in batch i and first quoted in batch i+1
        order = sorted(SYMBOLS)
        for i, (_, _, snaps, errors) in enumerate(runs[:21]):
            assert set(snaps) == set(order[:i])
            assert all(e.code == 'CHAIN_WARMING' for e in errors.values())
            assert not any((e.chain or {}).get('lastError') for e in errors.values())
        assert set(runs[20][2]) == set(SYMBOLS)            # all 20 by batch 21 (t = 200 s)
        assert max(d for _, d, _, _ in runs) <= 6.5          # worst-case durations from (a)
        assert not ctx.overlap
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_1_cap_three_bypassing_validation_is_stopped_by_window_precheck():
    p, ctx, mono = provider(settings=FutuSettings(warm_per_cycle=3))
    try:
        runs = await batches(p, configs(), 10)
        chains = [t for m, t in ctx.log if m == 'get_option_chain']
        assert max_in_window(chains) <= 8
        for _, _, _, errors in runs:
            assert not any((getattr(e, 'chain', None) or {}).get('lastError') for e in errors.values())
            assert not any(e.code == 'RATE_LIMITED' for e in errors.values())
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_1_cap_two_with_prefetched_expirations_stays_within_eight():
    p, ctx, mono = provider(settings=FutuSettings(warm_per_cycle=2))
    try:
        await batches(p, configs(), 12)
        assert max_in_window([t for m, t in ctx.log if m == 'get_option_chain']) <= 8
        assert ctx.count('get_option_expiration_date') == 20   # each symbol's E fetched once (prefetch or warm)
    finally:
        await p.close()


# --- Test 2: hot first ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_2_warm_monitors_quote_every_batch_while_cold_ones_warm():
    p, ctx, mono = provider(WORST)
    try:
        await batches(p, configs(SYMBOLS[:10]), 11)
        runs = await batches(p, configs(SYMBOLS), 10)
        for _, d, snaps, errors in runs:
            assert set(SYMBOLS[:10]) <= set(snaps)
            assert d <= 7.0
    finally:
        await p.close()


# --- Test 4: round-robin fairness -----------------------------------------------------
@pytest.mark.asyncio
async def test_4_oldest_waiting_first_new_symbols_never_jump_ahead():
    p, ctx, mono = provider()
    fetched = []
    original = p.contracts
    def spy(cfg, expiry, **kw):
        fetched.append(cfg.underlyingId); return original(cfg, expiry, **kw)
    p.contracts = spy
    # reverse-alphabetical arrival so the order cannot come from the symbol tie-break
    arrival = list(reversed(SYMBOLS))
    try:
        await batches(p, lambda i: configs(arrival[:min(5 + i, 20)]), 20)
        # the first 5 arrive together (tie -> symbol order); every later one strictly in arrival order
        assert fetched[:5] == sorted(arrival[:5])
        assert fetched[5:] == arrival[5:5 + len(fetched) - 5] and len(fetched) == 20
    finally:
        await p.close()


# --- Test 5: staggered TTL ------------------------------------------------------------
@pytest.mark.asyncio
async def test_5_chain_ttl_staggered_per_symbol():
    p, ctx, mono = provider(settings=FutuSettings(warm_per_cycle=100))
    try:
        await batches(p, configs(), 4, period=40)
        assert len(p.chains) == 20
        ttls = {k[0]: v['expires'] - v['fetched'] for k, v in p.chains.items()}
        assert all(CHAIN_TTL <= t < CHAIN_TTL + STAGGER for t in ttls.values())
        assert ttls == {k: CHAIN_TTL + stagger(k) for k in ttls}       # deterministic
        assert len(set(ttls.values())) > 10                             # actually spread
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_5_refresh_ahead_keeps_quoting_through_ttl_no_warming_gap():
    """PR 1 deviation: refresh-ahead (class 2) instead of SWR; nothing stale is ever served."""
    p, ctx, mono = provider()
    try:
        await batches(p, configs(['SPY']), 3)
        first = p.chains[('US.SPY', EXPIRY)]['fetched']
        runs = await batches(p, configs(['SPY']), int((CHAIN_TTL + STAGGER) / 10) + 5)
        assert all('SPY' in snaps for _, _, snaps, _ in runs)
        chain = p.chains[('US.SPY', EXPIRY)]
        assert chain['fetched'] > first and mono.t < chain['expires']
        refreshed_at = chain['fetched'] - first
        assert CHAIN_TTL + stagger('US.SPY') - REFRESH_LEAD <= refreshed_at <= CHAIN_TTL + stagger('US.SPY') - REFRESH_LEAD + 10
    finally:
        await p.close()


# --- Test 8: per-call headroom -------------------------------------------------------
@pytest.mark.asyncio
async def test_8_headroom_skips_user_info_but_runs_market_state():
    p, ctx, mono = provider()
    try:
        p.deadline = mono.t + 1.5
        with pytest.raises(DeadlineReached) as e:
            p.call('get_user_info')
        assert e.value.method == 'get_user_info' and ctx.count('get_user_info') == 0
        p.call('get_market_state', ['US.SPY'])
        assert ctx.count('get_market_state') == 1
        p.deadline = None
        assert load({'FUTU_HEADROOM_USER_INFO': '1.0'}).room('get_user_info') == 1.0
        p.settings = load({'FUTU_HEADROOM_USER_INFO': '1.0'}); p.deadline = mono.t + 1.5
        p.call('get_user_info'); assert ctx.count('get_user_info') == 1
    finally:
        p.deadline = None; await p.close()


# --- Test 9: slow snapshot ------------------------------------------------------------
@pytest.mark.asyncio
async def test_9_slow_snapshot_returns_by_deadline_with_timeout_operation():
    p, ctx, mono = provider(settings=FutuSettings(warm_per_cycle=100))
    try:
        await batches(p, configs(), 4, period=40)
        # The underlying snapshot is slow (6.5 s); the option snapshot would need 0.75 s more than is left,
        # so it is never started. A call already running is not preempted (batch_timeout covers that).
        ctx.sleeps['get_market_snapshot'] = 6.5
        mono.t += 40; start = mono.t
        snaps, errors = await p.collect_batch(configs())
        assert mono.t - start <= 7.0
        assert not snaps and set(errors) == set(SYMBOLS)
        assert all(e.code == 'TIMEOUT' and e.operation == 'get_market_snapshot' for e in errors.values())
        assert ctx.count('get_market_snapshot', start) == 1 and not ctx.overlap
        assert p.deadline is None
    finally:
        await p.close()


# --- Test 16: warm cache --------------------------------------------------------------
@pytest.mark.asyncio
async def test_16_warm_cache_second_batch_is_one_hot_pass():
    p, ctx, mono = provider({'get_market_state': .05, 'get_market_snapshot': .15, 'get_global_state': .05},
                            settings=FutuSettings(warm_per_cycle=100))
    try:
        await batches(p, configs(), 4, period=40)
        mono.t += 40; start = mono.t
        snaps, errors = await p.collect_batch(configs())
        assert set(snaps) == set(SYMBOLS) and not errors
        assert ctx.count('get_option_chain', start) == 0 and ctx.count('get_option_expiration_date', start) == 0
        assert ctx.count('get_market_state', start) == 1 and ctx.count('get_market_snapshot', start) == 2
        assert mono.t - start == pytest.approx(0.4)
    finally:
        await p.close()


# --- E.2 (test 11b/c provider side) -------------------------------------------------
@pytest.mark.asyncio
async def test_e2_vendor_error_visible_then_unavailable_after_cap_then_recovers(caplog):
    caplog.set_level(logging.INFO, 'app.futu')
    wall = [datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)]
    mono = Mono(); ctx = Fake(mono, fail_chain={'QQQ'})
    p = FutuProvider(ctx, clock=lambda: wall[0], mono=mono, settings=FutuSettings())
    from datetime import timedelta
    try:
        seen, attempts_at = [], []
        for i in range(70):  # 700 s
            snaps, errors = await p.collect_batch(configs(['SPY', 'QQQ', 'IWM']))
            e = errors.get('QQQ'); seen.append((i * 10, e.code, e.chain['state']))
            if i >= 1: assert e.chain['lastError']['code'] == 'RATE_LIMITED' and 'message' not in e.chain['lastError']
            assert e.retry_after == 10
            mono.t += 10; wall[0] += timedelta(seconds=10)
        assert 'SPY' in snaps and 'IWM' in snaps          # other symbols kept warming and quoting
        assert {c for t, c, _ in seen if t <= 600} == {'CHAIN_WARMING'}
        assert seen[-1][1:] == ('CHAIN_UNAVAILABLE', 'unavailable')
        warns = [r for r in caplog.records if r.levelno == logging.WARNING and 'unavailable' in r.getMessage()]
        assert len(warns) == 1
        assert p.warm[('US.QQQ', EXPIRY)].next_try - mono.t <= 300   # per-key backoff capped
        tries = [t for m, t in ctx.log if m == 'get_option_chain']
        ctx.fail_chain.clear()
        for _ in range(40):
            snaps, errors = await p.collect_batch(configs(['SPY', 'QQQ', 'IWM']))
            mono.t += 10; wall[0] += timedelta(seconds=10)
            if 'QQQ' in snaps: break
        assert 'QQQ' in snaps
        assert any(r.levelno == logging.INFO and 'ready after' in r.getMessage() for r in caplog.records)
        assert ('US.QQQ', EXPIRY) not in p.warm
    finally:
        await p.close()


# --- Test 18: account-level concurrency with the API process --------------------------
async def _account_run(external_per_window):
    account = Account()
    p, ctx, mono = provider(account=account)
    worker_chain_errors = []
    def external(i):
        # another process/client: bursts of chain calls once per 30 s window
        if i % 3 == 0:
            for _ in range(external_per_window):
                account.hit('get_option_chain', mono.t)
    try:
        runs = await batches(p, configs(), 22, on_batch=external)
        for _, _, _, errors in runs:
            worker_chain_errors += [e for e in errors.values() if (getattr(e, 'chain', None) or {}).get('lastError')]
    finally:
        await p.close()
    return account, worker_chain_errors


@pytest.mark.asyncio
async def test_18_worker_leaves_account_headroom_for_api_process():
    alone, _ = await _account_run(0)
    worker_peak = alone.peak['get_option_chain']
    assert worker_peak <= 4
    margin = 10 - worker_peak
    # one below the free capacity: no error, one call of margin
    account, errors = await _account_run(margin - 1)
    assert account.peak['get_option_chain'] == 9 and not account.rejected and not errors
    # exactly at capacity: 10 of 10, still no error (the bound is exact)
    account, errors = await _account_run(margin)
    assert account.peak['get_option_chain'] == 10 and not account.rejected
    # one more: the account limit is exceeded somewhere
    account, errors = await _account_run(margin + 1)
    assert account.rejected


# --- Test 19: schema / fingerprint ---------------------------------------------------
@pytest.mark.asyncio
async def test_19_snapshots_validate_and_fingerprint_ignores_chain_age():
    p, ctx, mono = provider(settings=FutuSettings(warm_per_cycle=100))
    try:
        await batches(p, configs(), 4, period=40)
        mono.t += 40
        a, _ = await p.collect_batch(configs())
        mono.t += 600  # chain 600 s older, same quotes
        b, _ = await p.collect_batch(configs())
        for s in list(a.values()) + list(b.values()):
            assert Snapshot.model_validate(s.model_dump(mode='json')) == s
            assert set(s.model_dump(mode='json')) == set(Snapshot.model_fields)
        assert all(fingerprint(a[k]) == fingerprint(b[k]) for k in a)
    finally:
        await p.close()
