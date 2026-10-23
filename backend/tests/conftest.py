import os, tempfile
os.environ['DATABASE_PATH']=tempfile.mktemp(suffix='.sqlite3')
import pytest
from datetime import datetime, timezone, timedelta
from app.models import *
from app.db import Base,engine
Base.metadata.create_all(engine)
@pytest.fixture
def at(): return datetime(2026,10,19,15,0,0,tzinfo=timezone.utc)
@pytest.fixture
def cfg(): return Config(nBelow=1,nAbove=0,rate=0,timeRule='days365',remainingDays=2)
@pytest.fixture
def sample(at):
    def make(strikes=(600,),spot=601,feed='MANUAL'):
        def q(b,a,l): return Quote(bid=b,ask=a,last=l,bidAt=at,askAt=at,lastAt=at,source='test',feedType=feed)
        legs=[]
        for k in strikes:
            for right in ('C','P'):
                c=Contract(id=f'{k}{right}',underlyingId='SPY',expiration='2026-10-19',strike=k,right=right,pricingAt=at+timedelta(hours=5),lastTradingAt=at+timedelta(hours=5))
                legs.append(Leg(contract=c,quote=q(4,4.2,4.1) if right=='C' else q(1,1.5,1.2)))
        return Snapshot(monitorId='test',valuationAt=at,provider='manual',underlying=q(spot-.01,spot+.01,spot),legs=legs,contracts=[l.contract for l in legs],marketOpen=True,optionSessionOpen=True,marketReason='TEST')
    return make
