from datetime import timedelta
from sqlalchemy import select
from app.db import *
from app.models import Config, Snapshot
from app.engine import BASES
from app.service import create_monitor, save_snapshot, statistics_for, config_for
from app.statistics import analyze

def legacy_statistics_for(db,m,asof,mode=None):
    """Pre-json_extract implementation, kept verbatim as a reference."""
    rc=config_for(db,m); cfg=Config.model_validate(rc.payload)
    if mode: cfg=cfg.model_copy(update={'optionPricePreset':mode})
    rows=db.scalars(select(AggregateResult).where(AggregateResult.monitor_id==m.id,AggregateResult.config_id==rc.id).order_by(AggregateResult.id.desc()).limit(3000)).all()
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

NEW_KEYS={'reason','lastGapAt','consecutiveMinutes','neededMinutes','minSlotsPerMinute'}
def same(a,b):
    # New diagnostic fields are additive; every legacy field must still match exactly.
    assert a.keys()==b.keys()|NEW_KEYS
    for k in b:
        assert a[k]==b[k] and type(a[k])==type(b[k]), k

def test_extract_matches_legacy_field_by_field(cfg,sample,at):
    with Session() as db:
        m=create_monitor(db,cfg)
        same(statistics_for(db,m,at),legacy_statistics_for(db,m,at))  # empty
        for i in range(400):
            base=sample(spot=601+(i%7)*.01); t=(at+timedelta(seconds=10*i)).strftime('%Y-%m-%dT%H:%M:%S')
            s=Snapshot.model_validate_json(base.model_dump_json().replace('2026-10-19T15:00:00',t)); s.monitorId=m.id; s.historical=i<5
            save_snapshot(db,s)
        asof=at+timedelta(minutes=67)
        for mode in [None,*BASES]:
            new=statistics_for(db,m,asof,mode); old=legacy_statistics_for(db,m,asof,mode)
            same(new,old)
        assert new['status']=='OK' and new['n']>=30 and new['meanBps'] is not None
