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

# Collection is treated as stopped when the last closed minute(s) before asof received no observation at all.
STALE_AFTER_MINUTES=1
REASON_TEXT={
    'OK':'统计可用 / statistics available',
    'NO_DATA':'暂无数据 / no data collected yet',
    'DATA_STALE':'数据已停止更新 / data collection stopped',
    'GAP_IN_WINDOW':'窗口内有缺口，连续分钟不足 / gap in window, not enough consecutive minutes',
    'TOO_FEW_MINUTES':'连续分钟不足 / not enough consecutive minutes yet',
    'MISSING_EVENT_TIME':'行情缺少事件时间，不满足严格条件 / quotes missing event time, not strict-eligible',
    'NOT_STRICT_ELIGIBLE':'观测不满足严格条件 / observations not strict-eligible',
    'ZERO_SE':'标准误为零 / zero standard error',
    'NONFINITE':'存在非有限值 / non-finite values',
}

def explain(observations,blocks,result,asof,cfg,reason_counts=None):
    """Diagnostic fields only; never changes the statistic or eligibility. reason_counts(indexes) -> (row reasons, statuses) Counters for those observations."""
    end=asof.replace(second=0,microsecond=0); start=end-timedelta(minutes=cfg.maxMinutes)
    streak=0; cursor=end-timedelta(minutes=1); gap=None
    while streak<cfg.maxMinutes:
        row=blocks.get(cursor)
        if not row or row['value'] is None: gap=cursor; break
        streak+=1; cursor-=timedelta(minutes=1)
    out=dict(consecutiveMinutes=streak,neededMinutes=cfg.minMinutes,minSlotsPerMinute=cfg.minSlots,
             lastGapAt=gap.astimezone(timezone.utc).isoformat() if gap else None)
    details={}; status=result['status']
    if status in ('OK','ZERO_SE','NONFINITE'): code=status
    else:
        times=[datetime.fromisoformat(o['at']) for o in observations]
        idx=[i for i,t in enumerate(times) if start<=t<end]; window=[observations[i] for i in idx]
        last=max(times) if times else None
        if not observations: code='NO_DATA'
        elif last<end-timedelta(minutes=STALE_AFTER_MINUTES):
            code='DATA_STALE'; details=dict(lastDataAt=last.astimezone(timezone.utc).isoformat(),minutesSinceLastData=int((end-last).total_seconds()//60))
        elif window and not any(o['eligible'] and o['dBps'] is not None for o in window):
            reasons,statuses=reason_counts(idx) if reason_counts else ({},{})
            top=sorted(reasons.items(),key=lambda kv:(-kv[1],kv[0]))[:3]
            code='MISSING_EVENT_TIME' if top and top[0][0]=='MISSING_EVENT_TIME' else 'NOT_STRICT_ELIGIBLE'
            details=dict(ineligibleRows=len(window),topReasons=[dict(reason=k,count=v) for k,v in top],
                         statuses=[dict(status=k,count=v) for k,v in sorted(statuses.items(),key=lambda kv:(-kv[1],kv[0]))[:3]])
        else:
            mins=[start+timedelta(minutes=i) for i in range(cfg.maxMinutes)]
            details=dict(missingMinutes=sum(1 for m in mins if m not in blocks),underfilledMinutes=sum(1 for m in mins if m in blocks and blocks[m]['value'] is None))
            # A usable minute before the gap means the run was broken; otherwise collection simply has not run long enough.
            code='GAP_IN_WINDOW' if gap is not None and any(m<gap and b['value'] is not None for m,b in blocks.items()) else 'TOO_FEW_MINUTES'
            details.update(n=streak,needed=cfg.minMinutes)
    out['reason']=dict(code=code,message=REASON_TEXT[code],**({'details':details} if details else {}))
    return out

def holm(results):
    valid=[r for r in results if r.get('p') is not None]
    if valid:
        adjusted=multipletests([r['p'] for r in valid],method='holm')[1]
        for r,p in zip(valid,adjusted): r['holmP']=float(p)
    return results
