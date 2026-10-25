'use client';
import { Activity,Plus,RefreshCw,Radio,ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { statusLabels,api } from './lib';
import { Connection } from './ui';
import { useDashboard,type Dashboard } from './useDashboard';
import { Sidebar } from './Sidebar';
import { FocusWatch,MultiOverview,OverviewMain } from './Overview';
import { ManualPanel } from './ManualPanel';
import { SettingsPanel } from './SettingsPanel';
import { HistoryPanel } from './HistoryPanel';
import { CreateModal,AskModal,RawModal } from './Modals';
export default function Dashboard(){
 const d=useDashboard();
 const {monitors,selected,provider,history,tab,error,setError,notice,setNotice,connected,zone,setZone,busy,showCreate,setShowCreate,setNewProvider,raw,ask,current,run,cfg,result}=d;
 return <div className="app-shell">
   <Sidebar d={d}/>
  <main><header className="topbar"><div><span className="muted">工作台</span><ChevronRight size={13}/><span>{{overview:'监测总览',manual:'人工报价',history:'历史与追溯',settings:'监测配置'}[tab]}</span></div><div><Connection connected={connected} heartbeatAt={provider.worker?.heartbeatAt}/><span className="divider"/><select aria-label="显示时区" value={zone} onChange={e=>setZone(e.target.value)}><option value="America/New_York">纽约时间</option><option value="Asia/Shanghai">北京时间</option><option value="UTC">UTC</option></select></div></header>
  <div className="workspace">
   <div className="page-title"><div><div className="eyebrow">OPTIONS · IMPLIED SPOT RESEARCH</div><h1>{{overview:'监测总览',manual:'人工报价',history:'历史与追溯',settings:'监测配置'}[tab]}</h1><p>同期限、同行权价的 Call − Put + K × e<sup>−rT</sup></p></div><div className="actions"><Button variant="outline" onClick={()=>{setNewProvider('futu');setShowCreate(true);}}><Radio size={15}/>连接富途</Button><Button variant="outline" onClick={()=>setShowCreate(true)}><Plus size={15}/>新增标的</Button>{selected&&<Button disabled={busy} onClick={()=>run(async()=>{const r=await api(`/monitors/${selected}/refresh`,{});setNotice(statusLabels[r.status]||'已排队；与定时采集共用下一10秒槽。');})}><RefreshCw size={15} className={busy?'spin':''}/>刷新报价</Button>}</div></div>
   {error&&<div className="message error" role="alert">{error}<button onClick={()=>setError('')}>×</button></div>}{notice&&<div className="message" role="status">{notice}<button onClick={()=>setNotice('')}>×</button></div>}
   {!current?<div className="empty-state"><Activity size={42}/><h2>从一个监测对象开始</h2><p>人工输入即可计算。也可以选择 MOCK，验证完整采集与统计流程。</p><Button onClick={()=>setShowCreate(true)}><Plus size={16}/>创建监测对象</Button></div>:<>
    {tab==='overview'&&<FocusWatch d={d}/>}
    {tab==='overview'&&monitors.length>1&&<MultiOverview d={d}/>}
   <div className="instrument-bar"><div><h2>{cfg.symbol}</h2><span className="tag">{cfg.exchange}</span><span className={'tag '+(result?.feedType==='MOCK'||cfg.provider==='mock'?'amber':'')}>{result?.feedType||(cfg.provider==='futu'?'FUTU · 待核验':cfg.dataMode)}</span><span className="muted">{cfg.expiryMode==='sameDay'?'当日到期 0DTE':cfg.expiration}</span></div><span className="mono muted">v{current.version} · {current.hash.slice(0,8)}</span></div>
   {cfg.provider==='futu'&&<div className="message"><div><strong>富途 OpenD · 只读行情</strong><p>保持本机 OpenD 登录。真实报价到达前不展示模拟价格；休市或报价过旧时保留原时间。</p><p>当前接入快照：不含买卖盘独立事件时间及期权截止时刻，暂不进入严格实时统计。计算反推参考价需在配置里填写定价参考时刻和明确假设，或剩余天数。</p></div></div>}
   {cfg.provider==='mock'&&<div className="message">MOCK 演示：当前价格由程序生成，不是富途或交易所行情。</div>}
    {tab==='overview'&&<OverviewMain d={d}/>}
    {tab==='manual'&&<ManualPanel d={d}/>}
    {tab==='settings'&&<SettingsPanel d={d}/>}
    {tab==='history'&&<HistoryPanel d={d}/>}
   </>}
   <footer className="app-footer"><span>PARITY LAB / RESEARCH TERMINAL</span><span>目标节拍 10s · {zone} · 无自动下单</span></footer>
  </div></main>
  {showCreate&&<CreateModal d={d}/>}
  {ask&&<AskModal d={d}/>}
  {raw&&<RawModal d={d}/>}
 </div>;
}
