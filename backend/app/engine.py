"""Pure price/quality engine. Never accesses a clock or provider."""
from datetime import datetime
from decimal import Decimal
from math import exp, isfinite
from statistics import mean, median, stdev
from zoneinfo import ZoneInfo
from .models import Config, Quote, Snapshot
MODEL_VERSION = 'parity-base-v1'
BASES = ['BID','ASK','MID','LAST','CUSTOM']
def price_selector(q: Quote, basis: str):
    if q.unit != 'PER_SHARE': return None
    if basis=='MID':
        if q.bid is not None and q.ask is not None:
            v=(q.bid+q.ask)/2 if q.bid>=0 and q.ask>=0 else None
        elif q.feedType=='MANUAL' and q.reportedMid is not None: v=q.reportedMid
        else: return None
    else: v=getattr(q,basis.lower())
    return v if v is not None and isfinite(v) and v>=0 else None

def event_times(q,basis):
    if basis=='MID':
        if q.bid is not None and q.ask is not None: return [q.bidAt,q.askAt]
        return [q.reportedMidAt]
    return [getattr(q,basis.lower()+'At')]

def quality(q,basis,at,cfg):
    errors=[]
    if q.feedType not in ('REALTIME','MOCK','MANUAL'): errors.append('INELIGIBLE_FEED_'+q.feedType)
    if q.bid is not None and q.ask is not None and q.bid>q.ask: errors.append('CROSSED_BOOK')
    if any(v is not None and v<0 for v in (q.bid,q.ask,q.last,q.bidSize,q.askSize,q.lastSize)): errors.append('NEGATIVE_VALUE')
    if price_selector(q,basis) is None: errors.append('MISSING_'+basis)
    for t in event_times(q,basis):
        if t is None: errors.append('MISSING_EVENT_TIME')
        elif (at-t).total_seconds()>cfg.maxAgeSeconds: errors.append('STALE_QUOTE')
        elif (at-t).total_seconds()<-.1: errors.append('FUTURE_QUOTE')
    return errors

def selected_strikes(contracts,spot,nb,na):
    keys=sorted(set(c.strike for c in contracts))
    s=Decimal(str(spot))
    # A strike exactly equal to the anchor (Decimal compare, so 450 == 450.0) is included as the
    # at-the-money strike in addition to nb below and na above; otherwise selection is unchanged.
    at=[(k,'at') for k in keys if k==s and (nb or na)]
    return [(k,'below') for k in sorted((k for k in keys if k<s),reverse=True)[:nb]]+at+[(k,'above') for k in keys if k>s][:na]

def summary(rows,requested):
    vals=[r['value'] for r in rows if r['value'] is not None]
    valid=[r['value'] for r in rows if r['qualityEligible']]
    def avg(side):
        a=[r['value'] for r in rows if r['side']==side and r['value'] is not None]
        return mean(a) if a else None
    return dict(rawMean=mean(vals) if vals else None,validatedMean=mean(valid) if valid else None,
        # 'at' rows count in rawMean/validatedMean/std/median/min/max but in neither side mean.
        belowMean=avg('below'),aboveMean=avg('above'),atValue=next((r['value'] for r in rows if r['side']=='at'),None),validCount=len(valid),computableCount=len(vals),requestedCount=requested,
        std=stdev(vals) if len(vals)>1 else (0 if vals else None),median=median(vals) if vals else None,
        min=min(vals) if vals else None,max=max(vals) if vals else None)

def compute(cfg:Config,s:Snapshot):
    req=cfg.nBelow+cfg.nAbove
    localday=s.valuationAt.astimezone(ZoneInfo(cfg.exchangeTimezone)).date().isoformat()
    expiry=localday if cfg.expiryMode=='sameDay' else cfg.expiration
    catalog=s.contracts or [l.contract for l in s.legs]
    catalog=[c for c in catalog if c.underlyingId==cfg.underlyingId and c.expiration==expiry]
    anchor=price_selector(s.underlying,'LAST')
    anchor_errors=quality(s.underlying,'LAST',s.valuationAt,cfg)
    selected=selected_strikes(catalog,anchor,cfg.nBelow,cfg.nAbove) if anchor is not None else []
    if any(side=='at' for _,side in selected): req+=1
    ref=price_selector(s.underlying,cfg.stockReferenceBasis)
    modes={}
    for mode in BASES:
        cb=cfg.callPriceBasis if mode=='CUSTOM' else mode
        pb=cfg.putPriceBasis if mode=='CUSTOM' else mode
        rows=[]
        for k,side in selected:
            errors=[]; fatal=[]
            legs=[l for l in s.legs if l.contract.underlyingId==cfg.underlyingId and l.contract.expiration==expiry and l.contract.strike==k]
            calls=[l for l in legs if l.contract.right=='C']; puts=[l for l in legs if l.contract.right=='P']
            c=calls[0] if len(calls)==1 else None; p=puts[0] if len(puts)==1 else None
            value=lower=upper=T=None
            if not c or not p: fatal.append('MISSING_OR_AMBIGUOUS_LEG')
            if c and p:
                for field in ('underlyingId','expiration','strike','currency','exercise','settlement','multiplier','deliverable','adjusted','pricingAt','lastTradingAt'):
                    if getattr(c.contract,field)!=getattr(p.contract,field): fatal.append('CONTRACT_MISMATCH_'+field)
                if c.contract.currency!=cfg.currency: fatal.append('CURRENCY_MISMATCH')
                if c.contract.adjusted or p.contract.adjusted: fatal.append('ADJUSTED_UNSUPPORTED')
                if c.quote.unit!='PER_SHARE' or p.quote.unit!='PER_SHARE': fatal.append('UNIT_ERROR')
                if cfg.timeRule=='days365': T=cfg.remainingDays/365
                else:
                    end=c.contract.pricingAt or cfg.pricingAt
                    if not end: fatal.append('MISSING_PRICING_TIME')
                    elif not c.contract.pricingAt and not cfg.timeAssumption: fatal.append('EXPLICIT_TIME_ASSUMPTION_REQUIRED')
                    else:
                        T=(end-s.valuationAt).total_seconds()/(365*86400)
                        if T<0: fatal.append('EXPIRED')
                cv=price_selector(c.quote,cb); pv=price_selector(p.quote,pb)
                errors+=quality(c.quote,cb,s.valuationAt,cfg)+quality(p.quote,pb,s.valuationAt,cfg)
                times=event_times(c.quote,cb)+event_times(p.quote,pb)+event_times(s.underlying,cfg.stockReferenceBasis)+[s.underlying.lastAt]
                times=[t for t in times if t is not None]
                if times and (max(times)-min(times)).total_seconds()>cfg.maxSkewSeconds: errors.append('TIME_SKEW')
                if not fatal and T is not None:
                    pk=float(k)*exp(-cfg.rate*T)
                    if cv is not None and pv is not None: value=cv-pv+pk
                    b1=price_selector(c.quote,'BID'); a1=price_selector(c.quote,'ASK'); b2=price_selector(p.quote,'BID'); a2=price_selector(p.quote,'ASK')
                    if b1 is not None and a2 is not None: lower=b1-a2+pk
                    if a1 is not None and b2 is not None: upper=a1-b2+pk
            if c and p and len({c.quote.source,p.quote.source,s.underlying.source})>1: errors.append('SOURCE_MISMATCH')
            if c and p and len({c.quote.feedType,p.quote.feedType,s.underlying.feedType})>1: errors.append('FEED_MISMATCH')
            errors+=fatal+anchor_errors+quality(s.underlying,cfg.stockReferenceBasis,s.valuationAt,cfg)
            row=dict(strike=str(k),side=side,value=value,T=T,computable=value is not None,qualityEligible=value is not None and not errors,
                     reasons=sorted(set(errors)),syntheticLower=lower,syntheticUpper=upper,
                     grossGapHigh=lower-s.underlying.ask if lower is not None and s.underlying.ask is not None else None,
                     grossGapLow=s.underlying.bid-upper if upper is not None and s.underlying.bid is not None else None,
                     call=c.model_dump(mode='json') if c else None,put=p.model_dump(mode='json') if p else None,
                     adjustedValue=value+cfg.dividendPV if value is not None and cfg.dividendPV is not None else None)
            rows.append(row)
        a=summary(rows,req)
        strict=req>0 and len(rows)==req and a['validCount']==req and ref is not None and ref>0 and s.marketOpen and s.optionSessionOpen
        a.update(rawGapBps=10000*(a['rawMean']-ref)/ref if a['rawMean'] is not None and ref is not None and ref>0 else None,rows=rows,strictEligible=strict,dBps=10000*(a['validatedMean']-ref)/ref if strict else None,callBasis=cb,putBasis=pb,stockBasis=cfg.stockReferenceBasis)
        modes[mode]=a
    status='PLEASE_SET_COUNTS' if req==0 else ('NO_SAME_DAY_EXPIRY' if not catalog else ('MISSING_ANCHOR' if anchor is None else 'OK'))
    if status=='OK' and anchor_errors: status='INVALID_SELECTION_ANCHOR'
    if status=='OK' and any('EXPIRED' in r['reasons'] for r in modes[cfg.optionPricePreset]['rows']): status='EXPIRED'
    if status=='OK' and s.provider=='manual': status='MANUAL_PRICE_ONLY'
    if status=='OK' and (not s.marketOpen or not s.optionSessionOpen): status='CLOSED_OR_UNKNOWN_SESSION'
    if status=='OK' and not modes[cfg.optionPricePreset]['strictEligible']: status='PARTIAL_OR_INELIGIBLE'
    return dict(modelVersion=MODEL_VERSION,valuationAt=s.valuationAt.isoformat(),expiration=expiry,tradingDay=localday,
        status=status,anchor=anchor,anchorStrictEligible=not anchor_errors,stockReference=ref,modes=modes,
        composition=[str(k) for k,_ in selected],feedType=s.underlying.feedType,marketReason=s.marketReason,
        warnings=['人工假设，非实时利率','盘口区间不是统计置信区间；grossGap不是保证可实现的套利收益']+
        (['简化平价近似，提前行权未建模'] if any(c.exercise=='AMERICAN' for c in catalog) else [])+
        (['未知股息，未作股息修正'] if cfg.dividendPV is None else ['独立股息修正版 parity-dividend-v1']))
