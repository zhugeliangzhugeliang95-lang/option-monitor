"""Exercise a running local instance; requires a MOCK monitor, never real data."""
import asyncio,json,sys
from pathlib import Path
from datetime import datetime,timezone
import httpx
ROOT=Path(__file__).resolve().parents[1]
async def main():
    base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8100'
    async with httpx.AsyncClient(base_url=base,timeout=30) as c:
        ms=(await c.get('/api/monitors')).json();m=next(x for x in ms if x['config']['provider']=='mock' and x['config']['collectionEnabled'])
        mid=m['id'];start=datetime.now(timezone.utc)
        async with c.stream('GET','/api/stream') as first,c.stream('GET','/api/stream') as second:
            assert first.status_code==second.status_code==200
            l1=first.aiter_lines();l2=second.aiter_lines()
            assert await anext(l1)=='event: ready';assert await anext(l2)=='event: ready'
            await asyncio.gather(*(c.post(f'/api/monitors/{mid}/refresh') for _ in range(12)))
            await asyncio.sleep(22)
        h=(await c.get(f'/api/monitors/{mid}/history')).json();new=[r for r in h if datetime.fromisoformat(r['valuationAt'])>=start]
        assert 1<=len(new)<=4, len(new)
        slots=[int(datetime.fromisoformat(r['valuationAt']).timestamp()//10) for r in new]
        assert len(slots)==len(set(slots))
        p=await asyncio.create_subprocess_exec(sys.executable,'-m','app.worker',cwd=ROOT/'backend',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(p.communicate(),timeout=10)
        assert p.returncode!=0 and b'already owns' in out+err
        result={'sseClients':2,'manualRefreshRequests':12,'observationSeconds':22,'newSnapshots':len(new),'distinctSlots':len(set(slots)),'secondWorkerRejected':True,'start':start.isoformat()}
        print(json.dumps(result,indent=2));(ROOT/'docs/runtime-smoke.json').write_text(json.dumps(result,indent=2))
asyncio.run(main())
