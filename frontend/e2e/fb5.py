import os,pathlib
BASE=os.environ.get('E2E_BASE_URL','').rstrip('/')
if not BASE:
  import sys
  sys.stderr.write('用法：E2E_BASE_URL=http://localhost:<前端端口> [E2E_ALLOW_WRITE=1] python frontend/e2e/%s\n'
    'E2E_BASE_URL 必填（无默认值，避免误打到别人的栈）；只应指向自己用临时 DATABASE_PATH 起的测试栈。\n' % __import__('os').path.basename(__file__))
  sys.exit(2)
HERE=pathlib.Path(__file__).resolve().parent
SHOTS=HERE/'shots';SHOTS.mkdir(exist_ok=True)
import asyncio,json,re,datetime
from playwright.async_api import async_playwright
R=[]
def ok(n,c,x=''): R.append(c); print('PASS' if c else 'FAIL',n,x)
def near(v): return abs((datetime.datetime.fromisoformat(v.replace('Z','+00:00'))-datetime.datetime.now(datetime.timezone.utc)).total_seconds())<8
A='2026-03-03T00:00:00Z';P='2026-03-04T16:00:00-05:00'
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch();ctx=await b.new_context(timezone_id='Asia/Shanghai',viewport={'width':1400,'height':1000});pg=await ctx.new_page()
    posted=[];sent=[]
    async def h(route):
      r=route.request
      if r.method=='POST': posted.append(json.loads(r.post_data));await route.fulfill(status=500,content_type='application/json',body='{"detail":"intercepted"}')
      else: await route.continue_()
    await pg.route(re.compile(r'/api/monitors$'),h)
    async def hs(route): sent.append(json.loads(route.request.post_data));await route.fulfill(status=200,content_type='application/json',body='{}')
    await pg.route(re.compile(r'/api/(manual-snapshots|imports/quotes)$'),hs)
    await pg.goto(BASE);await pg.wait_for_timeout(3500)
    s=pg.get_by_role('textbox',name='标的代码');err=pg.get_by_test_id('create-error')
    async def tryc(v):
      await pg.get_by_role('button',name='新增标的').click();await pg.wait_for_timeout(200)
      n=len(posted);await s.fill(v);await pg.get_by_role('button',name='创建监测').click();await pg.wait_for_timeout(400)
      e=await err.inner_text() if await err.count() else ''
      x=pg.locator('.modal button, [role=dialog] button').filter(has_text='×')
      if await x.count(): await x.first.click()
      await pg.wait_for_timeout(200);return e,(posted[n]['symbol'] if len(posted)>n else None)
    for v in ['SPY\t','\tSPY','SPY\u00a0','SPY\n',' \u00a0spy\t']:
      e,sy=await tryc(v);ok(f'放行 {v!r}',sy=='SPY',f'{sy} {e}')
    for v in ['ß','ﬀ','ＳＰＹ','SPY😀','İBM','\tß ']:
      e,sy=await tryc(v);ok(f'拦截 {v!r}',sy is None and '半角' in e,e[:20])
    nav=lambda n: pg.get_by_role('button',name=n).first
    async def manual():
      await nav('人工报价').click();await pg.wait_for_timeout(600)
    await pg.get_by_role('button',name='EXCEL').first.click();await pg.wait_for_timeout(800);await manual()
    a=pg.get_by_role('textbox',name=re.compile('事件时刻'));pr=pg.get_by_role('textbox',name=re.compile('定价参考时刻'))
    async def reset():
      await pg.get_by_role('button',name='SPY').first.click();await pg.wait_for_timeout(600);await pg.get_by_role('button',name='EXCEL').first.click();await pg.wait_for_timeout(600);await manual()
    async def act(kind):
      n=len(sent)
      if kind=='save': await pg.get_by_role('button',name='计算并保存快照').click()
      else: await pg.locator('input[type=file]').set_input_files(str(HERE/'min-import.csv'))
      await pg.wait_for_timeout(900);d=sent[n] if len(sent)>n else {}
      return d.get('valuationAt') or d.get('metadata',{}).get('valuationAt'),d.get('pricingAt') or d.get('metadata',{}).get('pricingAt')
    for kind in ['save','import']:
      for case in ['onlyA','onlyP','none','both']:
        await reset();await pg.wait_for_timeout(2500)
        if case in('onlyA','both'): await a.fill(A)
        if case in('onlyP','both'): await pr.fill(P)
        rv,rp=await act(kind)
        await nav('监测总览').click();await pg.wait_for_timeout(500);await manual();await pg.wait_for_timeout(2500)
        await nav('监测配置').click();await pg.wait_for_timeout(400);await manual()
        va,vp=await a.input_value(),await pr.input_value()
        if case=='onlyA': c=rv==A and va==A and vp!=P and (rp is None or rp!=P)
        elif case=='onlyP': c=near(rv) and (rp is None or rp==P) and vp==P and near(va)
        elif case=='none': c=near(rv) and near(va) and va!=rv
        else: c=rv==A and (rp is None or rp==P) and va==A and vp==P
        ok(f'{kind}/{case}',c,f'req=({rv},{rp}) panel=({va},{vp})')
    await b.close()
  print('SUMMARY',sum(R),'/',len(R))
asyncio.run(main())
