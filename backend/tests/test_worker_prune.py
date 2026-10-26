"""PR-B: worker per-monitor dicts are pruned for deleted monitors, never for in-flight ones. No live OpenD (fake ctx)."""
import asyncio
from sqlalchemy import select
from app.worker import Worker
from app.db import Session, Monitor
from test_worker_futu_timeout import make_worker, tick, futu_cfg  # noqa: F401 (fixture)
from app.service import create_monitor

DICTS = Worker.PER_MONITOR


def deactivate(mid):
    with Session() as db:
        db.get(Monitor, mid).active = False; db.commit()


def sizes(w):
    return {n: len(getattr(w, n)) for n in DICTS}


def test_dicts_stop_growing_after_create_delete_churn(make_worker):
    async def go():
        w, ids, ctx, mono = make_worker(n_futu=0, n_mock=1)
        keep = ids['mock'][0]
        await tick(w)
        for _ in range(5):
            mids = []
            with Session() as db:
                for _ in range(4):
                    from app.models import Config
                    mids.append(create_monitor(db, Config(provider='mock', dataMode='MOCK', nBelow=1, timeRule='days365', remainingDays=2)).id)
            await tick(w)
            assert all(m in w.locks and m in w.next_due and m in w.last_status for m in mids)
            for m in mids: deactivate(m)
        await tick(w)
        for n in DICTS:
            assert set(getattr(w, n)) <= {keep}, n
        assert keep in w.locks and keep in w.next_due and keep in w.last_status
    asyncio.run(go())


def test_held_lock_is_not_pruned():
    w = Worker()
    async def go():
        lock = asyncio.Lock(); await lock.acquire()
        w.locks['gone'] = lock; w.next_due['gone'] = 1; w.failures['gone'] = 2; w.last_status['gone'] = 'OK'
        w.locks['idle'] = asyncio.Lock(); w.next_due['idle'] = 1
        w.prune(set())
        assert w.locks['gone'] is lock and w.next_due['gone'] == 1 and w.failures['gone'] == 2 and w.last_status['gone'] == 'OK'
        assert 'idle' not in w.locks and 'idle' not in w.next_due
        lock.release(); w.prune(set())
        assert all('gone' not in getattr(w, n) for n in DICTS)
    asyncio.run(go())


def test_futu_inflight_is_not_pruned():
    w = Worker()
    w.futu_inflight.add('batch'); w.next_due['batch'] = 5; w.failures['batch'] = 1; w.last_status['batch'] = 'CHAIN_WARMING'
    w.prune(set())
    assert w.next_due['batch'] == 5 and w.failures['batch'] == 1 and w.last_status['batch'] == 'CHAIN_WARMING'
    w.futu_inflight.clear(); w.prune(set())
    assert all('batch' not in getattr(w, n) for n in DICTS)


def test_inflight_futu_batch_survives_cycle_after_delete(make_worker, monkeypatch):
    async def go():
        w, ids, ctx, mono = make_worker(n_futu=1)
        mid = ids['futu'][0]
        # Hold the fake batch explicitly; a fast executor may otherwise finish before cycle returns.
        release = asyncio.Event()
        original = w.providers['futu'].collect_batch
        async def held(configs):
            await release.wait()
            return await original(configs)
        monkeypatch.setattr(w.providers['futu'], 'collect_batch', held)
        w.next_due.clear()
        await w.cycle()
        assert mid in w.futu_inflight
        deactivate(mid)
        await w.cycle()  # batch still running: state kept
        assert mid in w.futu_inflight or mid in w.last_status
        release.set()
        await asyncio.wait({w.futu_task})
        assert mid in w.last_status
        await w.cycle()  # batch finished, monitor inactive: pruned
        assert all(mid not in getattr(w, n) for n in DICTS)
    asyncio.run(go())


def test_recreate_same_and_new_id_works(make_worker):
    async def go():
        w, ids, ctx, mono = make_worker(n_futu=0, n_mock=1)
        mid = ids['mock'][0]
        await tick(w); deactivate(mid); await tick(w)
        assert mid not in w.next_due
        with Session() as db:
            db.get(Monitor, mid).active = True; db.commit()  # same id back
        await tick(w)
        assert w.last_status.get(mid) is not None and mid in w.next_due and mid in w.locks
        from app.models import Config
        with Session() as db:
            new = create_monitor(db, Config(provider='mock', dataMode='MOCK', nBelow=1, timeRule='days365', remainingDays=2)).id
        await tick(w)
        assert w.last_status.get(new) is not None and new in w.next_due
    asyncio.run(go())
