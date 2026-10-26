import asyncio
import threading
import pandas as pd
import pytest
from app.futu import FutuProvider, quote_from_row
from app.models import Config, Snapshot, Leg
from app.providers import ProviderError
from app.engine import compute
from datetime import datetime as _dt, timezone as _tz
# Fixtures use 2026-10-19..23 expirations; pin the provider clock so they are not 'expired'.
CLOCK19=lambda:_dt(2026,10,19,15,0,tzinfo=_tz.utc)
CLOCK23=lambda:_dt(2026,10,23,15,0,tzinfo=_tz.utc)


class Mono:
    """Fake monotonic clock: the batch deadline, limiter and chain TTL never depend on real time."""
    def __init__(self): self.t=1000.0
    def __call__(self): return self.t


async def warm(p, configs, rounds=30):
    """Run batches (31 s apart on the fake clock) until no monitor is CHAIN_WARMING; return the last batch."""
    for _ in range(rounds):
        snapshots,errors=await p.collect_batch(configs)
        if not any(e.code=='CHAIN_WARMING' for e in errors.values()):
            return snapshots,errors
        p.mono.t+=31
    raise AssertionError('chains never warmed')


class OpenD:
    def __init__(self, rights='LV1'):
        self.rights = rights
        self.calls = []
        self.closed = False

    def get_global_state(self):
        return 0, {'qot_logined': True, 'market_us': 'AFTER_HOURS_END'}

    def get_user_info(self):
        return 0, {'us_qot_right': 'LV3', 'us_option_qot_right': self.rights, 'user_id': 'PRIVATE'}

    def get_option_expiration_date(self, code):
        self.calls.append('expirations')
        return 0, pd.DataFrame([{'strike_time': '2026-10-19'}])

    def get_option_chain(self, code, start, end):
        self.calls.append('chain')
        return 0, pd.DataFrame([dict(code='US.SPY261019'+r+'600000', stock_owner=code,
                                    strike_time=start, strike_price=600, lot_size=100,
                                    option_type=t, option_standard_type='STANDARD')
                               for r,t in [('C','CALL'),('P','PUT')]])

    def get_market_snapshot(self, codes):
        self.calls.append('snapshot')
        return 0, pd.DataFrame([dict(code=c, last_price=601 if c=='US.SPY' else 2,
                                    bid_price=600.9 if c=='US.SPY' else 1.9,
                                    ask_price=601.1 if c=='US.SPY' else 2.1,
                                    update_time='2026-10-19 11:00:00.000', bid_vol=10, ask_vol=10,
                                    option_valid=c!='US.SPY', stock_owner='US.SPY', strike_time='2026-10-19',
                                    option_strike_price=600, option_contract_size=100,
                                    option_type='CALL' if 'C600000' in c else 'PUT') for c in codes])

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_permissions_block_quotes_and_diagnostic_redacts():
    ctx=OpenD('NO');p=FutuProvider(ctx)
    try:
        diagnosis=await p.diagnose()
        assert diagnosis['status']=='FUTU_OPTION_PERMISSION_REQUIRED'
        assert 'PRIVATE' not in str(diagnosis)
        assert not diagnosis['liveQuotesVerified']
        with pytest.raises(ProviderError,match='FUTU_OPTION_PERMISSION_REQUIRED'):
            await p.get_underlying_snapshots(['SPY'])
        assert 'snapshot' not in ctx.calls
    finally:await p.close()
    assert ctx.closed


@pytest.mark.asyncio
async def test_real_snapshot_mapping_and_exclusion(at):
    ctx=OpenD();p=FutuProvider(ctx,clock=CLOCK19)
    cfg=Config(provider='futu',dataMode='API',nBelow=1,timeRule='days365',remainingDays=1)
    try:
        assert (await p.get_capabilities()).feedType=='UNKNOWN'
        q=(await p.get_underlying_snapshots(['SPY']))['SPY']
        assert q.feedType=='REALTIME' and q.lastAt==at
        assert q.bidAt is None and q.askAt is None
        assert await p.list_expirations(cfg)==['2026-10-19']
        await p.list_expirations(cfg)
        assert ctx.calls.count('expirations')==1
        cs=await p.list_option_contracts(cfg,'2026-10-19');quotes=await p.get_option_quotes(cs)
        assert cs[0].pricingAt is None and cs[0].lastTradingAt is None
        assert set(quotes)=={c.id for c in cs}
        snapshot=Snapshot(monitorId='test',valuationAt=at,underlying=q,provider='futu',contracts=cs,
                          legs=[Leg(contract=c,quote=quotes[c.id]) for c in cs])
        result=compute(cfg,snapshot);mid=result['modes']['MID']
        assert mid['rawMean'] is not None
        assert mid['validatedMean'] is None and mid['dBps'] is None and not mid['strictEligible']
        assert 'MISSING_EVENT_TIME' in mid['rows'][0]['reasons']
        missing=compute(cfg.model_copy(update={'timeRule':'timestamp'}),snapshot)
        assert 'MISSING_PRICING_TIME' in missing['modes']['MID']['rows'][0]['reasons']
    finally:await p.close()


def test_missing_zero_and_dst_not_fabricated():
    q=quote_from_row({'last_price':0,'bid_price':None,'ask_price':float('nan'),'update_time':''})
    assert q.last==0 and q.bid is None and q.ask is None and q.lastAt is None
    assert quote_from_row({'update_time':'2026-01-05 10:00:00'}).lastAt.hour==15
    assert quote_from_row({'update_time':'2026-07-06 10:00:00'}).lastAt.hour==14


@pytest.mark.asyncio
async def test_timeout_does_not_overlap_sdk_calls():
    p=FutuProvider(OpenD());gate=threading.Event()
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(p.run(lambda:gate.wait(1)),.02)
        with pytest.raises(ProviderError,match='FUTU_BUSY'):
            await p.run(lambda:True)
    finally:
        gate.set();await p.close()


@pytest.mark.asyncio
async def test_contract_metadata_mismatch():
    ctx=OpenD();original=ctx.get_market_snapshot
    def bad(codes):
        ret,rows=original(codes);rows['option_strike_price']=999;return ret,rows
    ctx.get_market_snapshot=bad;p=FutuProvider(ctx)
    try:
        cs=await p.list_option_contracts(Config(provider='futu',dataMode='API'),'2026-10-19')
        with pytest.raises(ProviderError,match='CONTRACT_METADATA_MISMATCH'):
            await p.get_option_quotes(cs)
    finally:await p.close()

@pytest.mark.asyncio
async def test_supported_universe_batch_and_metadata_error_isolation():
    from app.instruments import FUTU_SYMBOLS
    from app.models import Contract
    from app.futu import code_for
    from datetime import datetime, timezone
    class BatchOpenD(OpenD):
        def get_market_state(self, codes):
            return 0, pd.DataFrame([{'code':c,'market_state':'AFTERNOON'} for c in codes])
        def get_market_snapshot(self, codes):
            self.calls.append(('snapshot',len(codes)))
            rows=[]
            for code in codes:
                symbol=code.split('.')[1].split(':')[0]; option=':' in code
                rows.append(dict(code=code, last_price=101 if not option else 2,
                    bid_price=100.9 if not option else 1.9,ask_price=101.1 if not option else 2.1,
                    update_time=datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).isoformat(),
                    option_valid=option,stock_owner='US.'+symbol,strike_time='2026-10-23',
                    option_strike_price=999 if symbol=='NVDA' and option else 100,
                    option_contract_size=100,option_type='CALL' if code.endswith(':C') else 'PUT'))
            return 0,pd.DataFrame(rows)
    ctx=BatchOpenD();p=FutuProvider(ctx,clock=CLOCK23,mono=Mono())
    configs={s:Config(symbol=s,underlyingId=s,provider='futu',dataMode='API',expiryMode='manual',expiration='2026-10-23',nBelow=1,timeRule='days365',remainingDays=4) for s in FUTU_SYMBOLS}
    p.expirations=lambda cfg, **kwargs:['2026-10-23']
    p.contracts=lambda cfg,expiry, **kwargs:[Contract(id=code_for(cfg.underlyingId)+':'+right,underlyingId=cfg.underlyingId,expiration=expiry,strike=100,right=right) for right in ('C','P')]
    try:
        await warm(p,configs);ctx.calls.clear();p.mono.t+=31
        snapshots,errors=await p.collect_batch(configs)
        assert set(snapshots)==set(FUTU_SYMBOLS)-{'NVDA'}
        assert errors['NVDA'].code=='CONTRACT_METADATA_MISMATCH'
        assert ctx.calls==[('snapshot',len(FUTU_SYMBOLS)),('snapshot',2*len(FUTU_SYMBOLS))]
        for symbol,snapshot in snapshots.items():
            assert len(snapshot.legs)==2
            assert all(leg.contract.underlyingId==symbol for leg in snapshot.legs)
            assert snapshot.underlying.feedType=='REALTIME'
            assert snapshot.underlying.bidAt is None
            assert compute(configs[symbol],snapshot)['modes']['MID']['rawMean'] is not None
    finally:await p.close()


@pytest.mark.asyncio
async def test_batch_keeps_ready_symbols_when_one_chain_is_rate_limited():
    ctx=OpenD();ctx.get_market_state=lambda codes:(0,pd.DataFrame([{'code':c,'market_state':'AFTERNOON'} for c in codes]))
    p=FutuProvider(ctx,clock=CLOCK19,mono=Mono());original=p.expirations
    def dates(cfg, **kwargs):
        if cfg.underlyingId=='QQQ':raise ProviderError('RATE_LIMITED',30)
        return original(cfg, **kwargs)
    p.expirations=dates
    try:
        configs={s:Config(provider='futu',dataMode='API',symbol=s,underlyingId=s,expiryMode='manual',expiration='2026-10-19',nBelow=1) for s in ('SPY','QQQ')}
        for _ in range(3):  # SPY warms within the first batches; QQQ keeps failing
            snapshots,errors=await p.collect_batch(configs);p.mono.t+=31
        assert set(snapshots)=={'SPY'} and len(snapshots['SPY'].legs)==2
        # QQQ's failure is per monitor: CHAIN_WARMING with the real cause, never a batch-wide error
        assert errors['QQQ'].code=='CHAIN_WARMING' and errors['QQQ'].chain['lastError']['code']=='RATE_LIMITED'
        assert errors['QQQ'].chain['reason']=='retrying' and errors['QQQ'].chain['attempts']>=1
    finally:await p.close()


def test_unsupported_underlying_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):Config(provider='futu',dataMode='API',underlyingId='US.SPX')

@pytest.mark.asyncio
async def test_worker_persists_batched_futu_results(sample):
    from app.worker import Worker
    from app.service import create_monitor
    from app.db import Session, AggregateResult, Monitor
    from sqlalchemy import select
    w=Worker();ids=[]
    with Session() as db:
        for _ in range(2):
            ids.append(create_monitor(db,Config(provider='futu',dataMode='API',nBelow=1,timeRule='days365',remainingDays=2)).id)
    calls=[]
    async def batch(configs):
        calls.append(set(configs))
        return {mid:sample(feed='REALTIME').model_copy(update={'monitorId':mid,'provider':'futu'}) for mid in configs},{}
    w.providers['futu'].collect_batch=batch
    try:
        await w.cycle()
        await asyncio.wait({w.futu_task})  # the futu batch now runs as a background task
        assert calls==[set(ids)]
        with Session() as db:
            for mid in ids:
                result=db.scalar(select(AggregateResult).where(AggregateResult.monitor_id==mid))
                assert result.payload['feedType']=='REALTIME'
                assert result.payload['modes']['MID']['rawMean'] is not None
    finally:
        with Session() as db:
            for mid in ids:db.get(Monitor,mid).active=False
            db.commit()
        for p in w.providers.values():await p.close()


@pytest.mark.asyncio
async def test_batch_checks_permission_once_even_when_metadata_is_slow():
    ctx=OpenD();ctx.get_market_state=lambda codes:(0,pd.DataFrame([{'code':c,'market_state':'AFTERNOON'} for c in codes]))
    p=FutuProvider(ctx,clock=CLOCK19,mono=Mono());original=p.permissions;checks=[]
    def permission(*args,**kwargs):
        checks.append(1)
        return original(*args,**kwargs)
    p.permissions=permission
    try:
        cfg=Config(provider='futu',dataMode='API',expiryMode='manual',expiration='2026-10-19',nBelow=1)
        configs={str(i):cfg for i in range(20)}
        await warm(p,configs);checks.clear();ctx.calls.clear();p.mono.t+=31
        snapshots,errors=await p.collect_batch(configs)
        assert len(snapshots)==20 and not errors
        assert len(checks)==1
        assert ctx.calls.count('snapshot')==2
    finally:await p.close()


# --- expired expirations are skipped (item 3) ---
from app.providers import live_expirations, exchange_day
DATES=['2026-10-22','2026-10-23','2026-10-24','2026-10-26','2026-10-30']
def CLOCK24(): return _dt(2026,10,24,14,0,tzinfo=_tz.utc)  # 10:00 New York, Sat 10-24

def test_live_expirations_keeps_today_and_future():
    assert live_expirations(DATES,'2026-10-24')==['2026-10-24','2026-10-26','2026-10-30']
    # 03:30 UTC on 10-24 is still 10-23 in New York: 10-23 is today (0DTE) there, not expired.
    cfg=Config()
    assert exchange_day(cfg,_dt(2026,10,24,3,30,tzinfo=_tz.utc))=='2026-10-23'

class ChainOpenD(OpenD):
    def get_option_expiration_date(self, code):
        self.calls.append('expirations'); return 0, pd.DataFrame([{'strike_time':d} for d in reversed(DATES)])
    def get_option_chain(self, code, start, end):
        self.calls.append(('chain',start)); self.expiry=start; return super().get_option_chain(code,start,end)
    def get_market_snapshot(self, codes):
        ret,df=super().get_market_snapshot(codes); df['strike_time']=getattr(self,'expiry','2026-10-19'); return ret,df
    def get_market_state(self, codes):
        return 0, pd.DataFrame([{'code':c,'market_state':'AFTERNOON'} for c in codes])

@pytest.mark.asyncio
async def test_futu_expirations_drop_expired():
    p=FutuProvider(ChainOpenD(),clock=CLOCK24)
    try: assert await p.list_expirations(Config(provider='futu',dataMode='API'))==['2026-10-24','2026-10-26','2026-10-30']
    finally: await p.close()

@pytest.mark.asyncio
async def test_batch_same_day_uses_today_not_expired():
    ctx=ChainOpenD();p=FutuProvider(ctx,clock=CLOCK24,mono=Mono())
    # 2026-10-24 is a Saturday: (b7) no chain warming on non-trading days, so pin the warm window
    # open here; this test is about expiry choice. The weekend rule itself is test 6(vi).
    p.in_window=lambda cfg,wall: True
    try:
        cfg=Config(provider='futu',dataMode='API',nBelow=1)
        snaps,errors=await warm(p,{'m':cfg})
        assert not errors and ('chain','2026-10-24') in ctx.calls
        assert not any(c==('chain','2026-10-23') for c in ctx.calls)
        assert compute(cfg,snaps['m'])['expiration']=='2026-10-24'
    finally: await p.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('fixed,status_expired',[('2026-10-23',True),('2026-10-24',False),('2026-10-26',False)])
async def test_batch_fixed_expiration_expired_is_reported_not_substituted(fixed,status_expired):
    ctx=ChainOpenD();p=FutuProvider(ctx,clock=CLOCK24,mono=Mono())
    p.in_window=lambda cfg,wall: True  # Saturday: see test_batch_same_day_uses_today_not_expired
    try:
        cfg=Config(provider='futu',dataMode='API',nBelow=1,expiryMode='manual',expiration=fixed)
        snaps,errors=await warm(p,{'m':cfg})
        chains=[c[1] for c in ctx.calls if isinstance(c,tuple)]
        r=compute(cfg,snaps['m'])
        assert r['expiration']==fixed
        if status_expired:
            assert chains==[] and r['status']=='EXPIRED' and r['composition']==[]
        else:
            assert chains==[fixed] and r['status']!='EXPIRED'
    finally: await p.close()

def test_engine_expired_fixed_expiration_status(cfg,sample):
    s=sample(); cfg.expiryMode='manual'; cfg.expiration='2026-10-18'  # valuation day 2026-10-19
    assert compute(cfg,s)['status']=='EXPIRED'
    cfg.expiration='2026-10-19'; assert compute(cfg,s)['status']!='EXPIRED'
