'use client';
import { Button } from '@/components/ui/button';
import { Field } from './ui';
import type { Dashboard } from './useDashboard';
export function CreateModal({d}:{d:Dashboard}){
 const {busy,setShowCreate,newSymbol,setNewSymbol,newProvider,setNewProvider,create}=d;
 return (
<div className="modal-backdrop"><div className="modal" role="dialog" aria-label="新建监测对象"><div className="section-heading"><h2>新建监测对象</h2><button onClick={()=>setShowCreate(false)}>×</button></div><Field title="标的代码"><input autoFocus value={newSymbol} onChange={e=>setNewSymbol(e.target.value)}/></Field><Field title="数据源"><select value={newProvider} onChange={e=>setNewProvider(e.target.value)}><option value="manual">人工输入 · 无需 API</option><option value="futu">富途 OpenD · 美股 / ETF</option><option value="http">HTTP 行情网关</option></select></Field><p className="fine">初始选档数量为0；请设置档数及真实到期日。</p>{newProvider==='futu'&&<p className="fine">支持 SPY、QQQ、IWM、DIA、AAPL、MSFT、NVDA、AMZN、META、GOOGL、TSLA、AMD、AVGO、NFLX、PLTR、COIN、JPM、BAC、XOM、GLD、SOXX。请先登录本机 OpenD，并确认美股正股和 OPRA 期权权限。创建后在配置页测试连接；无权限时显示具体原因。</p>}<Button disabled={busy||!newSymbol.trim()} onClick={create}>创建监测</Button></div></div>
 );
}
export function RawModal({d}:{d:Dashboard}){
 const {setRaw}=d;
 const raw=d.raw!;
 return (
<div className="modal-backdrop"><div className="modal raw-modal"><div className="section-heading"><h2>原始输入追溯 #{raw.snapshotId}</h2><button onClick={()=>setRaw(null)}>关闭</button></div><pre>{JSON.stringify(raw,null,2)}</pre></div></div>
 );
}
