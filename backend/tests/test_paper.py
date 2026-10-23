from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from app.paper import PaperConfig, kelly, replay, run_research, quote_reason


def config(**kw):
    return PaperConfig(tradingDay=date(2026,10,19),optionDirection='convergence',initialCapital=100000,**kw)


def event(i, gap=100, spot=100, premium=2, missing_time=False, symbol='TSLA'):
    at=datetime(2026,10,19,14,0,tzinfo=timezone.utc)+timedelta(seconds=i*10)
    def quote(mid, size=10000):
        return dict(bid=mid-.01,ask=mid+.01,last=mid,source='test',feedType='REALTIME',unit='PER_SHARE',
            bidAt=None if missing_time else at.isoformat(),askAt=None if missing_time else at.isoformat(),
            lastAt=at.isoformat(),receivedAt=at.isoformat(),ingestedAt=at.isoformat(),bidSize=size,askSize=size)
    legs=[dict(contract=dict(id=symbol+r,underlyingId=symbol,expiration='2026-10-19',strike='100',right=r,
        multiplier='100',deliverable='100_SHARES',adjusted=False),quote=quote(premium,1000)) for r in ('C','P')]
    return dict(id=i+1,at=at,valuation=at,symbol=symbol,configId='v1',configHash='hash',stock=quote(spot),
        legs=legs,mode=dict(rawGapBps=gap,requestedCount=1,rows=[dict(value=spot*(1+gap/10000),call=legs[0],put=legs[1])]),
        duplicate=False,session=True,expiration='2026-10-19',config={})


def test_kelly_units_zero_and_probability():
    assert kelly(25,25)==(1,0)
    assert kelly(-50,25)==(2,.25)
    assert kelly(0,25)==(0,0)
    assert kelly(10,25)[1]==0
    with pytest.raises(ValidationError): config(stockSlippage=-1)
    with pytest.raises(ValidationError): config(delaySeconds=120,pendingExpirySeconds=10)


def test_strict_missing_time_blocks_both_accounts():
    r=replay([event(i,missing_time=True) for i in range(6)],config(),True)
    assert r['status']=='DATA_BLOCKED'
    assert r['totalNetPnl']==0
    assert all(a['filledOrders']==0 for a in r['accounts'])
    assert r['quality']['MISSING_EVENT_TIME']==6


def test_next_snapshot_only_and_reconciliation():
    es=[event(0),event(1,spot=100.1),event(2,gap=5,spot=100.2),event(3,gap=5,spot=100.3)]
    r=replay(es,config(),False)
    stock,option=r['accounts']
    assert stock['closedTrades']==1
    t=stock['trades'][0]
    assert t['entrySnapshotId']==2 and t['exitSnapshotId']==4
    assert t['entryPrice']==pytest.approx(100.12)
    assert t['exitPrice']==pytest.approx(100.28)
    assert stock['netPnl']==pytest.approx(t['grossPnl']-t['fees']-t['financing'])
    assert stock['equity']==pytest.approx(50000+stock['netPnl'])
    assert t['appliedKelly']==pytest.approx(t['rawKelly']*.25)
    assert option['trades'][0]['contractId']=='TSLAP'
    assert option['trades'][0]['netPnl']<0 # convergence alone need not yield profits
    assert sum(a['initialCapital'] for a in r['accounts'])==100000


def test_negative_gap_short_stock_and_call_option():
    r=replay([event(i,gap=-100) for i in range(2)],config(),False)
    stock,opt=r['accounts']
    assert stock['positions'][0]['direction']==-1
    assert opt['positions'][0]['contractId']=='TSLAC'
    assert stock['netPnl']<0 # spread and commission charged on mark, not an invented short gain


def test_option_follow_stock_is_distinct():
    cfg=config().model_copy(update={'optionDirection':'follow_stock'})
    r=replay([event(0),event(1)],cfg,False)
    assert r['accounts'][1]['positions'][0]['contractId']=='TSLAC'


def test_kelly_does_not_force_trades():
    r=replay([event(i,gap=7) for i in range(5)],config(),False)
    assert r['totalNetPnl']==0
    assert all(a['filledOrders']==0 for a in r['accounts'])


def test_cancel_disappearing_advantage_and_gapped_delay():
    for es in ([event(0),event(1,gap=5)],[event(0),event(10)]):
        r=replay(es,config(),False)
        assert all(a['filledOrders']==0 for a in r['accounts'])
        assert all(a['orders'][-1]['status']=='CANCELLED' for a in r['accounts'])


def test_stop_fills_next_price_not_stop_price():
    es=[event(0),event(1),event(2,spot=99),event(3,spot=98)]
    a=replay(es,config(),False)['accounts'][0]
    t=a['trades'][0]
    assert t['exitReason']=='UNDERLYING_STOP'
    assert t['exitPrice']==pytest.approx(97.98)
    assert t['netPnl'] < -25 # threshold is not a guaranteed execution price


def test_option_full_premium_is_bounded_even_when_stop_gaps():
    es=[event(0),event(1),event(2,premium=.5),event(3,premium=.02)]
    a=replay(es,config(),False)['accounts'][1]
    t=a['trades'][0]
    assert t['exitReason']=='PREMIUM_STOP'
    assert t['entryPrice']*100*t['quantity']+t['fees']<=251


def test_missing_contract_not_substituted_or_forced_closed():
    es=[event(0),event(1),event(100)]
    es[-1]['legs']=[]
    r=replay(es,config(),False)
    assert len(r['accounts'][1]['positions'])==1
    assert r['status']=='UNRESOLVED_POSITIONS'
    assert not r['accounts'][1]['finalValueVerified']


def test_stale_future_and_mock_are_rejected():
    e=event(0)
    for mutate,reason in [(dict(lastAt=(e['at']-timedelta(hours=1)).isoformat()),'STALE_OR_FUTURE_LAST'),
                           (dict(receivedAt=(e['at']+timedelta(seconds=1)).isoformat()),'STALE_OR_FUTURE_RECEIPT'),
                           (dict(feedType='MOCK'),'NON_REAL_QUOTE')]:
        q={**e['stock'],**mutate}
        assert quote_reason(q,e['at'],False)==reason


def test_shared_exposure_and_three_position_limit():
    es=[]
    for i in range(3):
        for j,s in enumerate(('TSLA','NVDA','AAPL','AMZN','META')):
            e=event(i,symbol=s);e['id']=i*10+j;es.append(e)
    a=replay(es,config(),False)['accounts'][0]
    assert len(a['positions'])<=3
    assert a['maxExposure']<=a['equity']*2
    assert sum(p['risk'] for p in a['positions'])<=a['initialCapital']*.02


def test_daily_loss_halts_entries_and_keeps_exit_orders():
    es=[event(0),event(1),event(2,spot=90),event(3,spot=89),event(4,spot=90)]
    a=replay(es,config(),False)['accounts'][0]
    assert a['dailyHalted']
    assert len([o for o in a['orders'] if o['side']=='OPEN' and o['status']=='FILLED'])==1


def test_no_lookahead_future_modification_preserves_entry():
    first=[event(0),event(1),event(2,gap=5),event(3,gap=5)]
    changed=deepcopy(first);changed[-1]=event(3,gap=-200,spot=120)
    a=replay(first,config(),False)['accounts'][0]['orders'][1]
    b=replay(changed,config(),False)['accounts'][0]['orders'][1]
    assert a==b


def test_report_deterministic_and_no_broker_orders():
    es=[event(0),event(1),event(2,gap=5),event(3,gap=5)]
    a=run_research(es,config());b=run_research(es,config())
    assert a==b
    assert a['hypothetical'] and a['liveOrdersEnabled'] is False
    assert len(a['sensitivity'])==3
    assert not a['fullDay']

def test_future_book_and_time_skew_fail_strict():
    e=event(0)
    e['stock']['askAt']=(e['at']+timedelta(seconds=1)).isoformat()
    assert replay([e],config(),True)['quality']['STALE_OR_FUTURE_BOOK']==1
    e=event(0)
    e['legs'][0]['quote']['bidAt']=(e['at']-timedelta(seconds=3)).isoformat()
    assert replay([e],config(),True)['quality']['UNSYNCHRONIZED']==1


def test_too_wide_spread_and_low_size_block_orders():
    for field in ('wide','size'):
        es=[event(0),event(1)]
        if field=='wide':
            es[1]['stock']['bid']=98;es[1]['stock']['ask']=102
        else:
            es[1]['stock']['bidSize']=1;es[1]['stock']['askSize']=1
        a=replay(es,config(),False)['accounts'][0]
        assert a['filledOrders']==0


def test_entry_cutoff_and_missing_session():
    es=[event(0),event(1)]
    for e in es:
        diff=timedelta(hours=5,minutes=30)
        e['at']+=diff;e['valuation']+=diff
        for q in [e['stock']]+[l['quote'] for l in e['legs']]:
            for field in ('bidAt','askAt','lastAt','receivedAt','ingestedAt'):q[field]=e['at'].isoformat()
    assert all(a['filledOrders']==0 for a in replay(es,config(),False)['accounts'])
    e=event(0);e['session']=False
    assert replay([e],config(),True)['quality']['UNCONFIRMED_OPTION_SESSION']==1


def test_paper_api_validation_and_empty_history():
    from fastapi.testclient import TestClient
    from app.api import app
    client=TestClient(app)
    assert client.get('/api/paper/runs').status_code==200
    assert client.get('/api/paper/coverage').status_code==200
    assert client.get('/api/paper/runs/not-found').status_code==404
    assert client.post('/api/paper/runs',json={'tradingDay':'2026-10-19'}).status_code==422
    assert client.post('/api/paper/runs',json={'tradingDay':'2000-01-01','optionDirection':'convergence'}).status_code==422
    assert client.post('/api/paper/runs',json={'tradingDay':'2026-10-19','optionDirection':'convergence'},headers={'origin':'https://untrusted.example'}).status_code==403

def test_api_persists_report_and_it_is_retrievable(monkeypatch):
    from fastapi.testclient import TestClient
    import app.api as api
    monkeypatch.setattr(api,'load_events',lambda db,cfg:[event(0),event(1),event(2,gap=5),event(3,gap=5)])
    client=TestClient(api.app)
    r=client.post('/api/paper/runs',json={'tradingDay':'2026-10-19','optionDirection':'convergence'})
    assert r.status_code==200,r.text
    data=r.json();saved=client.get('/api/paper/runs/'+data['id']).json()
    assert data==saved
    assert saved['exploratory']['accounts'][0]['closedTrades']==1
    assert saved['liveOrdersEnabled'] is False


def test_regular_session_rejects_holiday_and_after_hours():
    from app.paper import regular
    assert regular(datetime(2026,10,19,14,0,tzinfo=timezone.utc))
    assert not regular(datetime(2026,12,25,15,0,tzinfo=timezone.utc))
    assert not regular(datetime(2026,10,19,21,0,tzinfo=timezone.utc))


def test_default_capital_is_100000_per_independent_account():
    cfg=PaperConfig(tradingDay='2026-10-19',optionDirection='convergence')
    result=replay([],cfg,False)
    assert cfg.initialCapital==200000
    assert [a['initialCapital'] for a in result['accounts']]==[100000,100000]
    assert result['totalEquity']==200000
