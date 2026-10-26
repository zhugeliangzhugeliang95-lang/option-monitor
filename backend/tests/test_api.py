from fastapi.testclient import TestClient
from sqlalchemy import select,func
from datetime import timedelta
from app.api import app
from app.db import *
from app.models import Config
from app.service import save_snapshot,create_monitor,revise
client=TestClient(app)
def setup_monitor(cfg,sample):
    with Session() as db:m=create_monitor(db,cfg)
    s=sample();s.monitorId=m.id
    return m,s

def test_manual_persistence_preview_and_version(cfg,sample):
    m,s=setup_monitor(cfg,sample)
    r=client.post('/api/manual-snapshots',json=s.model_dump(mode='json'));assert r.status_code==200,r.text
    result=r.json();assert result['modes']['MID']['rawMean']==602.85
    for mode in ('BID','ASK','MID','LAST','CUSTOM'):
        p=client.post(f'/api/monitors/{m.id}/preview-price-basis',json={'snapshotId':result['snapshotId'],'optionPricePreset':mode});assert p.status_code==200
    h=client.get(f'/api/monitors/{m.id}/history').json();assert len(h)==1
    s.valuationAt+=timedelta(seconds=10);s.underlying.last=602
    client.post('/api/manual-snapshots',json=s.model_dump(mode='json'))
    with Session() as db:assert db.get(RawSnapshot,result['snapshotId']).payload['underlying']['last']==601
    update=client.patch(f'/api/monitors/{m.id}',json={'optionPricePreset':'BID'}).json();assert update['version']==2
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(RawSnapshot).where(RawSnapshot.monitor_id==m.id))==2
        assert db.scalar(select(func.count()).select_from(RunConfig).where(RunConfig.monitor_id==m.id))==2
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

def test_http_not_configured_and_secret_not_exposed(monkeypatch,cfg,sample):
    monkeypatch.delenv('QUOTE_GATEWAY_URL',raising=False);monkeypatch.setenv('QUOTE_GATEWAY_KEY','SUPER_SECRET_TEST')
    assert client.post('/api/providers/test',json={'provider':'http'}).json()['status']=='NOT_CONFIGURED'
    m,s=setup_monitor(cfg,sample)
    assert 'SUPER_SECRET_TEST' not in client.get('/api/providers/status').text

def test_refresh_manual_no_acquisition(cfg,sample):
    m,s=setup_monitor(cfg,sample)
    for _ in range(10):assert client.post(f'/api/monitors/{m.id}/refresh').json()['status']=='MANUAL_INPUT_REQUIRED'
    assert client.get(f'/api/monitors/{m.id}/history').json()==[]

def test_origin_rejected():
    r=client.post('/api/monitors',json={},headers={'origin':'https://untrusted.example'})
    assert r.status_code==403

def test_pairs_stored_in_aggregate_only(cfg,sample):
    with Session() as db:
        m=create_monitor(db,cfg); s=sample(); s.monitorId=m.id; r=save_snapshot(db,s)
        assert db.scalar(select(func.count()).select_from(PairResult).where(PairResult.monitor_id==m.id))==0
    latest=client.get('/api/results/latest').json()[m.id]
    assert latest['modes']['MID']['rows']==r['modes']['MID']['rows']

def test_history_and_latest_summary(cfg,sample):
    from app.service import SUMMARY_SCALARS
    with Session() as db:
        m=create_monitor(db,cfg); ids=[]
        for i in range(3):
            s=sample(spot=601+i*.01); s.monitorId=m.id; save_snapshot(db,s)
    full=client.get(f'/api/monitors/{m.id}/history').json()
    summ=client.get(f'/api/monitors/{m.id}/history?summary=1').json()
    assert [r['id'] for r in full]==[r['id'] for r in summ] and len(summ)==3
    for f,s in zip(full,summ):
        assert set(s)=={'id','modes',*SUMMARY_SCALARS}
        for k in SUMMARY_SCALARS: assert s[k]==f[k] and type(s[k])==type(f[k]), k
        assert s['modes']=={mode:{k:a[k] for k in ('rawMean','validatedMean','rawGapBps')} for mode,a in f['modes'].items()}
    after=client.get(f'/api/monitors/{m.id}/history?summary=1&after={full[0]["id"]}&limit=1').json()
    assert [r['id'] for r in after]==[full[1]['id']]
    assert [r['id'] for r in client.get(f'/api/monitors/{m.id}/history?after={full[0]["id"]}').json()]==[full[1]['id'],full[2]['id']]
    lf=client.get('/api/results/latest').json()[m.id]; ls=client.get('/api/results/latest?summary=1').json()[m.id]
    assert ls['id']==full[-1]['id'] and ls['oldConfiguration'] is False and lf['oldConfiguration'] is False
    assert 'rows' not in ls['modes']['MID'] and 'rows' in lf['modes']['MID'] and 'statistics' not in ls and 'statistics' not in lf
