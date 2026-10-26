import json, uuid
from sqlalchemy import select, func
from .db import *
from .models import Config, Snapshot
from .engine import compute, BASES

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
    if data.get('selectedExpiration') is None: data.pop('selectedExpiration',None)
    for q in [data['underlying']]+[x['quote'] for x in data['legs']]:
        q.pop('receivedAt',None); q.pop('ingestedAt',None)
    return digest(data)

def save_snapshot(db,s,config_id=None,extra=None):
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
    if extra: result.update(extra)  # e.g. futu chain-stale metadata; kept out of the Snapshot / fingerprint
    # Pair rows live in the aggregate payload (modes[*].rows); pair_results is legacy/read-only now.
    db.add(record(AggregateResult,m.id,rc.id,result,snapshot_id=raw.id))
    db.add(ResultOutbox(payload={'type':'result','monitorId':m.id,'snapshotId':raw.id},created_at=nowstr()))
    db.commit(); return result

SUMMARY_SCALARS=('snapshotId','valuationAt','configId','configVersion','feedType','historical','duplicate','status','stockReference','composition','expiration','expiryMode','pricingReferenceAt')
SUMMARY_MODE_KEYS=('rawMean','validatedMean','rawGapBps')
def summary_columns():
    """Compact history record (~1% of the full payload) built from json_extract columns; values come from SQLite as exact Python scalars."""
    j=lambda path: func.json_extract(AggregateResult.payload,path)
    cols=[AggregateResult.id]+[j(f'$.{k}') for k in SUMMARY_SCALARS]+[func.json_type(AggregateResult.payload,'$.stockReference')]
    cols+=[j(f'$.modes."{m}".{k}') for m in BASES for k in SUMMARY_MODE_KEYS]+[func.json_type(AggregateResult.payload,f'$.modes."{m}"') for m in BASES]
    return cols
def summary_row(row):
    row=list(row); end=1+len(SUMMARY_SCALARS); out={'id':row[0],**dict(zip(SUMMARY_SCALARS,row[1:end]))}; rest=row[end+1:]
    for k in ('historical','duplicate'):
        if out[k] is not None: out[k]=bool(out[k])
    if row[end] in ('object','array'): out['stockReference']=json.loads(out['stockReference'])
    out['composition']=json.loads(out['composition']) if out['composition'] is not None else None
    n=len(SUMMARY_MODE_KEYS); present=rest[len(BASES)*n:]
    out['modes']={m:dict(zip(SUMMARY_MODE_KEYS,rest[i*n:(i+1)*n])) for i,m in enumerate(BASES) if present[i]=='object'}
    return out
