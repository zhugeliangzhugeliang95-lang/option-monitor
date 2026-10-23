from datetime import datetime, timedelta, timezone
import numpy as np
import statsmodels.api as sm
from scipy.stats import t as student
from statsmodels.stats.multitest import multipletests

def minute_blocks(observations,asof,min_slots=4):
    """One *new* event per planned UTC ten-second slot; only closed minutes."""
    end=asof.replace(second=0,microsecond=0); grouped={}
    for o in observations:
        at=datetime.fromisoformat(o['at']); minute=at.replace(second=0,microsecond=0)
        if at>=end or not o['eligible'] or o['dBps'] is None: continue
        grouped.setdefault(minute,{}).setdefault(at.second//10,o['dBps'])
    return {m:dict(at=m.isoformat(),slots=len(v),value=float(np.mean(list(v.values()))) if len(v)>=min_slots else None) for m,v in grouped.items()}

def continuous_values(blocks,asof,maximum=60):
    cursor=asof.replace(second=0,microsecond=0)-timedelta(minutes=1); out=[]
    while len(out)<maximum:
        row=blocks.get(cursor)
        if not row or row['value'] is None: break
        out.append(row['value']); cursor-=timedelta(minutes=1)
    return list(reversed(out))

def hac_test(values,alternative='greater',delta=0,alpha=.05,lag=5,minimum=30):
    n=len(values)
    base=dict(method='HAC修正近似t检验，探索性',status='INSUFFICIENT_SAMPLES',n=n,meanBps=None,deltaBps=delta,
        se=None,t=None,df=n-1 if n else None,p=None,holmP=None,lower=None,upper=None,alpha=alpha,alternative=alternative,lag=lag)
    if n<minimum: return base
    y=np.asarray(values,dtype=float)
    if not np.isfinite(y).all(): return {**base,'status':'NONFINITE'}
    fit=sm.OLS(y,np.ones((n,1))).fit(cov_type='HAC',cov_kwds={'maxlags':lag,'kernel':'bartlett','use_correction':True},use_t=True)
    mu=float(fit.params[0]); se=float(fit.bse[0]); base.update(meanBps=mu,se=se)
    if not np.isfinite(se) or se<1e-10: return {**base,'status':'ZERO_SE'}
    stat=(mu-delta)/se; df=n-1
    if alternative=='greater': p=student.sf(stat,df); lo=mu-student.ppf(1-alpha,df)*se; hi=None
    elif alternative=='less': p=student.cdf(stat,df); lo=None; hi=mu+student.ppf(1-alpha,df)*se
    else: p=2*student.sf(abs(stat),df); q=student.ppf(1-alpha/2,df)*se; lo=mu-q; hi=mu+q
    return {**base,'status':'OK','t':float(stat),'p':float(p),'lower':float(lo) if lo is not None else None,'upper':float(hi) if hi is not None else None}

def analyze(observations,asof,cfg):
    blocks=minute_blocks(observations,asof,cfg.minSlots); vals=continuous_values(blocks,asof,cfg.maxMinutes)
    result=hac_test(vals,cfg.statsAlternative,cfg.deltaBps,cfg.alpha,5,cfg.minMinutes)
    sensitivity=[hac_test(vals,cfg.statsAlternative,cfg.deltaBps,cfg.alpha,k,cfg.minMinutes) for k in (1,5,10)]
    decisions=[r['p']<cfg.alpha for r in sensitivity if r['p'] is not None]
    result.update(statsAsOf=asof.isoformat(),windowMinutes=cfg.maxMinutes,minMinutes=cfg.minMinutes,sensitivity=sensitivity,
                  sensitive=len(set(decisions))>1,blocks=list(blocks.values()),callBasis=cfg.callPriceBasis if cfg.optionPricePreset=='CUSTOM' else cfg.optionPricePreset,
                  putBasis=cfg.putPriceBasis if cfg.optionPricePreset=='CUSTOM' else cfg.optionPricePreset,stockBasis=cfg.stockReferenceBasis,
                  warning='置信界描述窗口平均价差，不是未来收盘价；Holm不控制全天持续查看及反复改参数的全部错误率；不显著不等于价格相等。HAC不修复错误行情、结构突变或盘口偏差。')
    return result

def holm(results):
    valid=[r for r in results if r.get('p') is not None]
    if valid:
        adjusted=multipletests([r['p'] for r in valid],method='holm')[1]
        for r,p in zip(valid,adjusted): r['holmP']=float(p)
    return results
