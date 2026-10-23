from math import exp
from datetime import timedelta
from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.engine import *
from app.models import *

def test_excel(): assert 6.62-1.88+152.5*exp(-.05*2/365)==pytest.approx(157.19822490096)
def test_bases_and_synthetic(cfg,sample):
    result=compute(cfg,sample())
    assert [result['modes'][b]['rawMean'] for b in ('BID','ASK','MID')]==pytest.approx([603,602.7,602.85])
    row=result['modes']['BID']['rows'][0]
    assert row['syntheticLower']==pytest.approx(602.5)
    assert row['syntheticUpper']==pytest.approx(603.2)
    assert result['modes']['ASK']['rawMean']<result['modes']['BID']['rawMean']
@pytest.mark.parametrize('cb,pb,expected',[('BID','ASK',602.5),('ASK','BID',603.2),('LAST','LAST',602.9)])
def test_custom(cfg,sample,cb,pb,expected):
    cfg.callPriceBasis=cb; cfg.putPriceBasis=pb
    assert compute(cfg,sample())['modes']['CUSTOM']['rawMean']==pytest.approx(expected)
def test_nearest_decimal_and_atm(cfg,sample):
    cfg.nBelow=2;cfg.nAbove=2
    r=compute(cfg,sample((590,595,600,605,610,615),600))
    assert r['composition']==['595','590','605','610']
def test_zero_and_one_side(cfg,sample):
    cfg.nBelow=0
    r=compute(cfg,sample());assert r['status']=='PLEASE_SET_COUNTS';assert r['modes']['MID']['rawMean'] is None
    cfg.nAbove=1
    assert compute(cfg,sample((600,605),601))['composition']==['605']
def test_missing_leg_no_substitution(cfg,sample):
    s=sample((590,600)); s.legs=[l for l in s.legs if l.contract.id!='600P']
    r=compute(cfg,s); assert r['composition']==['600'];assert r['modes']['MID']['rawMean'] is None
    assert r['modes']['MID']['rows'][0]['reasons']==['MISSING_OR_AMBIGUOUS_LEG']
@pytest.mark.parametrize('field,value',[('expiration','2026-10-20'),('multiplier',Decimal(10)),('adjusted',True),('currency','EUR'),('settlement','CASH'),('deliverable','ODD'),('exercise','EUROPEAN')])
def test_no_mismatched_pairs(cfg,sample,field,value):
    s=sample();setattr(s.legs[1].contract,field,value)
    assert compute(cfg,s)['modes']['MID']['rawMean'] is None
def test_weighted_by_strikes():
    rows=[{'value':v,'qualityEligible':True,'side':side} for v,side in [(100,'below'),(102,'below'),(110,'above')]]
    assert summary(rows,3)['rawMean']==104

def test_missing_book_not_last(cfg,sample):
    s=sample();s.legs[0].quote.bid=None
    r=compute(cfg,s)
    assert r['modes']['MID']['rawMean'] is None
    assert r['modes']['LAST']['rawMean'] is not None

def test_reported_mid(cfg,sample):
    s=sample();q=s.legs[0].quote;q.bid=None;q.ask=None;q.reportedMid=4.1;q.reportedMidAt=s.valuationAt
    r=compute(cfg,s)['modes']['MID'];assert r['rawMean']==pytest.approx(602.85);assert r['rows'][0]['syntheticLower'] is None

def test_stale_keeps_raw(cfg,sample):
    s=sample();s.legs[0].quote.bidAt-=timedelta(seconds=10)
    r=compute(cfg,s)['modes']['MID'];assert r['rawMean'] is not None;assert r['validatedMean'] is None

def test_stale_anchor_not_replaced(cfg,sample):
    s=sample();s.underlying.lastAt=None
    r=compute(cfg,s);assert not r['anchorStrictEligible'];assert r['anchor']==601;assert r['status']=='INVALID_SELECTION_ANCHOR'

def test_crossed_book_raw(cfg,sample):
    s=sample();s.legs[0].quote.bid=5
    r=compute(cfg,s)['modes']['MID'];assert r['rawMean'] is not None;assert not r['strictEligible']

def test_timestamp_intraday_and_original_valuation(cfg,sample):
    cfg.timeRule='timestamp';s=sample();r=compute(cfg,s)
    assert r['modes']['MID']['rows'][0]['T']==pytest.approx(5/24/365)
    assert compute(cfg,s)['modes']['MID']['rows'][0]['T']==r['modes']['MID']['rows'][0]['T']

def test_expired(cfg,sample):
    cfg.timeRule='timestamp';s=sample()
    for l in s.legs:l.contract.pricingAt=s.valuationAt-timedelta(seconds=1)
    assert compute(cfg,s)['modes']['MID']['rawMean'] is None

def test_no_expiry_rollover(cfg,sample):
    s=sample();s.valuationAt+=timedelta(days=1)
    assert compute(cfg,s)['status']=='NO_SAME_DAY_EXPIRY'

def test_nan_rejected():
    with pytest.raises(ValidationError): Quote(source='x',last=float('nan'))

def test_units(cfg,sample):
    s=sample();s.legs[0].quote.unit='PER_CONTRACT'
    assert compute(cfg,s)['modes']['MID']['rawMean'] is None

def test_missing_timezone():
    with pytest.raises(ValidationError): Quote(source='x',lastAt='2026-10-19T10:00:00')

def test_excel_through_engine(cfg,sample):
    s=sample((152.5,),157.2);cfg.rate=.05
    for l in s.legs:
        l.quote.bid=l.quote.ask=l.quote.last=6.62 if l.contract.right=='C' else 1.88
    assert compute(cfg,s)['modes']['MID']['rawMean']==pytest.approx(157.19822490096)

def test_negative_mid_input_not_computable(cfg,sample):
    s=sample();s.legs[0].quote.bid=-1
    assert compute(cfg,s)['modes']['MID']['rawMean'] is None
