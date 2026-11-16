from datetime import datetime,timezone
from decimal import Decimal
from app.futu_gateway import next_expiry,map_leg,number,update_time

def test_expiry_roll_and_halfday():
    assert next_expiry(['2026-11-06','2026-11-13'],datetime(2026,11,6,20,tzinfo=timezone.utc))=='2026-11-06'
    assert next_expiry(['2026-11-06','2026-11-13'],datetime(2026,11,6,21,tzinfo=timezone.utc))=='2026-11-13'
    assert next_expiry(['2026-11-27','2026-11-30'],datetime(2026,11,27,18,tzinfo=timezone.utc))=='2026-11-30'

def test_snapshot_time_is_not_event_time():
    meta=dict(code='US.GOOG261106C275000',stock_owner='US.GOOG',strike_time='2026-11-06',lot_size=100,option_standard_type='STANDARD')
    quote=dict(last_price=7.59,bid_price=7.5,ask_price=7.7,bid_vol=1,ask_vol=2,update_time='2026-10-30 16:00:00',option_contract_size=100)
    leg=map_leg(meta,quote,'US.GOOG','2026-11-06')
    assert leg['lastAt'] is None and leg['bookAt'] is None
    assert leg['updatedAt']=='2026-10-30T20:00:00+00:00'
    assert leg['last']==7.59 and not leg['adjusted']
    quote['bid_vol']=0
    assert map_leg(meta,quote,'US.GOOG','2026-11-06')['bid'] is None

def test_missing_and_invalid():
    assert number(float('nan')) is None and number(-1) is None and number('N/A') is None
    assert map_leg({},None,'US.GOOG','2026-11-06') is None
    assert update_time('N/A') is None

def test_full_quote_collection_uses_selected_real_codes(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app import futu_gateway as gateway
    from app.engine import analyze
    class Frame:
        def __init__(self, rows): self.rows=rows
        def to_dict(self, orient): return self.rows
    expiry='2026-11-13'
    strikes=[95,97,98,100,103,105,108,110,115]
    chain=[dict(code=f'US.TEST_{k}_{right}',stock_owner='US.TEST',strike_time=expiry,
                strike_price=k,option_type=right,lot_size=100,option_standard_type='STANDARD')
           for k in strikes for right in ('CALL','PUT')]
    calls=[]
    class Context:
        closed=False
        def get_global_state(self):return 0,dict(qot_logined=True,market_us='MORNING')
        def get_user_info(self,**kwargs):return 0,dict(us_qot_right='LV1',us_option_qot_right='LV1')
        def get_market_snapshot(self,codes):
            calls.append(codes)
            if codes==['US.TEST']:return 0,Frame([dict(code='US.TEST',last_price=103.2,update_time='2026-11-12 10:00:00')])
            return 0,Frame([dict(code=r['code'],last_price=5 if r['option_type']=='CALL' else 4,
                bid_price=3,ask_price=6,bid_vol=1,ask_vol=1,option_contract_size=100,
                option_valid=True,option_strike_price=r['strike_price'],option_type=r['option_type'],
                strike_time=expiry,update_time='2026-11-12 10:00:00') for r in chain if r['code'] in codes])
        def get_option_expiration_date(self,**kwargs):return 0,Frame([dict(strike_time=expiry)])
        def get_option_chain(self,**kwargs):return 0,Frame(chain)
        def close(self):self.closed=True
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz):return datetime(2026,11,12,15,tzinfo=timezone.utc)
    ctx=Context()
    monkeypatch.setitem(sys.modules,'futu',SimpleNamespace(OpenQuoteContext=lambda **kwargs:ctx))
    monkeypatch.setattr(gateway.socket,'create_connection',lambda *args,**kwargs:Socket())
    monkeypatch.setattr(gateway,'datetime',FixedClock)
    data=gateway.collect('TEST')
    assert [float(r['strike']) for r in data['input']['rows']]==[97,98,100,103,105,108,110]
    assert len(calls[1])==14 and set(calls[1]).issubset({r['code'] for r in chain})
    assert ctx.closed
    report=analyze(gateway.StudyInput.model_validate(data['input']))
    assert report['modes']['LAST']['summary']['count']==7
    assert report['modes']['MID']['summary']['count']==7
    assert report['modes']['LAST']['summary']['qualityCount']==0

def test_connection_status_only_returns_safe_fields():
    from app.futu_gateway import connection_info
    class Context:
        def get_global_state(self):return 0,dict(qot_logined=True,market_us='PRE_MARKET_BEGIN',user_id='secret')
        def get_user_info(self,info_field):
            assert info_field==[4]
            return 0,dict(us_qot_right='LV3',us_option_qot_right='LV1',web_key='secret',nick_name='secret')
    info=connection_info(Context())
    assert info['quoteLoggedIn'] and info['optionRight']=='LV1'
    assert 'secret' not in str(info)

def test_known_errors_have_actionable_messages():
    import pytest
    from app.futu_gateway import unwrap,GatewayError
    for error,code in [('no permission to request','QUOTE_PERMISSION'),('too frequent','RATE_LIMIT')]:
        with pytest.raises(GatewayError) as e:unwrap((-1,error),'期权快照')
        assert e.value.code==code

def test_stock_anchor_follows_market_session():
    import pytest
    from app.futu_gateway import stock_anchor,GatewayError
    snapshot={'last_price':759.81,'pre_price':755.14,'after_price':756.1,'overnight_price':756.44}
    for market,expected,label in [('PRE_MARKET_BEGIN',755.14,'盘前成交价'),('MORNING',759.81,'常规成交价'),('AFTER_HOURS_BEGIN',756.1,'盘后成交价'),('NIGHT_OPEN',756.44,'夜盘成交价')]:
        price,info=stock_anchor(snapshot,market)
        assert price==expected and info['label']==label
    with pytest.raises(GatewayError):stock_anchor({'last_price':759.81},'PRE_MARKET_BEGIN')
