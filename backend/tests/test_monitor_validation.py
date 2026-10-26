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

# --- follow-up to #12 review ---
from app.db import RunConfig
from app.service import config_for

@pytest.mark.parametrize('bad',[123,None,True,['SPY'],{'a':1},1.5])
@pytest.mark.parametrize('field',['symbol','underlyingId'])
def test_patch_non_string_symbol_message(bad,field):
    mid=post('SPY').json()['id']
    r=client.patch(f'/api/monitors/{mid}',json={field:bad}); assert r.status_code==422,r.text
    d=r.json()['detail']; assert '必须是字符串' in d and f'{field} must be a string' in d

def legacy(sym):
    with Session() as db:
        m=create_monitor(db,Config(symbol=sym,underlyingId=sym,provider='mock',dataMode='MOCK'))
        rc=config_for(db,m); return m.id,rc.id,rc.hash,rc.version

def test_legacy_padded_symbol_read_unchanged():
    mid,cid,h,v=legacy('  ZZSP  ')
    with Session() as db: rc=db.get(RunConfig,cid); assert Config.model_validate(rc.payload).symbol=='  ZZSP  '
    m=next(m for m in client.get('/api/monitors').json() if m['id']==mid)
    assert m['config']['symbol']=='  ZZSP  ' and m['hash']==h and m['version']==v

@pytest.mark.parametrize('patch',[{},{'symbol':'  ZZSP  '},{'symbol':'  ZZSP  ','underlyingId':'  ZZSP  '}])
def test_noop_patch_on_legacy_keeps_hash_and_version(patch):
    mid,cid,h,v=legacy('  ZZSP  ')
    r=client.patch(f'/api/monitors/{mid}',json=patch).json()
    assert r['configId']==cid and r['hash']==h and r['version']==v

def test_unrelated_patch_on_legacy_keeps_symbol():
    mid,cid,h,v=legacy('  ZZSP  ')
    r=client.patch(f'/api/monitors/{mid}',json={'nBelow':2}).json()
    assert r['config']['symbol']=='  ZZSP  ' and r['version']==v+1

def test_changed_symbol_is_trimmed_and_same_value_after_trim_is_noop():
    mid=post('SPY').json()['id']; before=client.get('/api/monitors').json()
    h=next(m for m in before if m['id']==mid)['hash']
    r=client.patch(f'/api/monitors/{mid}',json={'symbol':' SPY '}).json(); assert r['hash']==h and r['config']['symbol']=='SPY'
    with Session() as db:
        mid2,_,_,_=legacy('  ZZSP  ')
    r=client.patch(f'/api/monitors/{mid2}',json={'symbol':' QQQ '}).json(); assert r['config']['symbol']=='QQQ'

def test_plain_config_does_not_trim():
    assert Config(symbol=' A ',underlyingId=' A ',provider='mock',dataMode='MOCK').symbol==' A '
