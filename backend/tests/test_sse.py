import asyncio
from app import api
from sqlalchemy import func, select
from app.db import Session, ResultOutbox, nowstr

def add(n):
    with Session() as db:
        rows=[ResultOutbox(payload={'type':'test','n':i},created_at=nowstr()) for i in range(n)]; db.add_all(rows); db.commit(); return [r.id for r in rows]

def collect(last, items=(), ping=0.05, limit=None, wait=0.0):
    async def run():
        q=asyncio.Queue(maxsize=100); api.subscribers.add(q)
        for i in items: api.publish(q,i)
        t0=asyncio.get_running_loop().time()
        async def disc(): return asyncio.get_running_loop().time()-t0>wait
        out=[]
        async for chunk in api.sse_events(q,last,disc,ping_seconds=ping):
            out.append(chunk)
            if limit and len(out)>=limit: break
        return out,q
    return asyncio.run(run())

def test_replay_after_last_event_id():
    ids=add(3)
    out,_=collect(ids[0])
    assert out[0].startswith('event: ready')
    assert [c.split('\n')[0] for c in out[1:3]]==[f'id: {ids[1]}',f'id: {ids[2]}']

def test_replay_skips_duplicates_from_live_queue():
    ids=add(2)
    out,_=collect(ids[0],items=[{'id':ids[1],'n':1}],wait=0.01)
    assert sum(c.startswith(f'id: {ids[1]}\n') for c in out)==1

def test_resync_when_id_older_than_outbox():
    add(1)
    with Session() as db: lo,hi=db.execute(select(func.min(ResultOutbox.id),func.max(ResultOutbox.id))).one()
    for last in (lo-2,hi+10):
        out,_=collect(last)
        assert out[1].startswith('event: resync') and not any(c.startswith('id: ') for c in out)

def test_resync_on_overflow():
    out,q=collect(None,items=[{'id':i} for i in range(101)],wait=0.01)
    assert out[1].startswith('event: resync') and not any(c.startswith('id: ') for c in out)

def test_ping():
    out,q=collect(None,ping=0.02,wait=0.05)
    assert any(c.startswith('event: ping') for c in out) and not any(c.startswith(':') for c in out)
    assert q not in api.subscribers

def test_header_wins_over_query():
    assert api.parse_event_id('7')==7 and api.parse_event_id('x') is None and api.parse_event_id(None) is None
