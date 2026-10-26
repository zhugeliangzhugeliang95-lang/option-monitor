"""Explicit one-time migration of active monitors; defaults to a read-only plan."""
import argparse, json, sys
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env');sys.path.insert(0,str(ROOT/'backend'))
from sqlalchemy import select
from app.db import Session, Monitor, DB_PATH
from app.service import config_for, revise

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
patch={'expiryMode':'nearest','expiration':None,'timeRule':'expiryClose','pricingAt':None,'remainingDays':None,
       'timeAssumption':'研究假设：采用所选到期日交易所常规收盘时间（含半日市），非API核实的期权最后交易或行权截止时刻；随到期日滚动。'}
with Session() as db:
    monitors=list(db.scalars(select(Monitor).where(Monitor.active==True)))
    before=[{'id':m.id,'configId':m.config_id,'config':config_for(db,m).payload} for m in monitors]
    if args.apply:
        backup=DB_PATH.parent/('configs-before-nearest-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
        backup.write_text(json.dumps(before,ensure_ascii=False,indent=2));backup.chmod(0o600)
        for m in monitors: revise(db,m,patch)
    print(json.dumps({'applied':args.apply,'count':len(monitors),'symbols':[m['config']['symbol'] for m in before]},ensure_ascii=False))
