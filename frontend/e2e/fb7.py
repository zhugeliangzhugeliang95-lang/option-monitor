# 运行：先起 dev 栈（scripts/dev.sh，须设置 E2E_BASE_URL），python frontend/e2e/fb7.py；只 mock 读接口，不写 data/
# stale-threshold：quality-strip 「历史 / 过旧快照」阈值（固定页面时钟，SSE 置空，mock results/latest 与 providers/status）
import asyncio,json,re,time,os,datetime as dt
BASE=os.environ.get('E2E_BASE_URL','').rstrip('/')
if not BASE:
  import sys
  sys.stderr.write('用法：E2E_BASE_URL=http://localhost:<前端端口> [E2E_ALLOW_WRITE=1] python frontend/e2e/%s\n'
    'E2E_BASE_URL 必填（无默认值，避免误打到别人的栈）；只应指向自己用临时 DATABASE_PATH 起的测试栈。\n' % __import__('os').path.basename(__file__))
  sys.exit(2)
from playwright.async_api import async_playwright
import urllib.request
R=[]
SPY=next(m['id'] for m in json.load(urllib.request.urlopen(BASE+'/api/monitors')) if m.get('config',{}).get('symbol')=='SPY' and m.get('active'))
def ok(n,c,x=''): R.append(c); print('PASS' if c else 'FAIL',n,x)
T=int(time.time())*1000
iso=lambda age: dt.datetime.fromtimestamp((T-age*1000)/1000,dt.timezone.utc).isoformat()
# (名称, staleAfterSeconds 或 None, cycleSeconds 或 None, {age: 是否过旧})  —— SPY maxAgeSeconds=5
CASES=[
 ('A 有 staleAfterSeconds=20',20,None,{6:False,10:False,19:False,21:True}),
 ('B 仅 cycleSeconds=10',None,10,{6:False,10:False,19:False,21:True}),
 ('C 两者皆无→max(5,20)',None,None,{6:False,10:False,19:False,21:True}),
 ('A2 staleAfterSeconds=8 优先于 cycle=10',8,10,{6:False,10:True}),
 ('B2 cycleSeconds=5→max(5,10)',None,5,{6:False,10:False,19:True}),
]
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch();ctx=await b.new_context(timezone_id='Asia/Shanghai',viewport={'width':1400,'height':1000})
    st={};errs=[]
    async def hp(route):
      r=await route.fetch();j=await r.json();j.pop('cycleSeconds',None)
      if st['cyc'] is not None: j['cycleSeconds']=st['cyc']
      await route.fulfill(response=r,body=json.dumps(j))
    async def hr(route):
      r=await route.fetch();j=await r.json()
      if SPY in j:
        x=dict(j[SPY]);x.pop('staleAfterSeconds',None);x['valuationAt']=iso(st['age'])
        if st['sa'] is not None: x['staleAfterSeconds']=st['sa']
        j[SPY]=x
      await route.fulfill(response=r,body=json.dumps(j))
    await ctx.route(re.compile(r'/api/providers/status'),hp);await ctx.route(re.compile(r'/api/results/latest'),hr)
    await ctx.route(re.compile(r'/api/stream'),lambda r: r.fulfill(status=204,body=''))
    for name,sa,cyc,ages in CASES:
      for age,exp in ages.items():
        st.update(sa=sa,cyc=cyc,age=age);pg=await ctx.new_page();pg.on('console',lambda m: m.type=='error' and errs.append(m.text))
        await pg.clock.set_fixed_time(T/1000)
        await pg.goto(BASE);await pg.wait_for_timeout(2500)
        await pg.get_by_role('button',name='SPY').first.click();await pg.wait_for_timeout(800)
        t=await pg.locator('.quality-strip').first.inner_text();got='历史 / 过旧快照' in t
        ok(f'{name} age={age}s 期望{"过旧" if exp else "新鲜"}',got==exp,repr(t[:80]));await pg.close()
    ok('控制台无 error',not errs,str(errs[:2]))
    await b.close()
  print('SUMMARY',sum(R),'/',len(R))
asyncio.run(main())
