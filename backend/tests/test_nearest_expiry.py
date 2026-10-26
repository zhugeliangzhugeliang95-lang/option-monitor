from datetime import datetime, timezone
import asyncio
import pytest
from app.models import Config, NewConfig
from app.expiry import nearest_expiration, reference_close
from app.providers import target_expiry
from app.engine import compute
from test_futu_swr import make, run, FRI_11


def at(s): return datetime.fromisoformat(s).astimezone(timezone.utc)

def test_new_default_and_legacy():
    assert NewConfig().expiryMode == 'nearest'
    assert NewConfig().timeRule == 'expiryClose'
    assert Config().expiryMode == 'sameDay'  # legacy untagged payloads keep their meaning

@pytest.mark.parametrize('now,dates,want',[
    ('2026-10-26T11:00:00-04:00',['2026-10-30','2026-10-23','2026-10-26'],'2026-10-26'),
    ('2026-10-26T11:00:00-04:00',['2026-10-30','2026-10-23'],'2026-10-30'),
    ('2026-10-26T16:00:00-04:00',['2026-10-26','2026-10-27'],'2026-10-27'),
    ('2026-10-24T11:00:00-04:00',['2026-10-23','2026-10-30'],'2026-10-30'),
    ('2026-10-26T11:00:00-04:00',[],None),
    ('2026-10-26T11:00:00-04:00',['2026-10-23'],None),
    ('2026-11-27T13:00:00-05:00',['2026-11-27','2026-11-30'],'2026-11-30'),
])
def test_nearest_sorted_calendar_and_cutoff(now,dates,want):
    cfg=NewConfig()
    assert target_expiry(cfg,now[:10],dates,at(now))==want

def test_manual_and_same_day_not_rolled():
    assert target_expiry(Config(expiryMode='manual',expiration='2026-10-19'),'2026-10-26')=='2026-10-19'
    assert target_expiry(Config(expiryMode='sameDay'),'2026-10-26')=='2026-10-26'

def test_dst_and_early_close_reference():
    assert reference_close('XNYS','2026-10-30').hour==20
    assert reference_close('XNYS','2026-11-02').hour==21
    assert reference_close('XNYS','2026-11-27').hour==18

def test_roll_pricing_ignores_stale_fixed_timestamp(cfg,sample):
    cfg=cfg.model_copy(update={'expiryMode':'nearest','timeRule':'expiryClose','pricingAt':at('2026-10-01T16:00:00-04:00')})
    snap=sample()
    for leg in snap.legs:
        leg.contract.pricingAt=None
        leg.contract.expiration='2026-10-23'
    snap.selectedExpiration='2026-10-23'
    result=compute(cfg,snap)
    assert result['expiration']=='2026-10-23'
    assert result['modes']['MID']['rawMean'] is not None
    assert result['modes']['MID']['rows'][0]['T']>0
    assert result['pricingReferenceAt']==reference_close('XNYS','2026-10-23').isoformat()
    assert any('定价时间假设' in w for w in result['warnings'])
    assert snap.legs[0].contract.pricingAt is None  # do not forge vendor metadata

def test_no_substitution_for_missing_selected_chain(cfg,sample):
    cfg=cfg.model_copy(update={'expiryMode':'nearest','timeRule':'expiryClose'})
    snap=sample();snap.selectedExpiration='2026-10-20'
    for leg in snap.legs:leg.contract.expiration='2026-10-23'
    result=compute(cfg,snap)
    assert result['modes']['MID']['rawMean'] is None
    assert result['expiration']=='2026-10-20'

def test_futu_nearest_discovery_then_hot_and_day_roll():
    async def scenario():
        p,ctx,mono,wall=make()
        c=NewConfig(provider='futu',dataMode='API',nBelow=1)
        cfgs={'spy':c}
        try:
            for _ in range(4): result,errors=await run(p,cfgs)
            assert not errors
            assert result['spy'].selectedExpiration=='2026-10-23'
            assert compute(c,result['spy'])['modes']['MID']['rawMean'] is not None
            wall.jump(3*86400)  # Monday; previous Friday expiry disappears
            for _ in range(4): result,errors=await run(p,cfgs)
            assert not errors
            assert result['spy'].selectedExpiration=='2026-10-30'
            assert all(c.expiration=='2026-10-30' for c in result['spy'].contracts)
        finally:await p.close()
    asyncio.run(scenario())

def test_futu_discovery_bounded_many_symbols():
    async def scenario():
        p,ctx,mono,wall=make()
        cfgs={s:NewConfig(symbol=s,underlyingId=s,provider='futu',dataMode='API',nBelow=1) for s in ['SPY','QQQ','IWM','DIA','AAPL']}
        try:
            result,errors=await run(p,cfgs)
            assert not result
            assert len(errors)==5
            assert ctx.count('get_option_expiration_date')<=3
            for _ in range(9):result,errors=await run(p,cfgs)
            assert not errors and len(result)==5
        finally:await p.close()
    asyncio.run(scenario())

def test_api_create_stores_new_default():
    from fastapi.testclient import TestClient
    from app.api import app
    with TestClient(app) as client:
        r=client.post('/api/monitors',json={'symbol':'SPY','underlyingId':'SPY','provider':'futu','dataMode':'API'})
        assert r.status_code==200
        assert r.json()['config']['expiryMode']=='nearest'
        assert r.json()['config']['timeRule']=='expiryClose'
        client.delete('/api/monitors/'+r.json()['id'])

def test_failed_expiry_discovery_does_not_starve_other_symbols():
    from app.providers import ProviderError
    async def scenario():
        p,ctx,mono,wall=make()
        cfgs={s:NewConfig(symbol=s,underlyingId=s,provider='futu',dataMode='API',nBelow=1) for s in ['SPY','QQQ','IWM','DIA','AAPL']}
        real=p.expirations
        def fail(cfg,**kw):
            if cfg.symbol in ('SPY','QQQ','IWM'):raise ProviderError('PERMISSION_DENIED')
            return real(cfg,**kw)
        p.expirations=fail
        try:
            for _ in range(8):result,errors=await run(p,cfgs)
            assert 'DIA' in result and 'AAPL' in result
        finally:await p.close()
    asyncio.run(scenario())
