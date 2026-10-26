import json, uuid
from datetime import datetime, timedelta
from sqlalchemy import select, func, text
from .db import *
from .models import Config, Snapshot
from .engine import compute, BASES
from collections import Counter
from .statistics import analyze, explain, holm

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
    # Pair rows live in the aggregate payload (modes[*].rows); pair_results is legacy/read-only now.
    db.add(record(AggregateResult,m.id,rc.id,result,snapshot_id=raw.id))
    db.add(ResultOutbox(payload={'type':'result','monitorId':m.id,'snapshotId':raw.id},created_at=nowstr()))
    db.commit(); return result

STREAM_KEYS=('tradingDay','expiration','feedType','source','historical')
STAT_KEYS=('id','valuationAt','strictEligible','dBps')+STREAM_KEYS
def stat_query(mid,cid,mode,limit=3000):
    # Only the fields statistics need, via SQLite json_extract (avoids decoding ~55KB payloads per row).
    j=lambda path: func.json_extract(AggregateResult.payload,path)
    m=mode.replace('"','')
    cols=[AggregateResult.id,j('$.valuationAt'),j(f'$.modes."{m}".strictEligible'),j(f'$.modes."{m}".dBps')]+[j(f'$.{k}') for k in STREAM_KEYS]
    return select(*cols).where(AggregateResult.monitor_id==mid,AggregateResult.config_id==cid).order_by(AggregateResult.id.desc()).limit(limit)

def statistics_for(db,m,asof,mode=None):
    rc=config_for(db,m); cfg=Config.model_validate(rc.payload)
    if mode: cfg=cfg.model_copy(update={'optionPricePreset':mode})
    rows=[dict(zip(STAT_KEYS,r)) for r in db.execute(stat_query(m.id,rc.id,cfg.optionPricePreset)).all()]
    # One source/feed, expiry, trade day and historical designation per stream.
    latest=rows[0] if rows else None
    if latest: latest['historical']=bool(latest['historical'])
    observations=[]; ids=[]
    for r in reversed(rows):
        if r['historical'] is not None: r['historical']=bool(r['historical'])
        if latest and any(r.get(k)!=latest.get(k) for k in STREAM_KEYS): continue
        observations.append({'at':r['valuationAt'],'eligible':bool(r['strictEligible']),'dBps':r['dBps']}); ids.append(r['id'])
    stat=analyze(observations,asof,cfg)
    blocks={datetime.fromisoformat(b['at']):b for b in stat['blocks']}
    stat.update(explain(observations,blocks,stat,asof,cfg,lambda window:reason_counts(db,[ids[i] for i in window],cfg.optionPricePreset)))
    stat.update(configId=rc.id,configHash=rc.hash,configVersion=rc.version,monitorId=m.id,mode=cfg.optionPricePreset,
                feedType=latest['feedType'] if latest else None,historical=latest['historical'] if latest else False,
                family=cfg.family,primary=mode is None or mode==rc.payload['optionPricePreset'])
    return stat

def reason_counts(db,ids,mode):
    """Most common stored ineligibility reasons for the given aggregate rows (SQL json_each, no payload decoding)."""
    m=mode.replace('"','').replace("'",'')
    if not ids: return Counter(),Counter(),None
    marks=','.join(str(int(i)) for i in ids)
    reasons=db.execute(text(f"""select r.value,count(*) from aggregate_results a,
        json_each(json_extract(a.payload,'$.modes."{m}".rows')) row, json_each(json_extract(row.value,'$.reasons')) r
        where a.id in ({marks}) group by r.value""")).all()
    statuses=db.execute(text(f"""select json_extract(payload,'$.status'),count(*) from aggregate_results
        where id in ({marks}) and json_extract(payload,'$.status')!='OK' group by 1""")).all()
    # Session flag of the newest row in the window (raw snapshot; one row only).
    market=db.execute(text(f"""select json_extract(r.payload,'$.marketOpen') from aggregate_results a join raw_snapshots r on r.id=a.snapshot_id
        where a.id={max(int(i) for i in ids)}""")).scalar()
    return Counter(dict(reasons)),Counter(dict(statuses)),(None if market is None else bool(market))

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

SUMMARY_SCALARS=('snapshotId','valuationAt','configId','configVersion','feedType','historical','duplicate','status','stockReference','composition')
SUMMARY_MODE_KEYS=('rawMean','validatedMean','rawGapBps')
def summary_columns():
    """Compact history record (~1% of the full payload) built from json_extract columns; values come from SQLite as exact Python scalars."""
    j=lambda path: func.json_extract(AggregateResult.payload,path)
    cols=[AggregateResult.id]+[j(f'$.{k}') for k in SUMMARY_SCALARS]+[func.json_type(AggregateResult.payload,'$.stockReference')]
    cols+=[j(f'$.modes."{m}".{k}') for m in BASES for k in SUMMARY_MODE_KEYS]+[func.json_type(AggregateResult.payload,f'$.modes."{m}"') for m in BASES]
    return cols
def summary_row(row):
    row=list(row); out={'id':row[0],**dict(zip(SUMMARY_SCALARS,row[1:11]))}; rest=row[12:]
    for k in ('historical','duplicate'):
        if out[k] is not None: out[k]=bool(out[k])
    if row[11] in ('object','array'): out['stockReference']=json.loads(out['stockReference'])
    out['composition']=json.loads(out['composition']) if out['composition'] is not None else None
    n=len(SUMMARY_MODE_KEYS); present=rest[len(BASES)*n:]
    out['modes']={m:dict(zip(SUMMARY_MODE_KEYS,rest[i*n:(i+1)*n])) for i,m in enumerate(BASES) if present[i]=='object'}
    return out
