"""MOCK quote event times use the real clock; the 10-second slot only seeds prices.

Regression: quotes stamped at the slot floor looked up to 10 s old against the 5 s default
maxAgeSeconds, so about half of MOCK cycles became INVALID_SELECTION_ANCHOR / STALE_QUOTE.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from app import worker as worker_mod
from app.worker import Worker
from app.providers import MockProvider
from app.models import Config
from app.service import create_monitor
from app.db import Session, Monitor, RunConfig, AggregateResult

NY = ZoneInfo('America/New_York')
SLOT = datetime(2026, 10, 26, 7, 40, 0, tzinfo=NY)  # NY pre-market, slot boundary


async def run_spy(monkeypatch, now):
    clock = lambda: now
    monkeypatch.setattr(worker_mod, 'utcnow', clock)
    w = Worker(); p = MockProvider(clock=clock); w.providers['mock'] = p
    with Session() as db:
        for m in db.scalars(select(Monitor).where(Monitor.active == True)): m.active = False
        db.commit()
        mid = create_monitor(db, Config(provider='mock', dataMode='MOCK', underlyingId='SPY', nBelow=2, nAbove=2,
                                        timeRule='days365', remainingDays=2)).id
        rc = db.get(RunConfig, db.get(Monitor, mid).config_id)
    u = (await p.get_underlying_snapshots(['SPY']))['SPY']
    await w.collect(mid, rc, underlying=u)
    with Session() as db:
        row = db.scalars(select(AggregateResult).where(AggregateResult.monitor_id == mid).order_by(AggregateResult.id.desc())).first()
        m = db.get(Monitor, mid); m.active = False; db.commit()
    assert row is not None
    return row.payload


def reasons(r):
    return {x for mode in r['modes'].values() for row in mode['rows'] for x in row['reasons']}


@pytest.mark.asyncio
async def test_plus8s_in_slot_is_not_stale_anchor(monkeypatch):
    r = await run_spy(monkeypatch, SLOT + timedelta(seconds=8))
    assert r['status'] != 'INVALID_SELECTION_ANCHOR'
    assert r['anchorStrictEligible'] is True
    assert 'STALE_QUOTE' not in reasons(r)


@pytest.mark.asyncio
@pytest.mark.parametrize('offset', [0, 4.9, 5.5, 8])
async def test_every_offset_in_slot_ok(monkeypatch, offset):
    # Different minutes per case so snapshots never collide; offset is within the 10 s slot.
    r = await run_spy(monkeypatch, SLOT + timedelta(minutes=1 + int(offset * 10), seconds=offset))
    assert r['status'] == 'OK', (r['status'], reasons(r))
    assert r['anchorStrictEligible'] is True and not reasons(r)


@pytest.mark.asyncio
async def test_prices_fixed_within_slot_times_are_real():
    t1 = SLOT + timedelta(seconds=1); t2 = SLOT + timedelta(seconds=8.5)
    q1 = (await MockProvider(clock=lambda: t1).get_underlying_snapshots(['SPY']))['SPY']
    q2 = (await MockProvider(clock=lambda: t2).get_underlying_snapshots(['SPY']))['SPY']
    assert (q1.bid, q1.ask, q1.last) == (q2.bid, q2.ask, q2.last)
    assert q1.sequence == q2.sequence
    assert q1.lastAt == q1.bidAt == q1.askAt == t1 and q2.lastAt == t2
    q3 = (await MockProvider(clock=lambda: SLOT + timedelta(seconds=10)).get_underlying_snapshots(['SPY']))['SPY']
    assert q3.last != q1.last  # next slot reseeds
