"""PR-C: at the NY day roll, chains/warm/refresh/dropped keys no batch config used during the previous
day are pruned (batch start only). No live OpenD: fakes from test_futu_swr / test_futu_cache."""
from datetime import datetime, timezone
import pytest
from app.futu import WarmState
from test_futu_swr import make, cfg, run, warmed, LIMIT

UTC = timezone.utc
THU = datetime(2026, 10, 22, 19, 50, tzinfo=UTC)   # Thu 15:50 New York
TO_FRI = 17 * 3600 + 20 * 60                        # -> Fri 09:10 New York
FAR = ('US.SPY', '2026-10-30')   # far-dated relative to Thu (fake lists only this future expiry)
NEAR = ('US.QQQ', '2026-10-30')


def stores(p):
    return (p.chains, p.warm, p.refresh, p.dropped)


@pytest.mark.asyncio
async def test_deleted_far_dated_key_pruned_at_roll_used_key_kept_and_served_stale():
    p, ctx, mono, wall = make(base=THU)
    try:
        far, near = cfg(expiration=FAR[1]), cfg('QQQ', expiration=NEAR[1])
        await warmed(p, {**far, **near})
        assert FAR in p.chains and NEAR in p.chains
        wall.jump(TO_FRI); mono.t += LIMIT
        snaps, errors = await run(p, near)        # first Friday batch: roll at batch start; SPY was used on Thursday
        assert FAR in p.chains                    # (SPY monitor deleted after Thursday: no Friday use from here on)
        assert NEAR in p.chains and 'QQQ' in snaps and not errors   # still used: kept, SWR-served stale
        assert p.chain_meta['QQQ']['stale'] is True
        p.warm[FAR] = WarmState(wall(), mono.t); p.refresh[FAR] = {'failures': 1, 'next': 0.0}; p.dropped[FAR] = 'MAX_AGE'
        await run(p, near, n=3)
        wall.jump(24 * 3600)                      # Saturday roll: FAR unused for the whole Friday
        await run(p, near)
        assert all(FAR not in s for s in stores(p))
        assert NEAR in p.chains
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_key_used_in_previous_day_survives_even_if_absent_from_first_batch():
    p, ctx, mono, wall = make(base=THU)
    try:
        far, near = cfg(expiration=FAR[1]), cfg('QQQ', expiration=NEAR[1])
        await warmed(p, {**far, **near})
        wall.jump(TO_FRI)
        await run(p, near)                        # SPY in backoff, not in this batch
        assert FAR in p.chains                    # used on Thursday: kept
        wall.jump(24 * 3600)                      # Saturday roll: SPY unused all Friday -> pruned
        await run(p, near)
        assert FAR not in p.chains and NEAR in p.chains
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_no_prune_mid_day_or_on_first_batch_after_start():
    p, ctx, mono, wall = make(base=THU)
    try:
        far, near = cfg(expiration=FAR[1]), cfg('QQQ', expiration=NEAR[1])
        await warmed(p, {**far, **near})
        await run(p, near, n=5)                   # same day, far key unused: never pruned mid-day
        assert FAR in p.chains
        mono.t += LIMIT                           # within MAX_AGE: SPY still served stale when it returns
        snaps, errors = await run(p, far)
        assert 'SPY' in snaps and p.chain_meta['SPY']['stale'] is True
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_recreated_after_prune_rewarms_normally():
    p, ctx, mono, wall = make(base=THU)
    try:
        far, near = cfg(expiration=FAR[1]), cfg('QQQ', expiration=NEAR[1])
        await warmed(p, {**far, **near})
        wall.jump(TO_FRI); await run(p, near)
        wall.jump(24 * 3600); await run(p, near)
        assert FAR not in p.chains
        snaps = await warmed(p, far)              # monitor created again for the same key
        assert 'SPY' in snaps and FAR in p.chains and p.chains[FAR]['fetchedDay'] == '2026-10-24'
    finally:
        await p.close()
