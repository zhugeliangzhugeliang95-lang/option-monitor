'use client';
import { Button } from '@/components/ui/button';
import { Field,Modal } from './ui';
import type { Dashboard } from './useDashboard';
export function CreateModal({d}:{d:Dashboard}){
 const {busy,setShowCreate,newSymbol,setNewSymbol,newProvider,setNewProvider,newBelow,setNewBelow,newAbove,setNewAbove,create}=d;
 return (
<Modal title="新建监测对象" onClose={()=>setShowCreate(false)}><Field title="标的代码"><input autoFocus value={newSymbol} onChange={e=>setNewSymbol(e.target.value)}/></Field><Field title="数据源"><select value={newProvider} onChange={e=>setNewProvider(e.target.value)}><option value="manual">人工输入 · 无需 API</option><option value="futu">富途 OpenD · 美股 / ETF</option><option value="http">HTTP 行情网关</option></select></Field><Field title="低于现价档数（nBelow）"><input type="number" min="0" max="50" value={newBelow} onChange={e=>setNewBelow(Number(e.target.value))}/></Field><Field title="高于现价档数（nAbove）"><input type="number" min="0" max="50" value={newAbove} onChange={e=>setNewAbove(Number(e.target.value))}/></Field><p className="fine">默认现价以下、以上各 5 档；创建后可在配置页调整，并设置真实到期日。</p>{newProvider==='futu'&&<p className="fine">支持 SPY、QQQ、IWM、DIA、AAPL、MSFT、NVDA、AMZN、META、GOOGL、TSLA、AMD、AVGO、NFLX、PLTR、COIN、JPM、BAC、XOM、GLD、SOXX。请先登录本机 OpenD，并确认美股正股和 OPRA 期权权限。创建后在配置页测试连接；无权限时显示具体原因。</p>}<Button disabled={busy||!newSymbol.trim()} onClick={create}>创建监测</Button></Modal>
 );
}
export function RawModal({d}:{d:Dashboard}){
 const {setRaw}=d;
 const raw=d.raw!;
 return (
<Modal className="raw-modal" closeText="关闭" title={`原始输入追溯 #${raw.snapshotId}`} onClose={()=>setRaw(null)}><pre>{JSON.stringify(raw,null,2)}</pre></Modal>
 );
}
export function AskModal({d}:{d:Dashboard}){
 const {setAsk}=d;
 const ask=d.ask!;
 return (
<Modal title={ask.title} onClose={()=>setAsk(null)}><p>{ask.body}</p><div className="actions"><Button variant="outline" onClick={()=>setAsk(null)}>取消</Button><Button onClick={()=>{const a=ask.action;setAsk(null);a();}}>{ask.ok}</Button></div></Modal>
 );
}
