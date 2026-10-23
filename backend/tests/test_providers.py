import asyncio
from datetime import datetime,timezone
import pytest,httpx
from app.providers import *
from app.gateway import app as gateway
from app.worker import Worker
from app.service import create_monitor
from app.db import Session,RunConfig,AggregateResult,ProviderStatus
from sqlalchemy import select

@pytest.mark.asyncio
async def test_mock_deterministic(at):
    a=MockProvider(clock=lambda:at);b=MockProvider(clock=lambda:at)
    qa=await a.get_underlying_snapshots(['SPY']);qb=await b.get_underlying_snapshots(['SPY'])
    assert qa['SPY'].last==qb['SPY'].last;assert qa['SPY'].feedType=='MOCK'

@pytest.mark.asyncio
async def test_http_gateway_pagination(monkeypatch):
    monkeypatch.setenv('QUOTE_GATEWAY_URL','http://gateway')
    p=HttpJsonProvider(transport=httpx.ASGITransport(app=gateway));cfg=Config()
    assert (await p.get_capabilities()).feedType=='MOCK'
    expiry=(await p.list_expirations(cfg))[0];contracts=await p.list_option_contracts(cfg,expiry)
    assert len(contracts)==42
    quotes=await p.get_option_quotes(contracts);assert len(quotes)==42;assert all(q.feedType=='MOCK' for q in quotes.values());await p.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('status,code',[(429,'RATE_LIMITED'),(403,'PERMISSION_DENIED'),(409,'NOT_SUBSCRIBED'),(503,'HTTP_503')])
async def test_http_errors(monkeypatch,status,code):
    monkeypatch.setenv('QUOTE_GATEWAY_URL','http://gateway')
    p=HttpJsonProvider(transport=httpx.MockTransport(lambda r:httpx.Response(status,headers={'Retry-After':'30'})))
    with pytest.raises(ProviderError) as error:await p.get_capabilities()
    assert error.value.code==code
    if status==429:assert error.value.retry_after==30
    await p.close()

def test_calendar_dst_holiday_halfday():
    c=Config()
    # Thanksgiving closed; day after closes 13:00 ET (18:00 UTC).
    assert not regular_session(c,datetime(2026,11,26,16,tzinfo=timezone.utc))
    assert regular_session(c,datetime(2026,11,27,17,tzinfo=timezone.utc))
    assert not regular_session(c,datetime(2026,11,27,19,tzinfo=timezone.utc))
    assert regular_session(c,datetime(2026,7,6,14,tzinfo=timezone.utc))
    assert not regular_session(c,datetime(2026,1,5,14,tzinfo=timezone.utc))

@pytest.mark.asyncio
async def test_worker_isolation_and_nonreentry(monkeypatch):
    monkeypatch.delenv('QUOTE_GATEWAY_URL',raising=False)
    with Session() as db:
        m=create_monitor(db,Config(provider='mock',dataMode='MOCK',nBelow=1));rc=db.get(RunConfig,m.config_id)
        bad=create_monitor(db,Config(provider='http',dataMode='API',nBelow=1))
    w=Worker();await w.cycle()
    with Session() as db:
        assert db.scalar(select(AggregateResult).where(AggregateResult.monitor_id==m.id)) is not None
        p=db.scalar(select(ProviderStatus).where(ProviderStatus.monitor_id==bad.id));assert p.payload['status']=='NOT_CONFIGURED'
    lock=w.locks[m.id];await lock.acquire()
    before=w.next_due[m.id];await w.collect(m.id,rc);assert w.next_due[m.id]==before;lock.release()
    for p in w.providers.values():await p.close()

@pytest.mark.asyncio
async def test_rate_limit_respects_vendor_delay_without_escalating_warmup():
    from app.db import Monitor
    with Session() as db:
        m=create_monitor(db,Config(provider='futu',dataMode='API'));rc=db.get(RunConfig,m.config_id)
    w=Worker()
    try:
        for _ in range(4):
            await w.collect(m.id,rc,pre_error=ProviderError('RATE_LIMITED',30))
        with Session() as db:
            status=db.scalar(select(ProviderStatus).where(ProviderStatus.monitor_id==m.id).order_by(ProviderStatus.id.desc()))
            assert status.payload['retryAfter']==30
            assert status.payload['status']=='RATE_LIMITED'
    finally:
        with Session() as db:db.get(Monitor,m.id).active=False;db.commit()
        for p in w.providers.values():await p.close()
