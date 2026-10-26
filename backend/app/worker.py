import asyncio, fcntl, logging, os, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import *
from .models import Config, Snapshot, Leg, utcnow
from .engine import selected_strikes
from .providers import provider_for, ProviderError, regular_session, exchange_day, live_expirations, target_expiry
from .service import save_snapshot, persist_statistics
from . import retention
from . import futu_config
log=logging.getLogger('app.worker')
# Plan item 1, section E: statuses that are not failures (no failure count, no backoff, retry 10 s)
NON_FAILURE={'FUTU_BUSY','CHAIN_WARMING'}
# Failure statuses (shown red) that retry every 10 s without exponential backoff and without counting failures
NO_BACKOFF={'OPEND_UNRESPONSIVE','CHAIN_UNAVAILABLE'}

class Worker:
    def __init__(self):
        self.providers={k:provider_for(k) for k in ('mock','http','futu')}; self.locks={}; self.next_due={}; self.failures={}; self.semaphore=asyncio.Semaphore(4); self.running=True
        self.futu_task=None; self.futu_inflight=set(); self.maintenance=False; self.last_status={}
    async def heartbeat(self):
        while self.running:
            with Session() as db:
                state=db.get(WorkerState,'singleton') or WorkerState(id='singleton',payload={})
                state.payload={'heartbeatAt':nowstr(),'pid':os.getpid(),'targetSeconds':10}; db.add(state); db.commit()
            await asyncio.sleep(2)
    async def collect(self,mid,rc,underlying=None,pre_error=None,prepared=None,chain_meta=None):
        lock=self.locks.setdefault(mid,asyncio.Lock())
        if lock.locked(): return
        async with lock, self.semaphore:
            started=time.monotonic(); cfg=Config.model_validate(rc.payload); provider=self.providers[cfg.provider]
            status='OK'; retry=0; operation=None; extra={}
            try:
                if pre_error: raise pre_error
                async with asyncio.timeout(9):
                    if prepared is not None:
                        snapshot=prepared
                    else:
                        state=await provider.get_market_state(cfg)
                        if underlying is None: raise ProviderError('MISSING_UNDERLYING')
                        at=utcnow(); day=exchange_day(cfg,at); expiry=target_expiry(cfg,day)
                        expirations=live_expirations(await provider.list_expirations(cfg),day)
                        contracts=await provider.list_option_contracts(cfg,expiry) if expiry in expirations else []
                        chosen=selected_strikes(contracts,underlying.last,cfg.nBelow,cfg.nAbove) if underlying.last is not None else []
                        wanted={k for k,_ in chosen}; selected=[c for c in contracts if c.strike in wanted]
                        quotes=await provider.get_option_quotes(selected)
                        at=utcnow()
                        # Real feeds require calendar and explicit provider option session, never a hardcoded 16:00.
                        simulated=underlying.feedType=='MOCK'
                        market=state.marketOpen and (simulated or regular_session(cfg,at))
                        options=state.optionSessionOpen and all(c.lastTradingAt and at<=c.lastTradingAt for c in selected)
                        snapshot=Snapshot(monitorId=mid,valuationAt=at,provider=cfg.provider,underlying=underlying,contracts=contracts,
                            legs=[Leg(contract=c,quote=quotes[c.id]) for c in selected if c.id in quotes],marketOpen=market,optionSessionOpen=options,marketReason=state.reason)
                    with Session() as db: result=save_snapshot(db,snapshot,rc.id,extra={'chain':chain_meta} if chain_meta else None)
                    if chain_meta: extra['chain']=chain_meta
                    status=result['status']; self.failures[mid]=0
            except (ProviderError,TimeoutError) as e:
                operation=getattr(e,'operation',None)
                status=e.code if isinstance(e,ProviderError) else 'TIMEOUT'
                for attr,name in (('pending_seconds','pendingSeconds'),('busy_cycles','busyCycles'),('chain','chain')):
                    if getattr(e,attr,None) is not None: extra[name]=getattr(e,attr)
                if status in NON_FAILURE or status in NO_BACKOFF:
                    retry=10  # failures[mid] untouched: the next real error starts at the base backoff
                else:
                    self.failures[mid]=self.failures.get(mid,0)+1
                    retry=max(10,getattr(e,'retry_after',0)) if status=='RATE_LIMITED' else max(getattr(e,'retry_after',0),min(120,10*2**(self.failures[mid]-1)))
            except Exception:
                status='INVALID_PROVIDER_DATA'; self.failures[mid]=self.failures.get(mid,0)+1; retry=10
            duration=time.monotonic()-started
            self.next_due[mid]=time.monotonic()+max(10-(time.time()%10),retry)
            with Session() as db:
                db.add(record(ProviderStatus,mid,rc.id,{'status':status,'operation':operation,'durationSeconds':duration,'retryAfter':retry,'delayed':duration>=10 or retry>0,'at':nowstr(),**extra}))
                db.add(ResultOutbox(payload={'type':'provider','monitorId':mid,'status':status},created_at=nowstr())); db.commit()
            if status in NON_FAILURE and self.last_status.get(mid)!=status:
                log.info('monitor %s status %s operation %s retry %ss',mid,status,operation,retry)
            self.last_status[mid]=status
    async def futu_batch(self,items):
        """Section B: one futu batch, run as a background task so it never holds the main loop."""
        try:
            try:
                async with asyncio.timeout(futu_config.settings().batch_timeout):
                    snapshots, errors = await self.providers['futu'].collect_batch({mid:Config.model_validate(rc.payload) for mid,rc in items})
            except Exception as e:
                # A failed batch never re-collects: every monitor gets the batch error, no extra OpenD call.
                error = e if isinstance(e,ProviderError) else ProviderError('TIMEOUT' if isinstance(e,TimeoutError) else 'INVALID_PROVIDER_DATA')
                if isinstance(e,TimeoutError): error.operation=getattr(self.providers['futu'],'current_method',None)
                for mid,rc in items:
                    await self.collect(mid,rc,pre_error=error)
                return
            meta=dict(getattr(self.providers['futu'],'chain_meta',None) or {})  # read right after the batch (one batch at a time)
            for mid, rc in items:
                await self.collect(mid,rc,prepared=snapshots.get(mid),pre_error=errors.get(mid),
                                   chain_meta=meta.get(mid) if snapshots.get(mid) is not None else None)
        finally:
            self.futu_inflight.difference_update(mid for mid,_ in items)
    async def futu_skip(self,items,operation):
        error=ProviderError('FUTU_BUSY',10); error.operation=operation
        await asyncio.gather(*(self.collect(mid,rc,pre_error=error) for mid,rc in items))
    async def cycle(self):
        due=[]
        with Session() as db:
            for m in db.scalars(select(Monitor).where(Monitor.active==True)):
                rc=db.get(RunConfig,m.config_id)
                if rc.payload['provider']=='manual' or not rc.payload.get('collectionEnabled',True): continue
                if m.id in self.futu_inflight: continue  # its batch is still running in the background
                scheduled=time.monotonic()>=self.next_due.get(m.id,0)
                # Manual refresh coalesces into the same slot and respects backoff.
                if scheduled:
                    due.append((m.id,rc)); m.refresh_requested=False
            db.commit()
        async def group(name,items):
            if name == 'futu':
                if self.maintenance or not self.running:
                    await self.futu_skip(items,'maintenance'); return
                if self.futu_task is not None and not self.futu_task.done():
                    await self.futu_skip(items,'busy'); return
                self.futu_inflight.update(mid for mid,_ in items)
                self.futu_task=asyncio.create_task(self.futu_batch(items))
                return
            try:
                async with asyncio.timeout(8): quotes=await self.providers[name].get_underlying_snapshots(list({r.payload['underlyingId'] for _,r in items}))
                await asyncio.gather(*(self.collect(mid,rc,quotes.get(rc.payload['underlyingId'])) for mid,rc in items))
            except Exception as e:
                error=e if isinstance(e,ProviderError) else ProviderError('TIMEOUT' if isinstance(e,TimeoutError) else 'INVALID_PROVIDER_DATA')
                async def individual(mid,rc):
                    try:
                        async with asyncio.timeout(8): q=await self.providers[name].get_underlying_snapshots([rc.payload['underlyingId']])
                        await self.collect(mid,rc,q.get(rc.payload['underlyingId']))
                    except Exception as sub:
                        issue=sub if isinstance(sub,ProviderError) else ProviderError('TIMEOUT' if isinstance(sub,TimeoutError) else 'INVALID_PROVIDER_DATA')
                        await self.collect(mid,rc,pre_error=issue)
                if len(items)>1 and error.code not in ('RATE_LIMITED','NOT_CONFIGURED','PERMISSION_DENIED','NOT_SUBSCRIBED'):
                    await asyncio.gather(*(individual(mid,rc) for mid,rc in items))
                else:
                    await asyncio.gather(*(self.collect(mid,rc,pre_error=error) for mid,rc in items))
        await asyncio.gather(*(group(name,[(m,r) for m,r in due if r.payload['provider']==name]) for name in self.providers if any(r.payload['provider']==name for _,r in due)))
    async def run(self):
        hb=asyncio.create_task(self.heartbeat()); minute=None; retention_day=None; zone=retention.exchange_zone()
        try:
            while True:
                await self.cycle()
                if retention.due(utcnow(),retention_day,zone):
                    # Between collection cycles, once per exchange day at 08:00 America/New_York.
                    retention_day=utcnow().astimezone(zone).date()
                    await self.run_retention()
                now=utcnow(); key=now.replace(second=0,microsecond=0)
                if key!=minute:
                    with Session() as db: persist_statistics(db,now)
                    minute=key
                await asyncio.sleep(.25)
        finally:
            await self.stop(hb)
    async def run_retention(self):
        """Section C: skip-cycle. No futu task starts during retention; a running one is awaited first."""
        settings=futu_config.settings(); self.maintenance=True
        try:
            if self.futu_task is not None and not self.futu_task.done():
                await asyncio.wait({self.futu_task},timeout=settings.batch_timeout)
            job=asyncio.ensure_future(asyncio.to_thread(retention.run_on_path,DB_PATH))
            done,_=await asyncio.wait({job},timeout=settings.maint_alert_seconds)
            if not done:
                log.warning('retention still running after %ss; futu stays FUTU_BUSY (maintenance)',settings.maint_alert_seconds)
            try: await job
            except Exception: log.exception('retention failed')
        finally:
            self.maintenance=False
    async def stop(self,hb=None):
        """Section B stop order: no new futu task, cancel/await it, heartbeat, futu close, other providers."""
        settings=futu_config.settings(); self.running=False
        if self.futu_task is not None and not self.futu_task.done():
            self.futu_task.cancel()
            await asyncio.wait({self.futu_task},timeout=settings.batch_timeout+1)
        if hb is not None:
            hb.cancel(); await asyncio.gather(hb,return_exceptions=True)
        await self.providers['futu'].close()
        for name,p in self.providers.items():
            if name!='futu': await p.close()
        log.info('worker stopped')

def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s %(message)s')
    lock=open(str(DB_PATH)+'.worker.lock','w')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('A worker already owns this database')
    try: asyncio.run(Worker().run())
    finally: lock.close()
if __name__=='__main__': main()
