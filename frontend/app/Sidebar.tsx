'use client';
import { Activity,Plus,Settings2,Table2,History,ChevronRight,FlaskConical } from 'lucide-react';
import { fmt } from './lib';
import type { Dashboard } from './useDashboard';
export function Sidebar({d}:{d:Dashboard}){
 const {monitors,selected,setSelected,latest,history,tab,setTab,setPreview,setShowCreate}=d;
 return (
  <aside className="sidebar"><div className="brand"><div className="brand-mark"><Activity size={23}/></div><div>PARITY LAB<small>期权反推价格监测台</small></div></div>
   <div className="workspace-label">RESEARCH WORKSPACE <span>01</span></div>
   <nav>{[['overview','监测总览',Activity],['manual','人工报价',Table2],['history','历史与追溯',History],['settings','监测配置',Settings2]].map(([id,label,Icon]:any)=><button key={id} className={tab===id?'nav-active':''} onClick={()=>setTab(id)}><Icon size={17}/>{label}<ChevronRight size={13}/></button>)}</nav>
   <div className="watch-header">监测列表 <button aria-label="新增监测对象" onClick={()=>setShowCreate(true)}><Plus size={16}/></button></div>
   <div className="watch-list">{monitors.map(m=>{const l=latest[m.id],a=l?.modes?.[m.config.optionPricePreset];return <button key={m.id} className={'watch '+(selected===m.id?'watch-active':'')} onClick={()=>{setSelected(m.id);setPreview(null);}}><div><strong>{m.config.symbol}</strong><span className={'tag '+(m.config.provider==='mock'?'amber':'')}>{m.config.provider==='futu'?'FUTU':m.config.dataMode}</span></div><div><span>{m.config.exchange} · {m.config.currency}</span><b>{fmt(a?.rawMean,2)}</b></div></button>})}{!monitors.length&&<p className="muted empty-side">还没有监测对象<br/>点击 + 开始研究</p>}</div>
   <div className="sidebar-bottom"><FlaskConical size={18}/><div>研究模式<small>只读行情 · 无交易接口</small></div></div>
  </aside>
 );
}
