import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.examples import goog
client=TestClient(app)

def test_health_and_new_frontend():
    assert client.get('/api/health').json()['independent']
    html=client.get('/').text
    assert '执行价研究室' in html and 'PARITY LAB' not in html
    assert client.get('/static/app.js').status_code==200

def test_examples_compare_and_export():
    item=client.get('/api/examples/goog').json()
    assert item['source']=='ATTACHMENT' and item['input']['spot'] is None
    c=client.post(f"/api/studies/{item['id']}/compare",json={'strikes':[275,277.5,280]}).json()
    assert c['lowest']==['277.5']
    assert client.get(f"/api/studies/{item['id']}/export").json()['report']==item['report']
    assert client.post('/api/import',json={'format':'json','content':__import__('json').dumps(item)}).json()['source']=='MANUAL'

def test_illustration_cannot_compare():
    item=client.get('/api/examples/seven').json()
    assert item['input'] is None
    assert client.post(f"/api/studies/{item['id']}/compare",json={'strikes':[123,127,129.5]}).status_code==422

def test_manual_json_and_csv():
    s=goog().model_dump(mode='json')
    assert client.post('/api/analyze',json=s).status_code==200
    s.pop('rows')
    r=client.post('/api/import',json={'format':'csv','settings':s,'content':'strike,call_last,put_last\n275,7.59,3.67\n277.5,5.85,4.72\n280,4.66,6.13'})
    assert r.status_code==200 and r.json()['report']['modes']['LAST']['rows'][0]['call']==7.59
    assert r.json()['report']['modes']['MID']['rows'][0]['implied'] is None

@pytest.mark.parametrize('body',[{'format':'json','content':'no'},{'format':'json','content':'[]'},{'format':'csv','content':'not,strikeprice\n1,2'}])
def test_import_validation(body):assert client.post('/api/import',json=body).status_code==422

def test_local_origin_guard():
    assert client.post('/api/analyze',json=goog().model_dump(mode='json'),headers={'Origin':'https://untrusted.example'}).status_code==403
    assert client.post('/api/analyze',json=goog().model_dump(mode='json'),headers={'Origin':'http://127.0.0.1:3200'}).status_code==200

def test_missing_study_and_removed_bases():
    assert client.get('/api/studies/missing/export').status_code==404
    assert client.post('/api/studies/missing/compare',json={'strikes':[1,2,3],'basis':'ASK'}).status_code==422
    assert client.post('/api/futu',json={'symbol':'../../secret'}).status_code==422

def test_connection_diagnostic_endpoint(monkeypatch):
    from app import main
    monkeypatch.setattr(main,'status_cache',None)
    async def gateway(arg):
        assert arg=='--status'
        return {'connected':True,'quoteLoggedIn':True,'optionRight':'LV1'}
    monkeypatch.setattr(main,'gateway',gateway)
    assert client.get('/api/futu/status').json()['optionRight']=='LV1'
    monkeypatch.setattr(main,'status_cache',None)

def test_futu_cache_preserves_original_times_and_changes_rate(monkeypatch):
    import time
    from app import main
    study=goog().model_dump(mode='json')
    from datetime import datetime,timezone
    original_valid=main.quote_cache_valid
    monkeypatch.setattr(main,'quote_cache_valid',lambda cached:original_valid(cached,datetime(2026,10,30,20,tzinfo=timezone.utc)))
    cached=dict(input=study,notes=[],receivedAt='2026-10-30T20:00:00Z',stockUpdatedAt=None,connection={'optionRight':'LV1'})
    monkeypatch.setattr(main,'futu_cache',{'GOOG':(time.monotonic(),cached)})
    result=client.post('/api/futu',json={'symbol':'GOOG','rate':.01}).json()
    assert result['cached'] and result['input']['rate']==.01
    assert result['receivedAt']==cached['receivedAt'] and result['connection']['optionRight']=='LV1'

def test_fast_symbol_change_waits_instead_of_failing(monkeypatch):
    import time
    from app import main
    monkeypatch.setattr(main,'futu_cache',{})
    monkeypatch.setattr(main,'last_attempt',time.monotonic())
    waits=[]
    async def pause(seconds):waits.append(seconds)
    async def gateway(symbol):return dict(input=goog().model_dump(mode='json'),notes=[],receivedAt='2026-10-30T20:00:00Z',stockUpdatedAt=None)
    monkeypatch.setattr(main.asyncio,'sleep',pause)
    monkeypatch.setattr(main,'gateway',gateway)
    r=client.post('/api/futu',json={'symbol':'GOOG'})
    assert r.status_code==200 and len(waits)==1 and 0<waits[0]<=10

@pytest.mark.parametrize('content',[
 '\ufeffstrike,call_last,put_last\n275,7.59,3.67',
 ' strike ,call_last,put_last\n275,7.59',
 'strike,call_last,put_last\n,,\n275,7.59,3.67',
])
def test_excel_csv_bom_blank_rows_and_missing_cells(content):
    settings=goog().model_dump(mode='json');settings.pop('rows')
    r=client.post('/api/import',json={'format':'csv','settings':settings,'content':content})
    assert r.status_code==200 and len(r.json()['input']['rows'])==1

@pytest.mark.parametrize('content',['strike,call_last\n275,7,unexpected','strike,strike\n275,275'])
def test_bad_csv_columns_are_validation_not_server_error(content):
    settings=goog().model_dump(mode='json');settings.pop('rows')
    assert client.post('/api/import',json={'format':'csv','settings':settings,'content':content}).status_code==422

def test_cache_invalid_at_expiry_even_inside_sixty_seconds():
    import time
    from datetime import datetime,timezone
    from app.main import quote_cache_valid
    cached=(time.monotonic(),{'input':{'expiration':'2026-11-13'}})
    assert quote_cache_valid(cached,datetime(2026,11,13,20,59,59,tzinfo=timezone.utc))
    assert not quote_cache_valid(cached,datetime(2026,11,13,21,0,0,tzinfo=timezone.utc))
    assert not quote_cache_valid((time.monotonic()-61,cached[1]),datetime(2026,11,13,20,tzinfo=timezone.utc))

@pytest.mark.parametrize('pricing_at',[
 '2036-11-06T21:00:00Z','9999-01-01T00:00:00Z','2026-10-29T20:00:00Z'
])
def test_wrong_pricing_day_is_validation_error(pricing_at):
    data=goog().model_dump(mode='json')|dict(timeRule='timestamp',pricingAt=pricing_at,rate=-.5)
    r=client.post('/api/analyze',json=data)
    assert r.status_code==422 and '定价参考时刻' in r.text

def test_pricing_timestamp_uses_new_york_date_and_preserves_import_mode():
    import json
    data=goog().model_dump(mode='json')|dict(timeRule='timestamp',pricingAt='2026-11-07T05:00:00+08:00')
    r=client.post('/api/import',json={'format':'json','content':json.dumps(data)})
    assert r.status_code==200
    assert r.json()['input']['timeRule']=='timestamp'
    assert r.json()['report']['T']==pytest.approx((7*86400+3600)/(365*86400))

def test_timestamp_before_valuation_or_more_than_ten_years_rejected():
    data=goog().model_dump(mode='json')|dict(timeRule='timestamp',pricingAt='2026-11-06T21:00:00Z')
    for valuation in ['2026-11-06T22:00:00Z','2000-10-30T20:00:00Z']:
        assert client.post('/api/analyze',json=data|dict(valuationAt=valuation)).status_code==422

def test_import_editor_recalculation_keeps_invalid_contract_blocked():
    import json,subprocess,shutil
    from pathlib import Path
    if not shutil.which('node'):pytest.skip('Node required for cross-layer editor regression')
    data=goog().model_dump(mode='json');data['spot']=277.5
    data['rows'][0]['call']['adjusted']=True
    code="""import {toDraft,fromDraft} from './app/static/editor-model.mjs';
let text='';for await(const chunk of process.stdin)text+=chunk;
const input=JSON.parse(text);input.rows=input.rows.map(r=>{const d=toDraft(r,input);return fromDraft(d.values,d.original)});console.log(JSON.stringify(input));"""
    edited=json.loads(subprocess.check_output(['node','--input-type=module','-e',code],input=json.dumps(data).encode(),cwd=Path(__file__).resolve().parents[1]))
    item=client.post('/api/analyze',json=edited).json()
    assert item['report']['modes']['LAST']['rows'][0]['implied'] is None
    result=client.post(f"/api/studies/{item['id']}/compare",json={'strikes':[275,277.5,280]}).json()
    assert not result['rankable'] and '调整合约不支持' in result['issues']

@pytest.mark.parametrize('column',['call_adjusted','call_multiplier','call_lsat'])
def test_csv_does_not_silently_discard_unknown_or_contract_fields(column):
    data=goog().model_dump(mode='json');data.pop('rows')
    r=client.post('/api/import',json={'format':'csv','settings':data,'content':f'strike,call_last,put_last,{column}\n275,7.59,3.67,1'})
    assert r.status_code==422 and '不支持的列' in r.text
