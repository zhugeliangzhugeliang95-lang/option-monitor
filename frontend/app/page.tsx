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
import { CreateModal,AskModal } from './Modals';
export default function Dashboard(){
 const d=useDashboard();
 const {openCreate,monitors,selected,provider,tab,error,setError,loadError,setLoadError,detailError,setDetailError,notice,setNotice,connected,zone,setZone,busy,showCreate,setShowCreate,setNewProvider,ask,current,run,cfg,result}=d;
 return <div className="app-shell">
   <Sidebar d={d}/>
  <main><header className="topbar"><div><span className="muted">工作台</span><ChevronRight size={13}/><span>{{overview:'监测总览',manual:'人工报价',settings:'监测配置'}[tab]}</span></div><div><Connection connected={connected} heartbeatAt={provider.worker?.heartbeatAt}/><span className="divider"/><select aria-label="显示时区" value={zone} onChange={e=>setZone(e.target.value)}><option value="America/New_York">纽约时间</option><option value="Asia/Shanghai">北京时间</option><option value="UTC">UTC</option>{!['America/New_York','Asia/Shanghai','UTC'].includes(zone)&&<option value={zone}>{zone}</option>}</select></div></header>
  <div className="workspace">
   <div className="page-title"><div><div className="eyebrow">OPTIONS · IMPLIED SPOT RESEARCH</div><h1>{{overview:'监测总览',manual:'人工报价',settings:'监测配置'}[tab]}</h1><p>同期限、同行权价的 Call − Put + K × e<sup>−rT</sup></p></div><div className="actions"><Button variant="outline" onClick={()=>openCreate('futu')}><Radio size={15}/>连接富途</Button><Button variant="outline" onClick={()=>openCreate()}><Plus size={15}/>新增标的</Button>{selected&&<Button disabled={busy} onClick={()=>run(async()=>{const r=await api(`/monitors/${selected}/refresh`,{});setNotice(statusLabels[r.status]||'已排队；与定时采集共用下一10秒槽。');})}><RefreshCw size={15} className={busy?'spin':''}/>刷新报价</Button>}</div></div>
   {(error||loadError||detailError)&&<div className="message error" role="alert">{[error,loadError,detailError].filter(Boolean).join('；')}<button onClick={()=>{setError('');setLoadError('');setDetailError('');}}>×</button></div>}{notice&&<div className="message" role="status">{notice}<button onClick={()=>setNotice('')}>×</button></div>}
   {!current?<div className="empty-state"><Activity size={42}/><h2>从一个监测对象开始</h2><p>人工输入即可计算；也可连接富途 OpenD 或 HTTP 行情网关。</p><Button onClick={()=>openCreate()}><Plus size={16}/>创建监测对象</Button></div>:<>
    {tab==='overview'&&<FocusWatch d={d}/>}
    {tab==='overview'&&monitors.length>1&&<MultiOverview d={d}/>}
   <div className="instrument-bar"><div><h2>{cfg.symbol}</h2><span className="tag">{cfg.exchange}</span><span className={'tag '+(result?.feedType==='MOCK'||cfg.provider==='mock'?'amber':'')}>{result?.feedType||(cfg.provider==='futu'?'FUTU · 待核验':cfg.dataMode)}</span><span className="muted">{cfg.expiryMode==='nearest'?`到期日：${!result?.oldConfiguration&&result?.expiration?result.expiration:'等待行情确认'} · 最近一期`:(cfg.expiryMode==='sameDay'?'当日到期 0DTE':`到期日：${cfg.expiration}`)}</span></div><span className="mono muted">v{current.version} · {current.hash.slice(0,8)}</span></div>
   {cfg.provider==='futu'&&<div className="message"><div><strong>富途 OpenD · 只读行情</strong><p>保持本机 OpenD 登录。真实报价到达前不展示模拟价格；休市或报价过旧时保留原时间。</p><p>当前接入快照：不含买卖盘独立事件时间及期权截止时刻，无法核实盘口同步性。最近一期模式默认以所选到期日的常规收盘时间作为定价假设（含半日市），随到期日自动更新；也可在配置中改用明确时刻或剩余天数。</p></div></div>}
   {cfg.provider==='mock'&&<div className="message">MOCK 演示：当前价格由程序生成，不是富途或交易所行情。</div>}
    {tab==='overview'&&<OverviewMain d={d}/>}
    {tab==='manual'&&<ManualPanel d={d}/>}
    {tab==='settings'&&<SettingsPanel d={d}/>}
   </>}
   <footer className="app-footer"><span>PARITY LAB / RESEARCH TERMINAL</span><span>目标节拍 10s · {zone} · 无自动下单</span></footer>
  </div></main>
  {showCreate&&<CreateModal d={d}/>}
  {ask&&<AskModal d={d}/>}
 </div>;
}
