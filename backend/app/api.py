import asyncio, csv, io, json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from pydantic import ValidationError
from sqlalchemy import select, func
from .db import *
from .models import Config, NewConfig, Snapshot, check_symbol, utcnow
from .engine import compute, BASES
from .service import config_for, create_monitor, revise, save_snapshot, statistics_for, summary_columns, summary_row
from .providers import provider_for, ProviderError
from .imports import parse_import
from . import models

LOCAL_ORIGINS=[f'http://127.0.0.1:{os.getenv("FRONTEND_PORT","3000")}',f'http://localhost:{os.getenv("FRONTEND_PORT","3000")}',f'http://127.0.0.1:{os.getenv("BACKEND_PORT","8000")}']
subscribers=set()
RESYNC={'resync':True}
PING_SECONDS=10
REPLAY_BATCH=200
def publish(queue,item):
    # A subscriber that cannot keep up loses its backlog and is told to resync instead of silently skipping events.
    if queue.full():
        while not queue.empty(): queue.get_nowait()
        queue.put_nowait(RESYNC)
    elif item is not RESYNC: queue.put_nowait(item)
def sse(item): return f'id: {item["id"]}\ndata: {json.dumps(item)}\n\n'
RESYNC_EVENT='event: resync\ndata: {"status":"resync"}\n\n'
PING_EVENT='event: ping\ndata: {}\n\n'
async def broadcast():
    with Session() as db: cursor=db.scalar(select(func.max(ResultOutbox.id))) or 0
    while True:
        with Session() as db: rows=db.scalars(select(ResultOutbox).where(ResultOutbox.id>cursor).order_by(ResultOutbox.id).limit(200)).all()
        for row in rows:
            cursor=row.id
            for queue in list(subscribers): publish(queue,{'id':row.id,**row.payload})
        await asyncio.sleep(.5)
@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(broadcast())
    yield
    task.cancel(); await asyncio.gather(task,return_exceptions=True)
from .logs import configure as _configure_logging
_configure_logging()  # uvicorn configures its loggers before importing the app; wrap them with redaction
app=FastAPI(title='期权反推价格监测台',version='1.0.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=LOCAL_ORIGINS,allow_methods=['*'],allow_headers=['*'])
@app.middleware('http')
async def local_origin(request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin') not in [None,*LOCAL_ORIGINS]:
        return Response('Origin not allowed',status_code=403)
    return await call_next(request)
@app.exception_handler(ValueError)
async def value_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422,content={'detail':str(exc)[:600]})
def monitor(db,mid):
    m=db.get(Monitor,mid)
    if not m: raise HTTPException(404,'监测对象不存在')
    return m

def monitor_dict(db,m):
    rc=db.get(RunConfig,m.config_id)
    return {'id':m.id,'active':m.active,'version':rc.version,'hash':rc.hash,'configId':rc.id,'config':rc.payload}
@app.get('/api/health')
def health(): return {'status':'OK','service':'api','at':nowstr()}
@app.get('/api/monitors')
def monitors():
    with Session() as db: return [monitor_dict(db,m) for m in db.scalars(select(Monitor).where(Monitor.active==True))]
@app.post('/api/monitors')
def add_monitor(cfg:NewConfig):
    for f in ('symbol','underlyingId'): check_symbol(getattr(cfg,f),f)
    cfg=Config.model_validate(cfg.model_dump())   # store as a plain Config (same fields)
    with Session() as db: return monitor_dict(db,create_monitor(db,cfg))
@app.patch('/api/monitors/{mid}')
def update_monitor(mid:str,patch:dict):
    with Session() as db:
        m=monitor(db,mid); old=config_for(db,m).payload
        # Only a symbol that is being changed is format-checked; legacy stored symbols stay readable/updatable.
        # A changed value is trimmed here (API layer only); an unchanged legacy value is passed through untouched.
        patch=dict(patch)
        for f in ('symbol','underlyingId'):
            if f in patch and patch[f]!=old.get(f): patch[f]=check_symbol(patch[f],f)
        revise(db,m,patch); return monitor_dict(db,m)
@app.delete('/api/monitors/{mid}')
def delete_monitor(mid:str):
    with Session() as db:
        m=monitor(db,mid); m.active=False; db.commit(); return {'status':'DEACTIVATED_HISTORY_RETAINED'}
@app.post('/api/monitors/{mid}/refresh')
def refresh(mid:str):
    with Session() as db:
        m=monitor(db,mid)
        if db.get(RunConfig,m.config_id).payload['provider']=='manual': return {'status':'MANUAL_INPUT_REQUIRED'}
        m.refresh_requested=True; db.commit(); return {'status':'QUEUED_NEXT_10S_SLOT'}
@app.post('/api/manual-snapshots')
def manual(s:Snapshot):
    if s.provider!='manual': raise HTTPException(422,'仅接受人工数据')
    with Session() as db: return save_snapshot(db,s)
@app.post('/api/imports/quotes')
def imports(body:dict):
    if len(body.get('content',''))>5_000_000: raise HTTPException(413,'文件大于5MB')
    with Session() as db:
        m=monitor(db,body['monitorId']); cfg=Config.model_validate(db.get(RunConfig,m.config_id).payload)
        if cfg.provider!='manual': raise HTTPException(422,'请选择人工数据流')
        snapshots=parse_import(body['content'],body['format'],m.id,cfg,body.get('metadata',{}))
        if len(snapshots)>2000: raise HTTPException(413,'单次最多2000个快照')
        return {'results':[save_snapshot(db,s) for s in snapshots]}
@app.post('/api/monitors/{mid}/preview-price-basis')
def preview(mid:str,body:dict):
    with Session() as db:
        monitor(db,mid); raw=db.get(RawSnapshot,body['snapshotId'])
        if not raw or raw.monitor_id!=mid: raise HTTPException(404,'快照不存在')
        rc=db.get(RunConfig,raw.config_id)
        changes={k:v for k,v in body.items() if k in ('optionPricePreset','callPriceBasis','putPriceBasis','stockReferenceBasis')}
        cfg=Config.model_validate({**rc.payload,**changes}); result=compute(cfg,Snapshot.model_validate(raw.payload))
        return {**result,'preview':True,'historicalRecalculation':True,'snapshotId':raw.id,'configHash':rc.hash}
@app.get('/api/results/latest')
def latest(summary:bool=False):
    with Session() as db:
        out={}
        for m in db.scalars(select(Monitor).where(Monitor.active==True)):
            if summary:
                r=db.execute(select(*summary_columns()).where(AggregateResult.monitor_id==m.id).order_by(AggregateResult.id.desc()).limit(1)).first()
                payload,config_id=(summary_row(r),None) if r else (None,None)
                if payload: config_id=payload['configId']
            else:
                row=db.scalar(select(AggregateResult).where(AggregateResult.monitor_id==m.id).order_by(AggregateResult.id.desc()).limit(1))
                payload,config_id=(row.payload,row.config_id) if row else (None,None)
            if payload:
                st=db.scalar(select(StatisticalResult).where(StatisticalResult.monitor_id==m.id,StatisticalResult.config_id==m.config_id).order_by(StatisticalResult.id.desc()).limit(1))
                out[m.id]={**payload,'oldConfiguration':config_id!=m.config_id,'statistics':st.payload if st else None}
                if not summary:
                    # Additive display hint (freshness): from the config that produced this result.
                    rc=db.get(RunConfig,config_id); max_age=(rc.payload if rc else {}).get('maxAgeSeconds',Config.model_fields['maxAgeSeconds'].default)
                    out[m.id]['staleAfterSeconds']=models.staleness_seconds(max_age)
        return out
@app.get('/api/monitors/{mid}/history')
def history(mid:str,limit:int=200,before:int|None=None,after:int|None=None,summary:bool=False):
    with Session() as db:
        monitor(db,mid); limit=min(max(limit,1),500)
        query=select(*summary_columns()) if summary else select(AggregateResult)
        query=query.where(AggregateResult.monitor_id==mid)
        if before: query=query.where(AggregateResult.id<before)
        if after is not None:
            # Incremental: oldest records newer than `after`, ascending.
            query=query.where(AggregateResult.id>after).order_by(AggregateResult.id.asc()).limit(limit); ordered=lambda rows: rows
        else:
            query=query.order_by(AggregateResult.id.desc()).limit(limit); ordered=lambda rows: list(reversed(rows))
        if summary: return [summary_row(r) for r in ordered(db.execute(query).all())]
        return [{'id':r.id,**r.payload} for r in ordered(db.scalars(query).all())]
@app.get('/api/snapshots/{sid}')
def raw_snapshot(sid:int):
    with Session() as db:
        raw=db.get(RawSnapshot,sid)
        if not raw: raise HTTPException(404,'快照不存在')
        rc=db.get(RunConfig,raw.config_id)
        return {'snapshot':raw.payload,'config':rc.payload,'configHash':rc.hash,'snapshotId':sid}
@app.get('/api/monitors/{mid}/statistics')
def stats(mid:str,mode:str|None=None):
    if mode and mode not in BASES: raise HTTPException(422,'未知取价口径')
    with Session() as db:
        m=monitor(db,mid)
        if mode:
            result=statistics_for(db,m,utcnow(),mode); result['historicalRecalculation']=True; result['holmP']=None; return result
        row=db.scalar(select(StatisticalResult).where(StatisticalResult.monitor_id==mid,StatisticalResult.config_id==m.config_id).order_by(StatisticalResult.id.desc()).limit(1))
        return row.payload if row else statistics_for(db,m,utcnow())
@app.get('/api/providers/status')
def status():
    with Session() as db:
        state=db.get(WorkerState,'singleton'); worker=state.payload if state else None; out={}
        for m in db.scalars(select(Monitor).where(Monitor.active==True)):
            row=db.scalar(select(ProviderStatus).where(ProviderStatus.monitor_id==m.id,ProviderStatus.config_id==m.config_id).order_by(ProviderStatus.id.desc()).limit(1))
            rc=db.get(RunConfig,m.config_id)
            out[m.id]={'status':'PAUSED'} if not rc.payload.get('collectionEnabled',True) else (row.payload if row else {'status':'MANUAL_INPUT_REQUIRED' if rc.payload['provider']=='manual' else 'WAITING_WORKER'})
        age=(utcnow()-datetime.fromisoformat(worker['heartbeatAt'])).total_seconds() if worker else None
        return {'cycleSeconds':models.CYCLE_SECONDS,'worker':worker,'workerStatus':'RUNNING' if age is not None and age<8 else 'INTERRUPTED','monitors':out}
@app.post('/api/providers/test')
async def test_provider(body:dict):
    name=body.get('provider','http')
    if name not in ('manual','mock','http','futu'): raise HTTPException(422,'未知Provider')
    p=provider_for(name)
    try:
        if name=='futu':
            async with asyncio.timeout(20): return await p.diagnose()
        async with asyncio.timeout(8): cap=await p.get_capabilities()
        return {'status':'OK' if cap.configured else 'NOT_CONFIGURED','capabilities':cap}
    except (ProviderError,TimeoutError) as e: return {'status':e.code if isinstance(e,ProviderError) else 'TIMEOUT'}
    finally: await p.close()
@app.get('/api/providers/futu/expirations')
async def futu_expirations(symbol:str='SPY'):
    p=provider_for('futu')
    try:
        cfg=Config(provider='futu',dataMode='API',symbol=symbol,underlyingId=symbol)
        return {'dates':await p.list_expirations(cfg),'feedType':'UNKNOWN'}
    except ProviderError as e: return {'dates':[],'status':e.code}
    finally: await p.close()

def parse_event_id(value):
    try: return int(value) if value not in (None,'') else None
    except ValueError: return None
async def sse_events(queue,last_id,is_disconnected,ping_seconds=None):
    ping_seconds=ping_seconds or PING_SECONDS
    try:
        yield 'event: ready\ndata: {"status":"connected"}\n\n'
        sent=last_id
        if last_id is not None:
            with Session() as db: lo,hi=db.execute(select(func.min(ResultOutbox.id),func.max(ResultOutbox.id))).one()
            if (lo is not None and last_id<lo-1) or (hi is not None and last_id>hi) or (lo is None and last_id>0):
                yield RESYNC_EVENT; sent=None
            else:
                while True:
                    with Session() as db: rows=db.scalars(select(ResultOutbox).where(ResultOutbox.id>sent).order_by(ResultOutbox.id).limit(REPLAY_BATCH)).all()
                    if not rows: break
                    for row in rows: yield sse({'id':row.id,**row.payload})
                    sent=rows[-1].id
        loop=asyncio.get_running_loop(); next_ping=loop.time()+ping_seconds
        while not await is_disconnected():
            try:
                item=await asyncio.wait_for(queue.get(),timeout=max(0,next_ping-loop.time()))
                if item is RESYNC: yield RESYNC_EVENT; sent=None
                elif sent is None or item['id']>sent: yield sse(item); sent=item['id']
            except TimeoutError: pass
            if loop.time()>=next_ping: yield PING_EVENT; next_ping=loop.time()+ping_seconds
    finally: subscribers.discard(queue)
@app.get('/api/stream')
async def stream(request:Request,lastEventId:str|None=None):
    # Last-Event-ID header (browser reconnect) wins over the ?lastEventId= query parameter.
    last=parse_event_id(request.headers.get('last-event-id'))
    if last is None: last=parse_event_id(lastEventId)
    queue=asyncio.Queue(maxsize=100); subscribers.add(queue)
    return StreamingResponse(sse_events(queue,last,request.is_disconnected),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})
@app.post('/api/research/frozen-observations')
def freeze(body:dict):
    with Session() as db:
        row=db.scalar(select(AggregateResult).where(AggregateResult.snapshot_id==body['snapshotId']))
        if not row: raise HTTPException(404,'快照不存在')
        raw=db.get(RawSnapshot,row.snapshot_id); rc=db.get(RunConfig,row.config_id)
        frozen=record(FrozenObservation,row.monitor_id,row.config_id,{'result':row.payload,'raw':raw.payload,'config':rc.payload,'note':body.get('note',''),'frozenAt':nowstr()})
        db.add(frozen); db.commit(); return {'id':frozen.id,'status':'FROZEN'}
@app.post('/api/research/frozen-observations/{fid}/outcomes')
def outcome(fid:int,body:dict):
    with Session() as db:
        f=db.get(FrozenObservation,fid)
        if not f: raise HTTPException(404,'观察不存在')
        r=record(ObservationOutcome,f.monitor_id,f.config_id,{'frozenId':fid,'outcome':body}); db.add(r); db.commit(); return {'id':r.id}
def pair_row(agg):
    # pair_results is no longer written; derive the same record shape from the aggregate payload.
    return {'id':agg['id'],'monitor_id':agg['monitor_id'],'config_id':agg['config_id'],'created_at':agg['created_at'],
            'payload':{k:v['rows'] for k,v in agg['payload']['modes'].items()},'snapshot_id':agg['snapshot_id']}
EXPORT_TABLES=[RunConfig,RawSnapshot,'pair_results',AggregateResult,MinuteBlock,StatisticalResult,FrozenObservation,ObservationOutcome,ProviderStatus]
EXPORT_BATCH=500
def export_rows(db,table,mid,batch=EXPORT_BATCH):
    # Keyset pagination by primary key keeps memory bounded to one batch.
    cols=table.__table__.columns; last=None
    while True:
        q=select(table).where(table.monitor_id==mid)
        if last is not None: q=q.where(table.id>last)
        rows=db.scalars(q.order_by(table.id).limit(batch)).all()
        if not rows: return
        for r in rows: yield {c.name:getattr(r,c.name) for c in cols}
        last=rows[-1].id; db.expunge_all()
def export_json(mid):
    with Session() as db:
        yield '{'
        for i,t in enumerate(EXPORT_TABLES):
            name=t if isinstance(t,str) else t.__tablename__
            rows=(pair_row(r) for r in export_rows(db,AggregateResult,mid)) if t=='pair_results' else export_rows(db,t,mid)
            yield ('' if i==0 else ', ')+json.dumps(name)+': ['
            buf=[]
            for j,r in enumerate(rows):
                buf.append(('' if j==0 else ', ')+json.dumps(r,ensure_ascii=False))
                if len(buf)>=EXPORT_BATCH: yield ''.join(buf); buf=[]
            yield ''.join(buf)+']'
        yield '}'
@app.get('/api/exports/{mid}')
def export(mid:str,format:str='json'):
    with Session() as db: monitor(db,mid)
    if format=='csv':
        with Session() as db:
            buff=io.StringIO(); writer=csv.writer(buff); writer.writerow(['snapshotId','configHash','valuationAt','feedType','mode','callBasis','putBasis','stockBasis','rawMean','validatedMean','belowMean','aboveMean','validCount','requestedCount','strictEligible'])
            for r in export_rows(db,AggregateResult,mid):
                p=r['payload']
                for mode,a in p['modes'].items(): writer.writerow([p['snapshotId'],p['configHash'],p['valuationAt'],p['feedType'],mode]+[a[k] for k in ('callBasis','putBasis','stockBasis','rawMean','validatedMean','belowMean','aboveMean','validCount','requestedCount','strictEligible')])
            return Response(buff.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{mid}.csv"'})
    return StreamingResponse((c.encode() for c in export_json(mid)),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{mid}.json"'})

@app.get('/api/research/frozen-observations')
def frozen_list(monitorId:str):
    with Session() as db:
        return [{'id':r.id,'createdAt':r.created_at,**r.payload} for r in db.scalars(select(FrozenObservation).where(FrozenObservation.monitor_id==monitorId).order_by(FrozenObservation.id.desc()))]
