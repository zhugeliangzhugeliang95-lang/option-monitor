from fastapi.testclient import TestClient
from sqlalchemy import select,func
from datetime import timedelta
from app.api import app
from app.db import *
from app.models import Config
from app.service import save_snapshot,create_monitor,statistics_for,revise
client=TestClient(app)
def setup_monitor(cfg,sample):
    with Session() as db:m=create_monitor(db,cfg)
    s=sample();s.monitorId=m.id
    return m,s

def test_manual_persistence_preview_version_and_freeze(cfg,sample):
    m,s=setup_monitor(cfg,sample)
    r=client.post('/api/manual-snapshots',json=s.model_dump(mode='json'));assert r.status_code==200,r.text
    result=r.json();assert result['modes']['MID']['rawMean']==602.85
    for mode in ('BID','ASK','MID','LAST','CUSTOM'):
        p=client.post(f'/api/monitors/{m.id}/preview-price-basis',json={'snapshotId':result['snapshotId'],'optionPricePreset':mode});assert p.status_code==200
    h=client.get(f'/api/monitors/{m.id}/history').json();assert len(h)==1
    f=client.post('/api/research/frozen-observations',json={'snapshotId':result['snapshotId']}).json()
    s.valuationAt+=timedelta(seconds=10);s.underlying.last=602
    client.post('/api/manual-snapshots',json=s.model_dump(mode='json'))
    with Session() as db:assert db.get(FrozenObservation,f['id']).payload['result']['anchor']==601
    update=client.patch(f'/api/monitors/{m.id}',json={'optionPricePreset':'BID'}).json();assert update['version']==2
    st=client.get(f'/api/monitors/{m.id}/statistics').json();assert st['n']==0
    export=client.get(f'/api/exports/{m.id}').json();assert len(export['raw_snapshots'])==2;assert len(export['run_configs'])==2
    assert client.delete(f'/api/monitors/{m.id}').status_code==200
    assert len(client.get(f'/api/monitors/{m.id}/history').json())==2

def test_duplicate_and_new_event_same_prices(cfg,sample):
    m,s=setup_monitor(cfg,sample)
    with Session() as db:
        first=save_snapshot(db,s);s.valuationAt+=timedelta(seconds=1)
        second=save_snapshot(db,s);assert second['duplicate'];assert not second['modes']['MID']['strictEligible']
        for q in [s.underlying]+[l.quote for l in s.legs]:
            q.bidAt+=timedelta(seconds=1);q.askAt+=timedelta(seconds=1);q.lastAt+=timedelta(seconds=1)
        third=save_snapshot(db,s);assert not third['duplicate']

def test_stream_isolation(cfg,sample):
    m,s=setup_monitor(cfg,sample);s.underlying.feedType='MOCK'
    assert client.post('/api/manual-snapshots',json=s.model_dump(mode='json')).status_code==422

def test_import_csv(cfg,sample):
    m,s=setup_monitor(cfg,sample)
    text='strike,call_bid,call_ask,put_bid,put_ask\n600,4,4.2,1,1.5\n'
    r=client.post('/api/imports/quotes',json={'monitorId':m.id,'format':'csv','content':text,'metadata':{'valuationAt':s.valuationAt.isoformat(),'expiration':'2026-10-19','underlying':s.underlying.model_dump(mode='json')}})
    assert r.status_code==200,r.text
    assert r.json()['results'][0]['historical']
    assert r.json()['results'][0]['modes']['MID']['rawMean']==602.85
    assert not r.json()['results'][0]['modes']['MID']['strictEligible']

def test_http_not_configured_and_secret_not_exported(monkeypatch,cfg,sample):
    monkeypatch.delenv('QUOTE_GATEWAY_URL',raising=False);monkeypatch.setenv('QUOTE_GATEWAY_KEY','SUPER_SECRET_TEST')
    assert client.post('/api/providers/test',json={'provider':'http'}).json()['status']=='NOT_CONFIGURED'
    m,s=setup_monitor(cfg,sample)
    assert 'SUPER_SECRET_TEST' not in client.get(f'/api/exports/{m.id}').text

def test_refresh_manual_no_acquisition(cfg,sample):
    m,s=setup_monitor(cfg,sample)
    for _ in range(10):assert client.post(f'/api/monitors/{m.id}/refresh').json()['status']=='MANUAL_INPUT_REQUIRED'
    assert client.get(f'/api/monitors/{m.id}/history').json()==[]

def test_origin_rejected():
    r=client.post('/api/monitors',json={},headers={'origin':'https://untrusted.example'})
    assert r.status_code==403
