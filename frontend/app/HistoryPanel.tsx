'use client';
import { Download } from 'lucide-react';
import { statusLabels,fmt,api } from './lib';
import type { Dashboard } from './useDashboard';
export function HistoryPanel({d}:{d:Dashboard}){
 const {selected,history,frozen,viewMode,setRaw,time,run}=d;
 return (
<section className="history-panel"><div className="section-heading"><h3>已保存快照 <span>APPEND ONLY</span></h3><div className="actions"><a className="btn btn-outline" href={`/api/exports/${selected}?format=csv`}><Download size={14}/>CSV</a><a className="btn btn-outline" href={`/api/exports/${selected}`}><Download size={14}/>完整审计 JSON</a></div></div><div className="table-scroll"><table><thead><tr>{['快照','估值时刻','配置 / 源','反推均价','档位','状态','原始输入'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{[...history].reverse().map(h=><tr key={h.id}><td>#{h.snapshotId}</td><td>{time(h.valuationAt)}</td><td>v{h.configVersion} · {h.feedType}{h.historical?' · 历史导入':''}</td><td>{fmt(h.modes[viewMode]?.rawMean)}</td><td>{h.composition.join(', ')||'—'}</td><td>{h.duplicate?'重复快照 · 不入统计':statusLabels[h.status]||h.status}</td><td><button className="link" onClick={()=>run(async()=>setRaw(await api(`/snapshots/${h.snapshotId}`)))}>查看输入</button></td></tr>)}</tbody></table></div><p className="fine">仅保留当天数据（美东交易日），前一交易日数据每日自动清理；界面展示最近200条，完整导出包含所有原始报价、配置、配对、分钟块、统计、锁定观察和后续结果。旧结果保留原时刻。</p><div className="frozen-list"><h3>已锁定研究观察</h3>{frozen.length?frozen.map(f=><div key={f.id}><span>#{f.id} · 快照 {f.result.snapshotId} · {time(f.frozenAt)} · {f.result.feedType}</span><button className="link" onClick={()=>setRaw({snapshotId:f.result.snapshotId,...f})}>查看冻结内容</button></div>):<p className="muted">尚未锁定观察</p>}</div></section>
 );
}
