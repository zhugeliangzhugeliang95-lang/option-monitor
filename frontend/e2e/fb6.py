import os,pathlib
BASE=os.environ.get('E2E_BASE_URL','').rstrip('/')
if not BASE:
  import sys
  sys.stderr.write('用法：E2E_BASE_URL=http://localhost:<前端端口> [E2E_ALLOW_WRITE=1] python frontend/e2e/%s\n'
    'E2E_BASE_URL 必填（无默认值，避免误打到别人的栈）；只应指向自己用临时 DATABASE_PATH 起的测试栈。\n' % __import__('os').path.basename(__file__))
  sys.exit(2)
HERE=pathlib.Path(__file__).resolve().parent
SHOTS=HERE/'shots';SHOTS.mkdir(exist_ok=True)
import asyncio,json,re
from playwright.async_api import async_playwright
import urllib.request
R=[]
# 依赖：后端里有一个 symbol=SPY 的启用监测（MOCK 默认种子数据里有）；按 symbol 查 id，不写死
SPY=next(m['id'] for m in json.load(urllib.request.urlopen(BASE+'/api/monitors')) if m.get('config',{}).get('symbol')=='SPY' and m.get('active'))
def ok(n,c,x=''): R.append(c); print('PASS' if c else 'FAIL',n,x)
CASES=[
 ('OPEND_OFFLINE',{'status':'OPEND_OFFLINE','chain':{'state':'warming'}},None,['OpenD 未启动或无法连接'],['11111'],'true'),
 ('B1_OFFLINE_op',{'status':'OPEND_OFFLINE','operation':'get_market_snapshot'},None,['OpenD 未启动或无法连接'],[],'true'),
 ('B1_OFFLINE_noop',{'status':'OPEND_OFFLINE','operation':None},None,['OpenD 未启动或无法连接'],[],'true'),
 ('B1_TIMEOUT_noop',{'status':'TIMEOUT'},None,['富途请求超时，稍后自动重试'],['（'],'true'),
 ('B1_TIMEOUT_unknown',{'status':'TIMEOUT','operation':'unknown'},None,['富途请求超时，稍后自动重试'],['unknown','（'],'true'),
 ('N1_dollar',{'status':'TIMEOUT','operation':"a$'b$&c$`d$$e"},None,["富途请求超时（a$'b$&c$`d$$e），稍后自动重试"],['{'],'true'),
 ('N3_unknown_op',{'status':'TIMEOUT','operation':'unknown','pendingSeconds':3},None,['富途请求超时，稍后自动重试'],['unknown','（'],'true'),
 ('N3_unknown_unresp',{'status':'OPEND_UNRESPONSIVE','operation':'unknown','pendingSeconds':9},None,['OpenD 无响应：请求已卡住 9 秒'],['unknown'],'true'),
 ('N2_long_op',{'status':'TIMEOUT','operation':'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'},None,['富途请求超时（'],[],'true'),
 ('OPEND_UNRESPONSIVE',{'status':'OPEND_UNRESPONSIVE','operation':'get_market_snapshot','pendingSeconds':61.4},None,['OpenD 无响应：请求（get_market_snapshot）已卡住 61 秒，未重建连接，请检查 OpenD'],['60 秒','{'],'true'),
 ('OPEND_UNRESPONSIVE_nosec',{'status':'OPEND_UNRESPONSIVE'},None,['OpenD 无响应：请求未完成，未重建连接，请检查 OpenD'],['{','undefined','卡住','（'],'true'),
 ('TIMEOUT',{'status':'TIMEOUT','operation':'deadline'},None,['富途请求超时（deadline），稍后自动重试'],['{'],'true'),
 ('FUTU_BUSY',{'status':'FUTU_BUSY','operation':'busy','pendingSeconds':12},None,['富途通道忙：上一请求尚未完成，本轮跳过','非失败'],[],'false'),
 ('CHAIN_WARMING',{'status':'CHAIN_WARMING','chain':{'state':'warming','reason':'retrying','waitingSeconds':30,'attempts':2,'lastError':{'code':'RATE_LIMITED'}}},None,['期权链加载中（已等待 30 秒），稍后出数','非失败 · 自动重试'],['等待开盘'],'false'),
 ('CHAIN_WARMING_OFF_HOURS',{'status':'CHAIN_WARMING','operation':'get_option_chain','chain':{'state':'warming','reason':'offHours','waitingSeconds':0}},None,['盘外，等待开盘前预热（约 09:00 美东）','非失败 · 等待开盘'],['已等待','自动重试'],'false'),
 ('CHAIN_WARMING_nowait',{'status':'CHAIN_WARMING','operation':'get_option_chain','chain':{'state':'warming','reason':'queued'}},None,['期权链加载中，稍后出数'],['（','{'],'false'),
 ('FUTU_BUSY_maint',{'status':'FUTU_BUSY','operation':'maintenance'},None,['富途通道忙：上一请求尚未完成，本轮跳过'],['处理中'],'false'),
 ('CHAIN_UNAVAILABLE',{'status':'CHAIN_UNAVAILABLE','chain':{'lastError':{'code':'FUTU_REQUEST_FAILED','ret':-1},'waitingSeconds':640,'attempts':9}},None,['期权链长时间无法获取（错误码 FUTU_REQUEST_FAILED），请检查权限或 OpenD'],[],'true'),
 ('CHAIN_UNAVAILABLE_nocode',{'status':'CHAIN_UNAVAILABLE','chain':{}},None,['期权链长时间无法获取，请检查权限或 OpenD'],['错误码','{'],'true'),
 ('UNKNOWN_msg',{'status':'NEW_FUTURE','chain':{},'message':'未来状态说明'},None,['未来状态说明'],[],'unknown'),
 ('UNKNOWN_code',{'status':'NEW_FUTURE2','chain':{}},None,['NEW_FUTURE2'],[],'unknown'),
 ('chain_stale_provider',{'status':'OK','chain':{'stale':True,'ageSeconds':420}},None,['使用 420 秒前的期权链（后台刷新中）'],['分钟'],None),
 ('chain_stale_result+expStale',None,{'chain':{'stale':True,'ageSeconds':125,'expirationsStale':True}},['使用 125 秒前的期权链（后台刷新中）','到期日列表为前一交易日'],['OpenD 暂不可用'],None),
 ('stale_no_age',{'status':'OK','chain':{'stale':True}},None,['使用缓存的期权链（后台刷新中）'],['秒前','undefined'],None),
 ('both_sources_one_badge',{'status':'OK','chain':{'stale':True,'ageSeconds':30,'fetchedAt':'2026-10-26T08:00:00+00:00','refreshFailures':1,'expirationsStale':False}},{'chain':{'stale':True,'ageSeconds':90}},['使用 30 秒前的期权链（后台刷新中）'],['90 秒前'],None),
 ('expStale_only',{'status':'OK','chain':{'stale':False,'ageSeconds':10,'expirationsStale':True}},None,['到期日列表为前一交易日'],['秒前的期权链'],None),
 ('offHours_bool_ignored',{'status':'CHAIN_WARMING','chain':{'state':'warming','reason':'queued','offHours':True,'waitingSeconds':5}},None,['期权链加载中（已等待 5 秒），稍后出数'],['盘外'],'false'),
]
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch();ctx=await b.new_context(timezone_id='Asia/Shanghai',viewport={'width':1400,'height':1000})
    st={'prov':None,'res':None};errs=[]
    async def hp(route):
      r=await route.fetch();j=await r.json()
      if st['prov'] is not None: j['monitors'][SPY]={**j['monitors'].get(SPY,{}),**st['prov']}
      await route.fulfill(response=r,body=json.dumps(j))
    async def hr(route):
      r=await route.fetch();j=await r.json()
      if st['res'] is not None and isinstance(j,dict) and SPY in j: j[SPY]={**j[SPY],**st['res']}
      await route.fulfill(response=r,body=json.dumps(j))
    await ctx.route(re.compile(r'/api/providers/status'),hp);await ctx.route(re.compile(r'/api/results/latest'),hr)
    async def strip(prov,res):
      st['prov'],st['res']=prov,res;pg=await ctx.new_page();pg.on('console',lambda m: m.type=='error' and errs.append(m.text))
      await pg.goto(BASE);await pg.wait_for_timeout(2500)
      await pg.get_by_role('button',name='SPY').first.click();await pg.wait_for_timeout(800)
      q=pg.locator('.quality-strip').first;t=await q.inner_text();fs=pg.get_by_test_id('futu-status')
      f=await fs.get_attribute('data-failure') if await fs.count() else None
      st['dot']=await pg.get_by_test_id('status-dot').first.evaluate('e=>getComputedStyle(e).backgroundColor') if await pg.get_by_test_id('status-dot').count() else None
      st['col']=await fs.evaluate('e=>getComputedStyle(e).color') if await fs.count() else None
      st['ovf']=await pg.evaluate("()=>{const q=document.querySelector('.quality-strip');return q.scrollWidth>q.clientWidth+1||document.documentElement.scrollWidth>document.documentElement.clientWidth+1}")
      return pg,t,f,await pg.get_by_test_id('chain-badge').count()
    pg,base,f,nb=await strip(None,None);await pg.close()
    ok('无新字段：无 futu-status、无徽标',f is None and nb==0,base)
    for name,prov,res,has,no,fail in CASES:
      pg,t,f,nb=await strip(prov,res)
      c=(name!='both_sources_one_badge' or nb==1) and all(h in t for h in has) and not any(n in t for n in no) and (fail is None or f==fail) and '[object' not in t
      RED='rgb(229, 120, 122)';AMB='rgb(217, 179, 115)'
      if fail=='true': c=c and st['dot']==RED and st['col']==RED
      elif fail in('false','unknown') or fail is None: c=c and st['dot']==AMB and st['col']!=RED
      if name=='N2_long_op': c=c and not st['ovf']
      ok(name,c,f'{t[:120]!r} failure={f} dot={st["dot"]} col={st["col"]}')
      if name in('CHAIN_UNAVAILABLE','CHAIN_WARMING','N2_long_op'):
        for cs in('dark','light'):
          await pg.emulate_media(color_scheme=cs);await pg.wait_for_timeout(200)
          await pg.screenshot(path=str(SHOTS)+f'/futu_{name}_{cs}.png')
      if name in('CHAIN_UNAVAILABLE','chain_stale_result+expStale'): await pg.screenshot(path=str(SHOTS)+f'/futu_{name}.png')
      await pg.close()
    pg,t2,f,nb=await strip(None,None);await pg.close()
    ok('恢复无字段后与基线一致（去掉时间）',f is None and nb==0 and t2.split('\n')[0]==base.split('\n')[0],t2)
    for tab in('监测配置','历史与追溯'):
      pg,_,_,_=await strip({'status':'OPEND_OFFLINE'},None)
      bt=pg.get_by_role('button',name=re.compile(tab)).first
      if await bt.count(): await bt.click();await pg.wait_for_timeout(800)
      body=await pg.inner_text('body');ok(f'{tab}页正常',bool(await bt.count()) and 'undefined' not in body and '[object' not in body and '{operation}' not in body)
      await pg.close()
    ok('控制台无 error',not errs,str(errs[:2]))
    await b.close()
  print('SUMMARY',sum(R),'/',len(R))
asyncio.run(main())
