"""Every statistics `reason` code, using seeded aggregate rows (same pattern as test_statistics_extract)."""
from datetime import timedelta
from types import SimpleNamespace
from app.db import *
from app.models import Config, Snapshot
from app.service import create_monitor, save_snapshot, statistics_for
from app.statistics import analyze, explain, minute_blocks, REASON_TEXT

def seed(db,cfg,sample,start,minutes,slots=4,skip=(),vary=True,edit=None):
    m=create_monitor(db,cfg)
    for minute in range(minutes):
        if minute in skip: continue
        for slot in range(slots):
            base=sample(spot=601+((minute*slots+slot)%7)*.01 if vary else 601)
            t=(start+timedelta(minutes=minute,seconds=10*slot)).strftime('%Y-%m-%dT%H:%M:%S')
            s=Snapshot.model_validate_json(base.model_dump_json().replace('2026-10-19T15:00:00',t)); s.monitorId=m.id
            if edit: s=edit(s)
            save_snapshot(db,s)
    return m

def check(stat,code,cfg):
    assert stat['reason']['code']==code and stat['reason']['message']==REASON_TEXT[code]
    assert stat['neededMinutes']==cfg.minMinutes==30 and stat['minSlotsPerMinute']==cfg.minSlots==4
    assert isinstance(stat['consecutiveMinutes'],int)
    return stat

def test_ok_and_no_gap(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,35); s=check(statistics_for(db,m,at+timedelta(minutes=35)),'OK',cfg)
    assert s['status']=='OK' and s['consecutiveMinutes']==35 and s['n']==35
    assert s['lastGapAt']==at.isoformat().replace('15:00','14:59')  # minute before the first seeded one
    assert 'details' not in s['reason']

def test_full_window_has_null_gap(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,62); s=check(statistics_for(db,m,at+timedelta(minutes=62)),'OK',cfg)
    assert s['consecutiveMinutes']==60 and s['lastGapAt'] is None

def test_no_data(cfg,at):
    with Session() as db:
        m=create_monitor(db,cfg); s=check(statistics_for(db,m,at),'NO_DATA',cfg)
    assert s['consecutiveMinutes']==0 and s['status']=='INSUFFICIENT_SAMPLES'
    assert s['lastGapAt'] is None   # nothing collected: no gap to report

def test_data_stale(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,35); s=check(statistics_for(db,m,at+timedelta(minutes=37)),'DATA_STALE',cfg)
    assert s['reason']['details']['minutesSinceLastData']==2 and s['reason']['details']['lastDataAt'].startswith('2026-10-19T15:34:30')
    assert s['lastGapAt']=='2026-10-19T15:36:00+00:00'

def test_gap_in_window(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,40,skip=(25,)); s=check(statistics_for(db,m,at+timedelta(minutes=40)),'GAP_IN_WINDOW',cfg)
    d=s['reason']['details']
    assert d['n']==14 and d['needed']==30 and d['missingMinutes']>=1 and s['lastGapAt']=='2026-10-19T15:25:00+00:00'

def test_underfilled_minutes(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,35,slots=3); s=check(statistics_for(db,m,at+timedelta(minutes=35)),'TOO_FEW_MINUTES',cfg)
    # 3 slots < minSlots: no usable minute at all -> too few minutes, reported as underfilled
    assert s['consecutiveMinutes']==0 and s['reason']['details']['underfilledMinutes']==35

def test_too_few_minutes(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,29); s=check(statistics_for(db,m,at+timedelta(minutes=29)),'TOO_FEW_MINUTES',cfg)
    assert s['reason']['details']=={'n':29,'needed':30,'missingMinutes':31,'underfilledMinutes':0}

def strip_times(s):
    q=lambda x:x.model_copy(update={'bidAt':None,'askAt':None})
    return s.model_copy(update={'underlying':q(s.underlying),'legs':[l.model_copy(update={'quote':q(l.quote)}) for l in s.legs]})

def test_missing_event_time(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,35,edit=strip_times); s=check(statistics_for(db,m,at+timedelta(minutes=35)),'MISSING_EVENT_TIME',cfg)
    d=s['reason']['details']
    assert d['topReasons'][0]['reason']=='MISSING_EVENT_TIME' and d['ineligibleRows']==140
    assert 'marketOpen' not in d

def test_not_strict_eligible(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,35,edit=lambda s:s.model_copy(update={'marketOpen':False}))
        s=check(statistics_for(db,m,at+timedelta(minutes=35)),'NOT_STRICT_ELIGIBLE',cfg)
    d=s['reason']['details']
    # Rows carry no reasons for a closed session; the aggregate status explains it (manual feed status wins in engine).
    assert d['topReasons']==[] and d['ineligibleRows']==140 and d['statuses']==[{'status':'MANUAL_PRICE_ONLY','count':140}]
    assert d['marketOpen'] is False   # session closed is visible to the user

def test_zero_se(cfg,sample,at):
    with Session() as db:
        m=seed(db,cfg,sample,at,35,vary=False); s=check(statistics_for(db,m,at+timedelta(minutes=35)),'ZERO_SE',cfg)
    assert s['status']=='ZERO_SE'

def test_nonfinite(cfg,at):
    # NaN cannot be stored through JSON payloads, so exercise analyze/explain directly.
    obs=[{'at':(at+timedelta(minutes=i,seconds=10*k)).isoformat(),'eligible':True,'dBps':float('nan') if i==3 else 1.0+i%3} for i in range(35) for k in range(4)]
    asof=at+timedelta(minutes=35); r=analyze(obs,asof,cfg)
    s=check({**r,**explain(obs,minute_blocks(obs,asof,cfg.minSlots),r,asof,cfg)},'NONFINITE',cfg)
    assert s['status']=='NONFINITE'

def test_constants_come_from_config(sample,at):
    cfg=Config(nBelow=1,nAbove=0,rate=0,timeRule='days365',remainingDays=2,minMinutes=45,minSlots=5)
    with Session() as db:
        m=create_monitor(db,cfg); s=statistics_for(db,m,at)
    assert s['neededMinutes']==45 and s['minSlotsPerMinute']==5
