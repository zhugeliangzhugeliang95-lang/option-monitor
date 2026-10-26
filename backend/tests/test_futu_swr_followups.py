"""Follow-ups from 阿衡's #18 review: calendar-error WARNING (rate-limited per key), refreshFailures
current in the failing batch, a market-holiday test, and the one-batch-at-a-time guard for chain_meta.
No live OpenD: fakes from test_futu_swr / test_futu_cache."""
import logging
from datetime import datetime, timezone
import pytest
from app import futu as futu_mod
from app.futu import CALENDAR_LOG_EVERY
from test_futu_swr import make, cfg, run, warmed, KEY

UTC = timezone.utc


@pytest.mark.asyncio
async def test_calendar_error_warns_once_per_key_without_message(caplog, monkeypatch):
    p, ctx, mono, wall = make()
    def boom(name):
        raise ValueError('secret-ish detail token=abc')
    monkeypatch.setattr(futu_mod.xcals, 'get_calendar', boom)
    try:
        c = cfg()['SPY']
        caplog.set_level(logging.WARNING, logger='app.futu')
        assert p._window(c, wall()) is None
        p.windows.clear()                       # cache cleared: same key retried, still rate-limited
        assert p._window(c, wall()) is None
        warns = [r for r in caplog.records if 'calendar error' in r.getMessage()]
        assert len(warns) == 1 and warns[0].levelno == logging.WARNING
        msg = warns[0].getMessage()
        assert 'ValueError' in msg and 'XNYS' in msg and '2026-10-23' in msg and 'token' not in msg
        mono.t += CALENDAR_LOG_EVERY             # past the interval: one more WARNING for that key
        p.windows.clear()
        p._window(c, wall())
        assert len([r for r in caplog.records if 'calendar error' in r.getMessage()]) == 2
        # a calendar error means no window: a same-day chain is offHours, never fetched
        snaps, errors = await run(p, cfg())
        assert errors['SPY'].chain['reason'] == 'offHours' and ctx.count('get_option_chain') == 0
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_refresh_failures_current_in_the_failing_batch():
    p, ctx, mono, wall = make()
    try:
        await warmed(p, cfg())
        ctx.chain_error = '内部错误'
        mono.t = p.chains[KEY]['expires'] + 1
        snaps, errors = await run(p, cfg(), step=0)
        assert 'SPY' in snaps and p.refresh[KEY]['failures'] == 1
        assert p.chain_meta['SPY']['refreshFailures'] == 1      # was 0 (previous value) before
        mono.t = p.refresh[KEY]['next']
        await run(p, cfg(), step=0)
        assert p.chain_meta['SPY']['refreshFailures'] == 2
        ctx.chain_error = None
        mono.t = p.refresh[KEY]['next']
        snaps, errors = await run(p, cfg(), step=0)
        m = p.chain_meta['SPY']                                  # served stale, refresh succeeded now
        assert m['stale'] is True and m['refreshFailures'] == 0 and KEY not in p.refresh
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_holiday_is_off_hours():
    """Thanksgiving Thu 2026-11-26: NYSE closed, no window; a same-day expiry is offHours, no chain call."""
    p, ctx, mono, wall = make(base=datetime(2026, 11, 26, 16, 0, tzinfo=UTC))  # 11:00 EST
    try:
        c = cfg(expiration='2026-11-26')
        assert p._window(c['SPY'], wall()) is None and not p.in_window(c['SPY'], wall())
        snaps, errors = await run(p, c)
        assert errors['SPY'].code == 'CHAIN_WARMING' and errors['SPY'].chain['reason'] == 'offHours'
        assert ctx.count('get_option_expiration_date') == 1      # E still fetched off-hours (b6 i)
        # the holiday is not a listed expiry: afterwards an empty catalog (engine NO_SAME_DAY_EXPIRY)
        await run(p, c, n=3)
        assert ctx.count('get_option_chain') == 0
        # the next session (early close Fri 11-27) still has a window
        assert p._window(c['SPY'], datetime(2026, 11, 27, 16, 0, tzinfo=UTC)) is not None
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_concurrent_batch_is_refused_and_flag_resets():
    p, ctx, mono, wall = make()
    try:
        p.batching = True
        with pytest.raises(RuntimeError, match='one batch at a time'):
            p.batch_snapshots(cfg())
        p.batching = False
        await run(p, cfg())
        assert p.batching is False
    finally:
        await p.close()
