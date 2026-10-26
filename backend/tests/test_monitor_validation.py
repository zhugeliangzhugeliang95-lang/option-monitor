import pytest
from fastapi.testclient import TestClient
from app.api import app
from app.db import Session
from app.models import Config
from app.service import create_monitor
client=TestClient(app)
UI={'manual':'MANUAL','mock':'MOCK','http':'API','futu':'API'}

def post(symbol,provider='mock',**kw):
    return client.post('/api/monitors',json={'symbol':symbol,'underlyingId':symbol,'provider':provider,'dataMode':UI[provider],**kw})

@pytest.mark.parametrize('provider',['manual','mock','http','futu'])
def test_frontend_payloads_still_accepted(provider):
    r=post('SPY',provider,nBelow=5,nAbove=5); assert r.status_code==200 and r.json()['config']['symbol']=='SPY'

@pytest.mark.parametrize('raw,stored',[(' SPY ','SPY'),('\tQQQ\n','QQQ'),('US.SPY','US.SPY'),('BRK.B','BRK.B'),('spy','spy'),('BF-B','BF-B'),('A_1','A_1'),('X'*20,'X'*20)])
def test_accepted(raw,stored):
    r=post(raw); assert r.status_code==200,r.text
    assert r.json()['config']['symbol']==stored and r.json()['config']['underlyingId']==stored

def test_futu_trimmed_then_allow_listed():
    assert post(' SPY ','futu').status_code==200
    r=post('UBER','futu'); assert r.status_code==422 and '富途' in r.text

@pytest.mark.parametrize('raw',['','   ','!!$#','A B','SPY;DROP','x'*100,'X'*21,'.SPY','-SPY','SPY/USD','$SPY','SPY\x00'])
def test_rejected_with_plain_message(raw):
    r=post(raw); assert r.status_code==422,r.text
    d=r.json()['detail']; assert isinstance(d,str) and ('标的代码' in d) and ('symbol' in d)

def test_underlying_id_checked_too():
    r=client.post('/api/monitors',json={'symbol':'SPY','underlyingId':'S P Y','provider':'mock','dataMode':'MOCK'})
    assert r.status_code==422 and 'underlyingId' in r.json()['detail']

def test_update_rejects_bad_new_symbol_and_trims():
    mid=post('SPY').json()['id']
    assert client.patch(f'/api/monitors/{mid}',json={'symbol':'!!$#'}).status_code==422
    r=client.patch(f'/api/monitors/{mid}',json={'symbol':' QQQ '}); assert r.status_code==200 and r.json()['config']['symbol']=='QQQ'

def test_legacy_odd_symbol_still_readable_and_updatable():
    with Session() as db: mid=create_monitor(db,Config(symbol='!!$# legacy',underlyingId='SPY',provider='mock',dataMode='MOCK')).id
    assert any(m['id']==mid for m in client.get('/api/monitors').json())
    r=client.patch(f'/api/monitors/{mid}',json={'nBelow':2}); assert r.status_code==200 and r.json()['config']['symbol']=='!!$# legacy'
    assert client.patch(f'/api/monitors/{mid}',json={'symbol':'!!$# legacy','nAbove':1}).status_code==200
    assert client.patch(f'/api/monitors/{mid}',json={'symbol':'!!$#'}).status_code==422
