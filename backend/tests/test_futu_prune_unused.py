"""PR-C (#25): at the NY day roll, chains/warm/refresh/dropped keys that no batch config used on either
of the last two NY days are pruned (batch start only). A day with no batch past _permissions()
(OpenD offline) is not counted. No live OpenD: fakes from test_futu_swr / test_futu_cache."""
import logging
from datetime import datetime, timezone
import pytest
from app.futu import WarmState
from app.providers import ProviderError
from test_futu_swr import make, cfg, run, warmed, LIMIT

UTC = timezone.utc
THU = datetime(2026, 10, 22, 19, 50, tzinfo=UTC)   # Thu 15:50 New York
TO_FRI = 17 * 3600 + 20 * 60                        # -> Fri 09:10 New York
DAY = 24 * 3600
FAR = ('US.SPY', '2026-10-30')   # far-dated relative to Thu (the fake lists only this future expiry)
NEAR = ('US.QQQ', '2026-10-30')


def stores(p):
    return (p.chains, p.warm, p.refresh, p.dropped)


async def setup():
    p, ctx, mono, wall = make(base=THU)
    far, near = cfg(expiration=FAR[1]), cfg('QQQ', expiration=NEAR[1])
    await warmed(p, {**far, **near})
    assert FAR in p.chains and NEAR in p.chains
    return p, ctx, mono, wall, far, near


@pytest.mark.asyncio
async def test_c_key_unused_two_days_pruned_used_key_kept_and_served_stale(caplog):
    """(c)+(d): deleted far-dated monitor's key goes only after two unused NY days; a used key stays SWR."""
    p, ctx, mono, wall, far, near = await setup()
    try:
        wall.jump(TO_FRI); mono.t += LIMIT
        snaps, errors = await run(p, near)        # Fri roll: SPY used Thu -> kept
        assert FAR in p.chains and 'QQQ' in snaps and not errors and p.chain_meta['QQQ']['stale'] is True
        p.warm[FAR] = WarmState(wall(), mono.t); p.refresh[FAR] = {'failures': 1, 'next': 0.0}; p.dropped[FAR] = 'MAX_AGE'
        wall.jump(DAY); await run(p, near)        # Sat roll: unused Fri only, used Thu -> kept
        assert FAR in p.chains
        caplog.set_level(logging.INFO, logger='app.futu')
        wall.jump(DAY); await run(p, near)        # Sun roll: unused Fri and Sat -> pruned everywhere
        assert all(FAR not in s for s in stores(p)) and NEAR in p.chains
        msg = [r.getMessage() for r in caplog.records if 'day roll' in r.getMessage()][-1]
        assert 'swept 1 chains (0 expired, 1 unused)' in msg and '3 unused warm/refresh/dropped entries' in msg
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_a_opend_offline_all_friday_keeps_keys_after_saturday_roll(monkeypatch):
    """(a): every Friday batch fails in _permissions (OPEND_OFFLINE): Friday does not count."""
    p, ctx, mono, wall, far, near = await setup()
    try:
        def offline(*a, **k): raise ProviderError('OPEND_OFFLINE')
        monkeypatch.setattr(p, 'permissions', offline)
        wall.jump(TO_FRI)
        for _ in range(5):
            with pytest.raises(ProviderError):
                await run(p, {**far, **near})
        monkeypatch.undo()
        wall.jump(DAY); await run(p, near)        # Sat roll after an all-offline Friday
        assert FAR in p.chains and NEAR in p.chains
        wall.jump(DAY); await run(p, near)        # Sun: Thu usage merged, not lost; Sat counted, still in 2-day window
        assert FAR in p.chains
        wall.jump(DAY); await run(p, near)        # Mon: FAR unused Sat and Sun (both counted) -> pruned
        assert FAR not in p.chains and NEAR in p.chains
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_b_monitor_in_backoff_one_whole_day_not_pruned():
    """(b): SPY absent from every Friday batch (backoff), back on Saturday: never pruned."""
    p, ctx, mono, wall, far, near = await setup()
    try:
        wall.jump(TO_FRI); await run(p, near, n=3)
        wall.jump(DAY); mono.t += LIMIT
        snaps, errors = await run(p, {**far, **near})   # Sat roll, then SPY is back
        assert FAR in p.chains and 'SPY' in snaps
        wall.jump(DAY); await run(p, near)        # Sun: used Sat -> kept
        assert FAR in p.chains
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_no_prune_mid_day_or_on_first_roll_after_start():
    p, ctx, mono, wall, far, near = await setup()
    try:
        await run(p, near, n=5)                   # same day, far key unused: never pruned mid-day
        assert FAR in p.chains
        mono.t += LIMIT                           # within MAX_AGE: SPY still served stale when it returns
        snaps, errors = await run(p, far)
        assert 'SPY' in snaps and p.chain_meta['SPY']['stale'] is True
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_recreated_after_prune_rewarms_normally():
    p, ctx, mono, wall, far, near = await setup()
    try:
        wall.jump(TO_FRI); await run(p, near)
        wall.jump(DAY); await run(p, near)
        wall.jump(DAY); await run(p, near)
        assert FAR not in p.chains
        snaps = await warmed(p, far)              # monitor created again for the same key
        assert 'SPY' in snaps and FAR in p.chains and p.chains[FAR]['fetchedDay'] == '2026-10-25'
    finally:
        await p.close()
