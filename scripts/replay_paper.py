#!/usr/bin/env python3
"""Reproducible local replay; no broker calls. Run from project root."""
import argparse, json, sys, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.db import Session, PaperRun, nowstr
from app.paper import PaperConfig,load_events,run_research
p=argparse.ArgumentParser();p.add_argument('--day',required=True);p.add_argument('--direction',choices=['convergence','follow_stock'],default='convergence');p.add_argument('--output',required=True)
args=p.parse_args()
cfg=PaperConfig(tradingDay=args.day,optionDirection=args.direction)
with Session() as db:
    events=load_events(db,cfg)
    if not events: raise SystemExit('No saved regular-session data; no fabricated replay.')
    report=run_research(events,cfg)
    row=PaperRun(id=uuid.uuid4().hex,created_at=nowstr(),payload=report);db.add(row);db.commit()
    result={'id':row.id,'createdAt':row.created_at,**report}
Path(args.output).parent.mkdir(parents=True,exist_ok=True)
Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:result[k] for k in ['id','sampleCount','firstAt','lastAt']},ensure_ascii=False))
for mode in ['strict','exploratory']:
    r=result[mode];print(mode,r['status'],r['totalNetPnl'],r['quality'])
    for a in r['accounts']:print(a['kind'],{k:a[k] for k in ['equity','netPnl','closedTrades','filledOrders','reasons']})
