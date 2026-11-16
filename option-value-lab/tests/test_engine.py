from decimal import Decimal
from datetime import timedelta
import pytest
from pydantic import ValidationError
from app.models import StudyInput,CompareRequest
from app.engine import analyze,compare,select_strikes,stats,close_at
from app.examples import goog,seven

def fixture():
    strikes=[123,124.5,127,129.5,133,134,135]
    values=[129.33,130.11,131.06,129.18,128.98,130.51,131.02]
    # Synthetic test quotes only: not shipped or labeled as actual observations.
    return StudyInput(symbol='X',spot=130.89,expiration='2026-11-06',valuationAt='2026-10-30T15:00:00-04:00',rate=0,timeRule='days',remainingDays=7,
      rows=[dict(strike=k,call=dict(last=20+v-k,bid=20+v-k,ask=20+v-k,lastAt='2026-10-30T15:00:00-04:00',bookAt='2026-10-30T15:00:00-04:00'),put=dict(last=20,bid=20,ask=20,lastAt='2026-10-30T15:00:00-04:00',bookAt='2026-10-30T15:00:00-04:00')) for k,v in zip(strikes,values)])

def test_seven_calculation():
    r=analyze(fixture())
    assert r['selectedStrikes']==['123','124.5','127','129.5','133','134','135']
    assert r['center']=='129.5'
    assert set(r['modes'])=={'LAST','MID'}
    for a in r['modes'].values():
        s=a['summary'];assert s['median']==pytest.approx(130.11)
        assert s['min']==pytest.approx(128.98) and s['max']==pytest.approx(131.06)
        assert s['count']==7 and s['qualityCount']==7 and s['complete']
        assert 'mean' not in s

def test_nearest_center_not_stock_split():
    keys,center=select_strikes([90,99,100,103,110,112,130,140],104)
    assert center==Decimal('103') and keys==list(map(Decimal,['90','99','100','103','110','112','130']))

@pytest.mark.parametrize('spot,want',[(105,100),(105.00001,110),(100,100)])
def test_ties_and_decimal(spot,want):assert select_strikes([100,110],spot)[1]==want

def test_missing_leg_not_replaced():
    s=fixture();s.rows[3].put=None
    r=analyze(s)
    assert r['center']=='129.5' and len(r['selectedStrikes'])==7
    assert r['modes']['LAST']['rows'][3]['implied'] is None
    assert r['modes']['LAST']['summary']['count']==6
    assert not r['modes']['LAST']['summary']['complete']

def test_edge_does_not_fill_other_side():
    assert select_strikes([100,101,102,103,104,105,106,107],100)[0]==[100,101,102,103]

@pytest.mark.parametrize('values,median',[((),None),((2,),2),((1,2,3,1000),2.5),((1,None,3),2)])
def test_summary(values,median):assert stats(values)['median']==median

def test_mid_never_falls_back_to_last():
    s=fixture();s.rows[0].call.ask=None
    r=analyze(s);assert r['modes']['LAST']['summary']['count']==7
    assert r['modes']['MID']['summary']['count']==6

def test_zero_is_real_input_not_missing():
    s=fixture();s.rows[0].call.last=0
    assert analyze(s)['modes']['LAST']['rows'][0]['call']==0

def test_crossed_raw_kept_quality_fails():
    s=fixture();s.rows[0].call.bid=s.rows[0].call.ask+1
    a=analyze(s)['modes']['MID']['rows'][0]
    assert a['implied'] is not None and not a['qualityPassed'] and '买价高于卖价' in a['issues']

@pytest.mark.parametrize('field,value',[('adjusted',True),('underlying','OTHER'),('expiration','2026-11-13'),('currency','HKD'),('multiplier',50)])
def test_contract_mismatch_rejected(field,value):
    s=fixture();d=s.model_dump(mode='json');d['rows'][0]['call'][field]=value
    a=analyze(StudyInput.model_validate(d))['modes']['LAST']['rows'][0]
    assert a['implied'] is None

def test_stale_data_not_claimed_good():
    s=fixture();s.rows[0].call.lastAt-=timedelta(minutes=5)
    a=analyze(s)['modes']['LAST']['rows'][0]
    assert a['implied'] is not None and not a['qualityPassed']

def test_point_in_time_discount():
    s=fixture();s.rate=.05;s.timeRule='timestamp';s.pricingAt=s.valuationAt+timedelta(days=2)
    r=analyze(s);assert r['T']==pytest.approx(2/365)
    s.pricingAt=s.valuationAt-timedelta(seconds=1)
    assert analyze(s)['modes']['LAST']['summary']['count']==0

def test_calendar_halfday_and_dst():
    assert close_at('2026-11-27').isoformat()=='2026-11-27T18:00:00+00:00'
    assert close_at('2026-10-30').hour==20 and close_at('2026-11-06').hour==21
    assert close_at('2026-11-07') is None

def test_goog_attachment_exact_and_missing_inputs_honest():
    s=goog();r=analyze(s)
    assert r['spot'] is None and r['center'] is None
    assert r['modes']['LAST']['summary']['median'] is None
    assert all(r['call'] is None for r in r['modes']['MID']['rows'])
    c=compare(s,list(map(Decimal,['275','277.5','280'])),'LAST')
    by={r['strike']:r for r in c['rows']}
    assert [by[k]['reference'] for k in ('275','277.5','280')]==pytest.approx([7.4,5.945,4.47])
    assert by['277.5']['premiumPct']==pytest.approx(-1.5979814971)
    assert c['lowest']==['277.5'] and not c['qualityPassed']
    assert sum(t['weight']*t['price'] for t in by['277.5']['terms'])==pytest.approx(5.945)

def test_seven_example_never_fabricates_option_prices():
    r=seven();assert r['modes']['LAST']['summary']['median']==130.11
    assert all(row['call'] is None and row['put'] is None for row in r['modes']['LAST']['rows'])
    assert not r['modes']['MID']['rows']

def test_uneven_replication_tie():
    s=goog();s.rows[0].strike=Decimal('270');s.rows[2].strike=Decimal('290')
    for r in s.rows:r.call.last=400-float(r.strike);r.put.last=10
    c=compare(s,[r.strike for r in s.rows],'LAST')
    assert len(c['lowest'])==3
    assert all(abs(r['premiumPct'])<1e-9 for r in c['rows'])

def test_cross_strike_multiplier_blocks_ranking():
    s=goog();s.rows[0].call.multiplier=s.rows[0].put.multiplier=Decimal('10')
    assert not compare(s,[r.strike for r in s.rows],'LAST')['rankable']

def test_nonpositive_reference_blocks_ranking():
    s=goog()
    for r in s.rows:r.call.last=r.put.last=0
    assert not compare(s,[r.strike for r in s.rows],'LAST')['rankable']

@pytest.mark.parametrize('patch',[{'spot':0},{'rate':float('nan')},{'valuationAt':'2026-10-30T15:00:00'},{'timeRule':'days','remainingDays':None}])
def test_invalid_inputs(patch):
    with pytest.raises(ValidationError):StudyInput.model_validate(fixture().model_dump(mode='json')|patch)

def test_duplicate_strikes():
    d=fixture().model_dump(mode='json');d['rows'].append(d['rows'][0])
    with pytest.raises(ValidationError):StudyInput.model_validate(d)

@pytest.mark.parametrize('strikes',[[1,1,3],[1,2],[-1,2,3],['NaN',2,3]])
def test_invalid_comparison(strikes):
    with pytest.raises(ValidationError):CompareRequest(strikes=strikes)

def test_old_snapshot_timestamp_does_not_claim_freshness():
    s=fixture();s.rows[0].call.updatedAt=s.valuationAt-timedelta(hours=3)
    result=analyze(s)['modes']['LAST']['rows'][0]
    assert result['implied'] is not None
    assert '快照更新时间较旧' in result['issues'] and not result['qualityPassed']
