#!/usr/bin/env python3
"""Explicit opt-in local demo, including separately marked synthetic history."""
import asyncio,sys
from pathlib import Path
from datetime import timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.models import *
from app.providers import MockProvider
from app.db import Session,Monitor
from sqlalchemy import select
from app.service import create_monitor,save_snapshot,persist_statistics
async def main():
    with Session() as db:
        if db.scalar(select(Monitor).limit(1)):
            print('Database is not empty; demo seed refused to avoid duplicate monitors.');return
        for symbol in ('SPY','QQQ'):
            create_monitor(db,Config(symbol=symbol,underlyingId=symbol,provider='mock',dataMode='MOCK',nBelow=3,nAbove=3))
        manual=create_monitor(db,Config(symbol='EXCEL',underlyingId='EXCEL',nBelow=1,timeRule='days365',remainingDays=2))
        at=utcnow();expiration=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).date().isoformat()
        def q(b,a,l):return Quote(bid=b,ask=a,last=l,bidAt=at,askAt=at,lastAt=at,source='manual-excel-example',feedType='MANUAL')
        legs=[Leg(contract=Contract(id='EXCEL-'+right,underlyingId='EXCEL',expiration=expiration,strike='152.5',right=right),quote=quote) for right,quote in [('C',q(6.52,6.72,6.62)),('P',q(1.78,1.98,1.88))]]
        save_snapshot(db,Snapshot(monitorId=manual.id,provider='manual',valuationAt=at,underlying=q(157.19,157.21,157.2),legs=legs))
        create_monitor(db,Config(symbol='HTTP',underlyingId='SPY',provider='http',dataMode='API'))
        if '--history' in sys.argv:
            cfg=Config(symbol='SIM-HISTORY',underlyingId='SPY',provider='mock',dataMode='MOCK',nBelow=1,nAbove=1,collectionEnabled=False)
            m=create_monitor(db,cfg);end=utcnow().replace(second=0,microsecond=0)
            for minute in range(35):
                for slot in range(4):
                    tick=end-timedelta(minutes=35-minute)+timedelta(seconds=slot*10)
                    p=MockProvider(clock=lambda:tick);u=(await p.get_underlying_snapshots(['SPY']))['SPY'];expiry=(await p.list_expirations(cfg))[0]
                    contracts=await p.list_option_contracts(cfg,expiry);quotes=await p.get_option_quotes(contracts)
                    save_snapshot(db,Snapshot(monitorId=m.id,provider='mock',valuationAt=tick,underlying=u,contracts=contracts,legs=[Leg(contract=c,quote=quotes[c.id]) for c in contracts],marketOpen=True,optionSessionOpen=True,marketReason='MOCK_HISTORICAL_SIMULATION',historical=True))
            persist_statistics(db,utcnow())
        print('Created explicitly labeled MOCK and MANUAL demo streams. No real quotes.')
asyncio.run(main())
