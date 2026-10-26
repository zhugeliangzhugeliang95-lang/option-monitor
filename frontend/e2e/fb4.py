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
S=str(SHOTS)+'/';R=[]
def ok(n,c,x=''): R.append(c); print('PASS' if c else 'FAIL',n,x)
def near(v): return abs((datetime.datetime.fromisoformat(v.replace('Z','+00:00'))-datetime.datetime.now(datetime.timezone.utc)).total_seconds())<6
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch();ctx=await b.new_context(timezone_id='Asia/Shanghai',viewport={'width':1400,'height':1000});pg=await ctx.new_page()
    # Chromium 对任何非 2xx（含 4xx 与 route.abort）都会打一条 "Failed to load resource"；按消息来源 URL 区分本脚本拦截的 POST，其余 error 必须为 0
    errs=[];icpt=[]
    def onc(m):
      if m.type!='error': return
      u=(m.location or {}).get('url','')
      (icpt if 'Failed to load resource' in m.text and re.search(r'/api/(monitors|manual-snapshots)$',u) and '422' in m.text else errs).append(m.text)
    pg.on('console',onc)
    posted=[];snaps=[]
    async def h(route):
      r=route.request
      if r.method=='POST': posted.append(json.loads(r.post_data or '{}'));await route.fulfill(status=422,content_type='application/json',body='{"detail":"intercepted"}')
      else: await route.continue_()
    await pg.route(re.compile(r'/api/monitors$'),h)
    async def hs(route): snaps.append(json.loads(route.request.post_data));await route.fulfill(status=422,content_type='application/json',body='{"detail":"intercepted"}')
    await pg.route(re.compile(r'/api/manual-snapshots$'),hs)
    await pg.goto(BASE);await pg.wait_for_timeout(3500)
    s=pg.get_by_role('textbox',name='标的代码');err=pg.get_by_test_id('create-error')
    addb=pg.get_by_role('button',name='新增标的');await addb.wait_for(state='visible',timeout=15000)
    first=[True]
    async def tryc(v):
      if first[0]:
        first[0]=False
        try: await addb.click(timeout=5000)
        except Exception as e: print('首次点击「新增标的」失败，重试一次:',type(e).__name__);await pg.wait_for_timeout(1000);await addb.click(timeout=10000)
      else: await addb.click()
      await pg.wait_for_timeout(200)
      n=len(posted);await s.fill(v);await pg.get_by_role('button',name='创建监测').click();await pg.wait_for_timeout(500)
      e=await err.inner_text() if await err.count() else ''
      inmodal=await pg.locator('[role=dialog], .modal').filter(has=err).count()>0 if e else False
      sent=posted[n]['symbol'] if len(posted)>n else None
      await pg.keyboard.press('Escape');x=pg.locator('.modal button, [role=dialog] button').filter(has_text='×')
      if await x.count(): await x.first.click()
      await pg.wait_for_timeout(200);return e,inmodal,sent
    for v in ['ß','ﬀ','ＳＰＹ','spß']:
      e,m,sent=await tryc(v);ok(f'拒绝 {v!r}',sent is None and m and '半角' in e,e)
      if v=='ＳＰＹ':pass
    for v,exp in [('SPY','SPY'),('BRK.B','BRK.B'),('US.SPY','US.SPY'),('A'*20,'A'*20),('  qqq  ','QQQ')]:
      e,m,sent=await tryc(v);ok(f'放行 {v!r}',sent==exp,f'sent={sent}')
    e,m,sent=await tryc('!!$#');ok('!!$# 仍报原格式错误',sent is None and '格式无效' in e,e)
    e,m,sent=await tryc('A'*21);ok('21 字符拒绝',sent is None and m,e)
    # manual panel
    nav=lambda n: pg.get_by_role('button',name=n).first
    await pg.get_by_role('button',name='EXCEL').first.click();await pg.wait_for_timeout(800);await nav('人工报价').click();await pg.wait_for_timeout(800)
    a=pg.get_by_role('textbox',name=re.compile('事件时刻'));pr=pg.get_by_role('textbox',name=re.compile('定价参考时刻'));ex=pg.locator('input[type=date]')
    v1=await a.input_value();await pg.wait_for_timeout(3000);await nav('监测总览').click();await pg.wait_for_timeout(600);await nav('人工报价').click();await pg.wait_for_timeout(600)
    v2=await a.input_value();ok('未改→切回为当前时间',v2!=v1 and near(v2),f'{v1}->{v2}')
    await a.fill('2026-10-20T10:00:00-04:00');await pr.fill('2026-10-21T16:00:00-04:00');await ex.fill('2026-10-21')
    for t in ['监测配置','监测总览','人工报价']: await nav(t).click();await pg.wait_for_timeout(600)
    vals=(await a.input_value(),await pr.input_value(),await ex.input_value())
    ok('改三项→切 tab 保留',vals==('2026-10-20T10:00:00-04:00','2026-10-21T16:00:00-04:00','2026-10-21'),str(vals))
    await pg.screenshot(path=S+'polish_manual_kept.png',full_page=True)
    await pg.get_by_role('button',name='SPY').first.click();await pg.wait_for_timeout(800);await pg.get_by_role('button',name='EXCEL').first.click();await pg.wait_for_timeout(800);await nav('人工报价').click();await pg.wait_for_timeout(600)
    vals2=(await a.input_value(),await pr.input_value(),await ex.input_value())
    ok('切到 SPY 再回 EXCEL→重置',vals2[0]!='2026-10-20T10:00:00-04:00' and near(vals2[0]) and vals2[2]!='2026-10-21',str(vals2))
    # save untouched: wait then save -> valuationAt is now
    await pg.wait_for_timeout(4000);await pg.get_by_role('button',name='计算并保存快照').click();await pg.wait_for_timeout(800)
    ok('保存时未改时间刷新为当前',len(snaps)==1 and near(snaps[0]['valuationAt']),snaps and snaps[0]['valuationAt'])
    await pg.wait_for_timeout(300);await nav('人工报价').click();await pg.wait_for_timeout(400)
    await a.fill('2026-10-20T10:00:00-04:00');await pg.get_by_role('button',name='计算并保存快照').click();await pg.wait_for_timeout(800)
    ok('保存时已改时间保留',len(snaps)==2 and snaps[1]['valuationAt']=='2026-10-20T10:00:00-04:00',snaps[-1]['valuationAt'])
    print('console errors',len(errs),'intercepted-422 network lines',len(icpt),'intercepted requests',len(posted)+len(snaps))
    ok('控制台无 500、非拦截 error 数为 0',len(errs)==0 and not any('500' in e for e in icpt),str(errs[:3]))
    ok('拦截产生的网络行数 = 拦截请求数',len(icpt)==len(posted)+len(snaps),f'{len(icpt)}/{len(posted)+len(snaps)}')
    await b.close()
  print('SUMMARY',sum(R),'/',len(R))
asyncio.run(main())
