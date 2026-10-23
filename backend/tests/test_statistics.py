from datetime import timedelta
import numpy as np
import pytest
import statsmodels.api as sm
from scipy.stats import t
from statsmodels.stats.multitest import multipletests
from app.statistics import *
from app.models import Config

def test_closed_minutes_slots_gaps(at):
    obs=[]
    for minute in range(35):
        for slot in range(6):
            obs.append({'at':(at+timedelta(minutes=minute,seconds=slot*10)).isoformat(),'eligible':True,'dBps':minute+slot/10})
    end=at+timedelta(minutes=35)
    b=minute_blocks(obs,end);assert len(continuous_values(b,end))==35
    # Duplicate arrival in a slot does not add coverage.
    dup=obs[:1]*10
    assert list(minute_blocks(dup,at+timedelta(minutes=1)).values())[0]['value'] is None
    assert len(minute_blocks(obs,end-timedelta(seconds=1)))==34
    b.pop(at+timedelta(minutes=30));assert len(continuous_values(b,end))==4

def test_missing_latest_minute(at):
    assert continuous_values({at:{'value':1}},at+timedelta(minutes=2))==[]

def test_hac_exact_reference():
    y=np.random.default_rng(42).normal(2,4,50)
    fit=sm.OLS(y,np.ones((50,1))).fit(cov_type='HAC',cov_kwds={'maxlags':5,'kernel':'bartlett','use_correction':True},use_t=True)
    r=hac_test(y,delta=.2)
    assert r['se']==pytest.approx(fit.bse[0]);assert r['t']==pytest.approx((fit.params[0]-.2)/fit.bse[0]);assert r['p']==pytest.approx(t.sf(r['t'],49))

@pytest.mark.parametrize('tail',['greater','less','two-sided'])
def test_tails_and_bounds(tail):
    y=np.random.default_rng(55).normal(1,2,40);r=hac_test(y,tail)
    if tail=='greater': assert r['upper'] is None and r['lower']<r['meanBps']
    elif tail=='less': assert r['lower'] is None and r['upper']>r['meanBps']
    else: assert r['lower']<r['meanBps']<r['upper']
    greater=hac_test(y,'greater');less=hac_test(y,'less');assert greater['p']+less['p']==pytest.approx(1)

def test_insufficient_zero_and_nonfinite():
    assert hac_test([1]*29)['p'] is None
    assert hac_test([1]*40)['status']=='ZERO_SE'
    assert hac_test([float('inf')]*40)['status']=='NONFINITE'

def test_holm():
    ps=[.01,.04,.2];results=holm([{'p':p} for p in ps]+[{'p':None}])
    assert [r['holmP'] for r in results[:3]]==pytest.approx(multipletests(ps,method='holm')[1])
