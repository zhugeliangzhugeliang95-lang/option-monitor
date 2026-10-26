import os,pathlib
BASE=os.environ.get('E2E_BASE_URL','').rstrip('/')
if not BASE:
  import sys
  sys.stderr.write('用法：E2E_BASE_URL=http://localhost:<前端端口> [E2E_ALLOW_WRITE=1] python frontend/e2e/%s\n'
    'E2E_BASE_URL 必填（无默认值，避免误打到别人的栈）；只应指向自己用临时 DATABASE_PATH 起的测试栈。\n' % __import__('os').path.basename(__file__))
  sys.exit(2)
# E2E_BASE_URL 只决定地址；写操作（真实 POST /api/monitors：futu ZZFUTU 422 校验、创建 ZZFB）只在 E2E_ALLOW_WRITE=1 时执行。
# 创建 ZZFB 之后的流程包在 try/finally 里，无论成功或中途失败都 DELETE 本次新建的监测（运行前已有的 id 不动；后端为停用，历史保留）。
ALLOW_WRITE=os.environ.get('E2E_ALLOW_WRITE')=='1'
# 仅用于验证清理逻辑：E2E_FAULT=manual 时让「人工报价」一步用不存在的按钮名并 3 秒超时
FAULT=os.environ.get('E2E_FAULT','')
HERE=pathlib.Path(__file__).resolve().parent
SHOTS=HERE/'shots';SHOTS.mkdir(exist_ok=True)
import asyncio,json,re,urllib.request
def api(path,method='GET'):
  with urllib.request.urlopen(urllib.request.Request(BASE+'/api'+path,method=method)) as f: return json.load(f)
from playwright.async_api import async_playwright
S=str(SHOTS)+'/'
R=[]
def ok(name,cond,extra=''): R.append((name,bool(cond),extra)); print('PASS' if cond else 'FAIL',name,extra)
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch()
    ctx=await b.new_context(timezone_id='Asia/Shanghai',viewport={'width':1400,'height':1000});pg=await ctx.new_page()
    errs=[];pg.on('console',lambda m: m.type=='error' and errs.append(m.text))
    posts=[];pg.on('request',lambda r: r.method=='POST' and '/api/monitors' in r.url and r.url.endswith('/monitors') and posts.append(r.post_data))
    await pg.goto(BASE);await pg.wait_for_timeout(4000)
    zone=await pg.get_by_label('显示时区').input_value()
    txt=await pg.locator('main').inner_text()
    ok('默认浏览器时区 Asia/Shanghai',zone=='Asia/Shanghai',zone)
    ok('时间带后缀 GMT+8',re.search(r'\d{2}:\d{2}:\d{2} GMT\+8',txt),str(re.findall(r'\d{4}/\S+ \d{2}:\d{2}:\d{2} \S+',txt)[:2]))
    ok('显著性说明文案',await pg.get_by_test_id('stats-rule').count()==1)
    await pg.screenshot(path=S+'overview_shanghai.png',full_page=True)
    await pg.get_by_label('显示时区').select_option('UTC');await pg.reload();await pg.wait_for_timeout(3000)
    z2=await pg.get_by_label('显示时区').input_value();txt=await pg.locator('main').inner_text()
    ok('改选 UTC 刷新后保持',z2=='UTC' and ' UTC' in txt,z2)
    await pg.get_by_label('显示时区').select_option('Asia/Shanghai')
    # create modal
    await pg.get_by_role('button',name='新增标的').click();await pg.wait_for_timeout(500)
    ok('新建弹窗默认 manual',await pg.locator('.modal select, [role=dialog] select').first.input_value()=='manual')
    sym=pg.get_by_label('标的代码') if await pg.get_by_label('标的代码').count() else pg.locator('input[autofocus], input').first
    inp=pg.get_by_role('textbox',name='标的代码')
    await inp.fill('!!$#');n=len(posts);await pg.get_by_role('button',name='创建监测').click();await pg.wait_for_timeout(500)
    ce=pg.get_by_test_id('create-error')
    ok('非法字符前端拦截且不发请求',await ce.is_visible() and len(posts)==n,await ce.inner_text() if await ce.count() else '')
    await pg.screenshot(path=S+'invalid_symbol.png')
    sel=pg.get_by_role('combobox',name='数据源')
    if not ALLOW_WRITE:
      print('SKIP futu ZZFUTU 422 校验、创建 ZZFB 及后续依赖步骤（只读模式；需写操作请设置 E2E_ALLOW_WRITE=1）')
      print('console errors',errs[:5]);await b.close();print('SUMMARY',sum(r[1] for r in R),'/',len(R));return
    await sel.select_option('futu');await inp.fill('ZZFUTU');await pg.get_by_role('button',name='创建监测').click();await pg.wait_for_timeout(1500)
    t=await ce.inner_text() if await ce.count() else ''
    ok('422 弹窗内可读错误',await ce.is_visible() and '富途支持' in t and '[{' not in t,t)
    # visibility: top element at error center is inside modal
    box=await ce.bounding_box();top=await pg.evaluate("([x,y])=>!!document.elementFromPoint(x,y)?.closest('[data-testid=create-error]')",[box['x']+box['width']/2,box['y']+box['height']/2])
    ok('错误未被遮罩挡住',top)
    await pg.screenshot(path=S+'futu_422_in_modal.png')
    before={m['id'] for m in api('/monitors')}
    try:
      await sel.select_option('manual');await inp.fill('  zzfb  ');await pg.get_by_role('button',name='创建监测').click();await pg.wait_for_timeout(2000)
      body=json.loads(posts[-1]);ok('前后空格被 trim',body['symbol']=='ZZFB' and body['underlyingId']=='ZZFB',posts[-1][:80])
      ok('成功后显示已创建',await pg.locator('.message').filter(has_text='已创建').count()>0)
      await pg.screenshot(path=S+'created.png')
      await pg.get_by_role('button',name='新增标的').click();await pg.wait_for_timeout(500)
      ok('再次打开弹窗旧绿条消失',await pg.locator('.message').filter(has_text='已创建').count()==0)
      await pg.keyboard.press('Escape');await pg.wait_for_timeout(300)
      if await pg.get_by_role('button',name='创建监测').count(): await pg.locator('[aria-label=关闭], button:has-text("×")').first.click()
      # manual panel times (ZZFB selected, tab settings) -> go to 人工报价
      await pg.wait_for_timeout(1000)
      await pg.get_by_role('button',name='人工报价' if FAULT!='manual' else '人工报价-故障注入').first.click(timeout=3000 if FAULT=='manual' else None);await pg.wait_for_timeout(1500)
      import datetime
      vals=await pg.locator('input').evaluate_all("e=>e.map(x=>x.value)")
      isos=[v for v in vals if re.match(r'\d{4}-\d\d-\d\dT',v)]
      now=datetime.datetime.now(datetime.timezone.utc)
      d0=abs((datetime.datetime.fromisoformat(isos[0].replace('Z','+00:00'))-now).total_seconds()) if isos else 1e9
      ok('人工面板输入时刻为当前',d0<30,f'{isos} diff={d0:.1f}s')
      await pg.screenshot(path=S+'manual_panel.png',full_page=True)
      # invalid timestamp -> —  : intercept history to return invalid valuationAt
      await pg.route('**/api/results/latest',lambda r: r.fulfill(status=200,content_type='application/json',body=json.dumps({})))
      await pg.route('**/api/monitors/*/statistics*',lambda r: r.fulfill(status=200,content_type='application/json',body=json.dumps({'status':'INSUFFICIENT_SAMPLES','n':3,'statsAsOf':'garbage','reason':'采集中断','lastGapAt':'2026-10-26T05:40:00'})))
      await pg.get_by_role('button',name='SPY').first.click();await pg.get_by_role('button',name='监测总览').first.click();await pg.wait_for_timeout(2500)
      txt=await pg.locator('main').inner_text()
      ok('非法时间显示—，无 Invalid Date','统计截至 —' in txt and 'Invalid Date' not in txt)
      ok('可选 reason/lastGapAt 显示且无时区按UTC','原因：采集中断' in txt and '最近一次中断：2026/10/26 13:40:00 GMT+8' in txt)
      await pg.screenshot(path=S+'stats_optional_fields.png',full_page=True)
    finally:
      created=[m['id'] for m in api('/monitors') if m['id'] not in before]
      for mid in created: api(f'/monitors/{mid}','DELETE')
      left=[m['id'] for m in api('/monitors') if m['id'] in created and m.get('active')]
      print('CLEANUP 本次新建',created,'清理后仍 active',len(left))
      ok('清理：本次新建的监测已 DELETE（停用）',len(created)==1 and not left,str(created))
    print('console errors',[e for e in errs if '422' not in e][:5])
    await b.close()
  print('SUMMARY',sum(r[1] for r in R),'/',len(R))
asyncio.run(main())
