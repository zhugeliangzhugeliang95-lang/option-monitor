import uuid
from datetime import datetime, timedelta
from sqlalchemy import select
from .db import *
from .models import Config, Snapshot
from .engine import compute, BASES
from .statistics import analyze, holm

def config_for(db,monitor): return db.get(RunConfig,monitor.config_id)
def create_monitor(db,config):
    mid=uuid.uuid4().hex; cid=uuid.uuid4().hex; payload=config.model_dump(mode='json')
    db.add(RunConfig(id=cid,monitor_id=mid,version=1,hash=digest(payload),payload=payload,created_at=nowstr()))
    m=Monitor(id=mid,config_id=cid,active=True); db.add(m); db.commit(); return m

def revise(db,m,patch):
    old=config_for(db,m); cfg=Config.model_validate({**old.payload,**patch}); payload=cfg.model_dump(mode='json')
    if digest(payload)==old.hash: return old
    rc=RunConfig(id=uuid.uuid4().hex,monitor_id=m.id,version=old.version+1,hash=digest(payload),payload=payload,created_at=nowstr())
    db.add(rc); m.config_id=rc.id; db.commit(); return rc

def fingerprint(s):
    data=s.model_dump(mode='json'); data.pop('valuationAt'); data.pop('historical')
    for q in [data['underlying']]+[x['quote'] for x in data['legs']]:
        q.pop('receivedAt',None); q.pop('ingestedAt',None)
    return digest(data)

def save_snapshot(db,s,config_id=None):
    m=db.get(Monitor,s.monitorId)
    if not m or not m.active: raise ValueError('监测对象不存在或已停用')
    rc=db.get(RunConfig,config_id or m.config_id); cfg=Config.model_validate(rc.payload)
    expected={'manual':'MANUAL','mock':'MOCK','http':None,'futu':None}[cfg.provider]
    if s.provider!=cfg.provider: raise ValueError('行情源与监测流不匹配')
    feeds={s.underlying.feedType}|{l.quote.feedType for l in s.legs}
    if expected and feeds!={expected}: raise ValueError('人工/API/MOCK 数据流不可混合')
    if cfg.provider in ('http','futu') and ('MANUAL' in feeds or len(feeds)>1): raise ValueError('联网行情类型不一致')
    fp=fingerprint(s)
    duplicate=db.scalar(select(RawSnapshot).where(RawSnapshot.monitor_id==m.id,RawSnapshot.config_id==rc.id,RawSnapshot.fingerprint==fp).limit(1)) is not None
    result=compute(cfg,s); result.update(configId=rc.id,configHash=rc.hash,configVersion=rc.version,monitorId=m.id,duplicate=duplicate,historical=s.historical,source=s.underlying.source)
    previous=db.scalar(select(AggregateResult).where(AggregateResult.monitor_id==m.id,AggregateResult.config_id==rc.id).order_by(AggregateResult.id.desc()).limit(1))
    result['compositionChanged']=bool(previous and previous.payload['composition']!=result['composition'])
    result['previousComposition']=previous.payload['composition'] if previous else []
    for a in result['modes'].values():
        if duplicate: a['strictEligible']=False; a['dBps']=None
    raw=record(RawSnapshot,m.id,rc.id,s.model_dump(mode='json'),fingerprint=fp); db.add(raw); db.flush()
    result['snapshotId']=raw.id
    db.add(record(PairResult,m.id,rc.id,{k:v['rows'] for k,v in result['modes'].items()},snapshot_id=raw.id))
    db.add(record(AggregateResult,m.id,rc.id,result,snapshot_id=raw.id))
    db.add(ResultOutbox(payload={'type':'result','monitorId':m.id,'snapshotId':raw.id},created_at=nowstr()))
    db.commit(); return result

def statistics_for(db,m,asof,mode=None):
    rc=config_for(db,m); cfg=Config.model_validate(rc.payload)
    if mode: cfg=cfg.model_copy(update={'optionPricePreset':mode})
    rows=db.scalars(select(AggregateResult).where(AggregateResult.monitor_id==m.id,AggregateResult.config_id==rc.id).order_by(AggregateResult.id.desc()).limit(3000)).all()
    # One source/feed, expiry, trade day and historical designation per stream.
    latest=rows[0].payload if rows else None
    observations=[]
    for row in reversed(rows):
        r=row.payload; a=r['modes'][cfg.optionPricePreset]
        if latest and any(r.get(k)!=latest.get(k) for k in ('tradingDay','expiration','feedType','source','historical')): continue
        observations.append({'at':r['valuationAt'],'eligible':a['strictEligible'],'dBps':a['dBps']})
    stat=analyze(observations,asof,cfg)
    stat.update(configId=rc.id,configHash=rc.hash,configVersion=rc.version,monitorId=m.id,mode=cfg.optionPricePreset,
                feedType=latest['feedType'] if latest else None,historical=latest['historical'] if latest else False,
                family=cfg.family,primary=mode is None or mode==rc.payload['optionPricePreset'])
    return stat

def persist_statistics(db,asof):
    groups={}; results=[]
    for m in db.scalars(select(Monitor).where(Monitor.active==True)):
        stat=statistics_for(db,m,asof)
        groups.setdefault((stat['family'],stat['feedType'],stat['historical']),[]).append(stat); results.append((m,stat))
    for family in groups.values():
        holm(family)
        for s in family: s['familyMembers']=[x['monitorId'] for x in family]
    for m,s in results:
        db.add(record(StatisticalResult,m.id,m.config_id,s))
        # Closed block snapshots are append-only audit revisions, never overwritten.
        db.add(record(MinuteBlock,m.id,m.config_id,{'statsAsOf':asof.isoformat(),'blocks':s['blocks']}))
    db.add(ResultOutbox(payload={'type':'statistics','statsAsOf':asof.isoformat()},created_at=nowstr())); db.commit()
