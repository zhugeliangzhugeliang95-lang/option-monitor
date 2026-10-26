"""Additive freshness fields: providers/status cycleSeconds, results/latest staleAfterSeconds."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import models
from app.api import app
from app.worker import Worker
from app.providers import MockProvider
from app.models import Config
from app.service import create_monitor
from app.db import Session, Monitor, RunConfig, WorkerState

client = TestClient(app)


def fresh_monitors(ages):
    with Session() as db:
        for m in db.scalars(select(Monitor).where(Monitor.active == True)): m.active = False
        ws = db.get(WorkerState, 'singleton')
        if ws: db.delete(ws)
        db.commit()
        ids = [create_monitor(db, Config(provider='mock', dataMode='MOCK', underlyingId='SPY', nBelow=1, nAbove=1, timeRule='days365',
                                         remainingDays=2, maxAgeSeconds=a)).id for a in ages]
    return ids


async def collect_all(ids):
    w = Worker(); w.providers['mock'] = MockProvider()
    for mid in ids:
        with Session() as db: rc = db.get(RunConfig, db.get(Monitor, mid).config_id)
        u = (await w.providers['mock'].get_underlying_snapshots(['SPY']))['SPY']
        await w.collect(mid, rc, underlying=u)


@pytest.fixture
def two():
    ids = fresh_monitors([5, 30]); asyncio.run(collect_all(ids)); yield ids
    with Session() as db:
        for mid in ids: db.get(Monitor, mid).active = False
        db.commit()


def test_cycle_seconds_default_without_heartbeat(two):
    r = client.get('/api/providers/status').json()
    assert r['worker'] is None
    assert r['cycleSeconds'] == 10 and type(r['cycleSeconds']) is int


@pytest.mark.parametrize('idx,expected', [(0, 20), (1, 30)])
def test_stale_after_seconds(two, idx, expected):
    r = client.get('/api/results/latest').json()[two[idx]]
    assert r['staleAfterSeconds'] == expected and type(r['staleAfterSeconds']) is int


def test_fields_follow_cycle(two, monkeypatch):
    monkeypatch.setattr(models, 'CYCLE_SECONDS', 20)
    assert client.get('/api/providers/status').json()['cycleSeconds'] == 20
    latest = client.get('/api/results/latest').json()
    assert [latest[m]['staleAfterSeconds'] for m in two] == [40, 40]


def test_heartbeat_target_same_source(two, monkeypatch):
    monkeypatch.setattr(models, 'CYCLE_SECONDS', 15)
    w = Worker()
    async def beat():
        t = asyncio.create_task(w.heartbeat()); await asyncio.sleep(0.05); w.running = False; t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    asyncio.run(beat())
    r = client.get('/api/providers/status').json()
    assert r['worker']['targetSeconds'] == r['cycleSeconds'] == 15


def test_summary_unchanged(two):
    r = client.get('/api/results/latest', params={'summary': 'true'}).json()[two[0]]
    assert 'staleAfterSeconds' not in r


@pytest.mark.parametrize('age,cycle,expected', [(5, 10, 20), (30, 10, 30), (20.5, 10, 21), (5, 3, 6)])
def test_staleness_seconds(age, cycle, expected):
    assert models.staleness_seconds(age, cycle) == expected
