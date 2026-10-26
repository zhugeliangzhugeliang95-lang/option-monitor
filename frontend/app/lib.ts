export type Obj=Record<string,any>;
// 统计接口：reason / lastGapAt 为后端可选字段，前端不依赖其存在
export type StatsReason=string|{code:string;message?:string;details?:unknown}|null;
export type Statistics=Obj&{reason?:StatsReason;lastGapAt?:string|null;consecutiveMinutes?:number;neededMinutes?:number;minSlotsPerMinute?:number};
// 统计 reason.code → 界面文案（以 code 为准；后端 message 仅作未知 code 兜底；details 不显示）。对照 backend/app/statistics.py REASON_TEXT 与 docs/API.md
export const reasonLabels:Record<string,string>={NO_DATA:'暂无数据：当前数据流还没有采集到观测',DATA_STALE:'数据已停止更新：最近一个完整分钟没有观测（采集可能已中断）',GAP_IN_WINDOW:'窗口内有缺口：连续分钟被中断，需重新累计',TOO_FEW_MINUTES:'连续分钟不足：采集时间尚短或部分分钟槽位不足',MISSING_EVENT_TIME:'行情缺少事件时间，不满足严格条件',NOT_STRICT_ELIGIBLE:'观测不满足严格条件（如休市 / 会话关闭）',ZERO_SE:'标准误为零，无法检验',NONFINITE:'数据中存在非有限值'};
// 兼容字符串与对象；code=OK 不视为问题（返回空）
export const reasonText=(r:unknown):string=>{if(r==null||r==='')return '';if(typeof r==='string')return r==='OK'?'':reasonLabels[r]||r;if(typeof r==='object'){const o=r as {code?:unknown;message?:unknown};const code=typeof o.code==='string'?o.code:'';if(code==='OK')return '';if(code&&reasonLabels[code])return reasonLabels[code];if(typeof o.message==='string'&&o.message)return o.message;return code;}return '';};
const isNum=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
export const numOr=(v:unknown,d:number)=>isNum(v)?v:d;
export {isNum};
export const focusSymbols=['TSLA','NVDA','META','AMZN','GOOGL','AAPL','MSFT'];
export const focusRank=(m:Obj)=>{const rank=focusSymbols.indexOf(m.config.symbol);return rank<0?focusSymbols.length:rank;};
export const bases=['BID','ASK','MID','LAST','CUSTOM'];
export const labels:Obj={BID:'买一',ASK:'卖一',MID:'中间价',LAST:'最新成交',CUSTOM:'自定义'};
export const statusLabels:Obj={FUTU_RIGHTS_READY:'富途权限就绪 · 等待报价核验',FUTU_OPTION_PERMISSION_REQUIRED:'缺少 OpenAPI 美股期权权限',FUTU_STOCK_PERMISSION_REQUIRED:'缺少 OpenAPI 美股正股权限',OPEND_OFFLINE:'OpenD 未启动或无法连接',OPEND_LOGIN_REQUIRED:'请登录 OpenD 并完成协议',FUTU_SDK_MISSING:'富途 SDK 未安装',FUTU_BUSY:'富途请求处理中',FUTU_REQUEST_FAILED:'富途请求失败',CONTRACT_METADATA_MISMATCH:'合约资料不匹配',UNSUPPORTED_SYMBOL:'不在已配置的美股/ETF标的中',EXPIRED:'合约已过期',MANUAL_PRICE_ONLY:'人工快照 · 价格计算',CLOSED_OR_UNKNOWN_SESSION:'休市 / 交易时段未确认',PAUSED:'采集已暂停',PLEASE_SET_COUNTS:'请设置选档数量',NO_SAME_DAY_EXPIRY:'今天没有可用到期期权',PARTIAL_OR_INELIGIBLE:'部分档位 / 质量未通过',INVALID_SELECTION_ANCHOR:'选档成交价过旧或缺时间',MISSING_ANCHOR:'缺少选档成交价',NOT_CONFIGURED:'未配置 API',WAITING_WORKER:'等待采集',MANUAL_INPUT_REQUIRED:'等待人工输入',RATE_LIMITED:'供应商限流',TIMEOUT:'请求超时',PERMISSION_DENIED:'行情权限不足',CONNECTION_OR_PROTOCOL_ERROR:'行情连接中断',INSUFFICIENT_SAMPLES:'样本积累中',ZERO_SE:'方差为零 / SE 过小',OK:'计算完成',OPEND_UNRESPONSIVE:'OpenD 无响应，未重建连接，请检查 OpenD',CHAIN_WARMING:'期权链加载中，稍后出数',CHAIN_WARMING_OFF_HOURS:'盘外，等待开盘前预热（约 09:00 美东）',CHAIN_UNAVAILABLE:'期权链长时间无法获取，请检查权限或 OpenD'};
// ---- Futu 超时方案 v5「用户可见状态（最终）」前端准备：后端字段全部可选，没有时不显示任何新东西 ----
export type ChainLastError={code?:string;ret?:number|string;at?:string};
export type ChainInfo={state?:'warming'|'unavailable'|(string&{});reason?:'queued'|'retrying'|'offHours'|(string&{});stale?:boolean;ageSeconds?:number;fetchedAt?:string;refreshFailures?:number;expirationsStale?:boolean;waitingSeconds?:number;attempts?:number;lastError?:ChainLastError|null;droppedReason?:'MAX_AGE'|(string&{})};
export type ProviderStatusRow=Obj&{status?:string;operation?:string|null;pendingSeconds?:number;busyCycles?:number;message?:string;chain?:ChainInfo|null};
// 以后端 #16 docs/API.md「Futu 用户可见状态（前端契约）」为准。占位 {operation} {pendingSeconds} {waitingSeconds} {code}；
// 字段缺失时省略对应括号（OPEND_UNRESPONSIVE 缺 pendingSeconds 时省略「已卡住 N 秒，」）。不写死秒数和端口。
// CHAIN_WARMING_OFF_HOURS（chain.reason=offHours）与 CHAIN_STALE（chain.stale/ageSeconds/expirationsStale）对齐后端 #18 docs/API.md
export const futuStatusTemplates:Record<string,string>={
 OPEND_OFFLINE:'OpenD 未启动或无法连接',
 OPEND_UNRESPONSIVE:'OpenD 无响应：请求（{operation}）已卡住 {pendingSeconds} 秒，未重建连接，请检查 OpenD',
 TIMEOUT:'富途请求超时（{operation}），稍后自动重试',
 FUTU_BUSY:'富途通道忙：上一请求尚未完成，本轮跳过',
 CHAIN_WARMING:'期权链加载中（已等待 {waitingSeconds} 秒），稍后出数',
 CHAIN_WARMING_OFF_HOURS:'盘外，等待开盘前预热（约 09:00 美东）',
 CHAIN_UNAVAILABLE:'期权链长时间无法获取（错误码 {code}），请检查权限或 OpenD',
 CHAIN_STALE:'使用 {ageSeconds} 秒前的期权链（后台刷新中）'};
// 是否算失败（对照计划表与 worker.py NON_FAILURE / NO_BACKOFF）
export const futuFailure:Record<string,boolean>={OPEND_OFFLINE:true,OPEND_UNRESPONSIVE:true,TIMEOUT:true,FUTU_BUSY:false,CHAIN_WARMING:false,CHAIN_WARMING_OFF_HOURS:false,CHAIN_UNAVAILABLE:true};
export const NEW_FUTU_STATUSES=['OPEND_UNRESPONSIVE','CHAIN_WARMING','CHAIN_UNAVAILABLE'];
const fillTemplate=(t:string,v:Record<string,unknown>)=>t.replace(/（[^（）]*\{(\w+)\}[^（）]*）|\{(\w+)\}/g,(m,a,b)=>{const k=a||b,x=v[k];const ok=x!=null&&x!=='';if(!ok)return a?'':'';return m.replace(`{${k}}`,()=>String(x));});
const isOffHours=(c?:ChainInfo|null)=>c?.reason==='offHours';
/** 状态码 + 可选 chain 对象 → 展示文案与是否算失败。未知码回退 message，再回退 code。 */
export function futuStatusView(row:ProviderStatusRow|null|undefined):{key:string;text:string;failure:boolean|null}|null{
 if(!row||typeof row.status!=='string'||!row.status)return null;
 const chain=row.chain&&typeof row.chain==='object'?row.chain:null;
 const key=row.status==='CHAIN_WARMING'&&isOffHours(chain)?'CHAIN_WARMING_OFF_HOURS':row.status;
 const tpl=futuStatusTemplates[key];
 if(tpl){const pend=isNum(row.pendingSeconds)?Math.round(row.pendingSeconds):undefined;
  const wait=isNum(chain?.waitingSeconds)?Math.round(chain!.waitingSeconds as number):undefined;
  const t=key==='OPEND_UNRESPONSIVE'&&pend==null?tpl.replace('已卡住 {pendingSeconds} 秒，','未完成，'):tpl;
  return {key,text:fillTemplate(t,{operation:row.operation==='unknown'?undefined:row.operation,pendingSeconds:pend,waitingSeconds:wait,code:chain?.lastError?.code}),failure:futuFailure[key]};}
 const text=statusLabels[key]||(typeof row.message==='string'&&row.message)||key;
 return {key,text,failure:null};}
/** chain 缓存标记 → 徽标文案（字段缺失返回空数组） */
export function chainBadges(c:ChainInfo|null|undefined):string[]{if(!c||typeof c!=='object')return [];const out:string[]=[];
 if(c.stale===true)out.push(isNum(c.ageSeconds)?fillTemplate(futuStatusTemplates.CHAIN_STALE,{ageSeconds:Math.max(0,Math.round(c.ageSeconds))}):'使用缓存的期权链（后台刷新中）');
 if(c.expirationsStale===true)out.push('到期日列表为前一交易日');return out;}
/** provider_status 行是否带有 v5 新字段 / 新状态（没有则页面保持现状） */
export const hasFutuV5Fields=(row:ProviderStatusRow|null|undefined)=>{if(!row||typeof row.status!=='string'||!row.status)return false;const s=row.status;
 // 已知 Futu 状态：新状态，或带了新字段；其他已有状态（OK 等）一律走原逻辑；未知状态只有带 chain 时才走新逻辑
 // 契约算失败的状态一律走新视图，保证 data-failure=true（红点红字），不依赖是否带 chain/pendingSeconds/operation
 if(futuStatusTemplates[s])return futuFailure[s]===true||NEW_FUTU_STATUSES.includes(s)||row.chain!=null||row.pendingSeconds!=null||(s==='TIMEOUT'&&!!row.operation&&row.operation!=='unknown')||(s==='FUTU_BUSY'&&(row.operation==='busy'||row.operation==='maintenance'));
 return !statusLabels[s]&&row.chain!=null;};
export const fmt=(n:any,d=3)=>typeof n==='number'&&Number.isFinite(n)?n.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
// 422 pydantic 数组：取每项 msg，去掉 "Value error, " 前缀，拼成可读文字
export const readableDetail=(items:any[])=>items.map(x=>{const msg=String(x?.msg??'').replace(/^Value error,\s*/,'');const loc=Array.isArray(x?.loc)?x.loc.filter((p:any)=>p!=='body').join('.'):'';return loc?`${loc}：${msg}`:msg;}).filter(Boolean).join('；')||'请求参数不合法';
// 与后端 models.SYMBOL_RE 一致（#12）：字母数字开头，允许 . - _，最长 20（前端已转大写）
// 先拒绝非可打印 ASCII：避免 ß、ﬀ 经 toUpperCase 变成 SS/FF 后通过（后端对原始输入会拒绝）
export const PRINTABLE_ASCII_RE=/^[\x20-\x7E]*$/;
export const SYMBOL_RE=/^[A-Z0-9][A-Z0-9._-]{0,19}$/;
export const ZONE_KEY='option-monitor.zone';
export const browserZone=()=>{try{return Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}catch{return 'UTC';}};
// 空/非法 → —；纯日期原样；无时区的时间戳按 UTC 解析；附带时区缩写（取不到则用 IANA 名）
export function formatTime(v:any,zone:string){if(v==null||v==='')return '—';if(typeof v==='string'){const s=v.trim();if(/^\d{4}-\d{2}-\d{2}$/.test(s))return s;const h=s.match(/^(.*T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)([+-]\d{2})$/);v=h?`${h[1]}${h[2]}:00`:/T\d{2}:\d{2}/.test(s)&&!/(Z|[+-]\d{2}:?\d{2})$/i.test(s)?s+'Z':s;}const d=new Date(v);if(Number.isNaN(d.getTime()))return '—';try{const text=d.toLocaleString('zh-CN',{timeZone:zone,hour12:false});const abbr=new Intl.DateTimeFormat('en-US',{timeZone:zone,timeZoneName:'short'}).formatToParts(d).find(p=>p.type==='timeZoneName')?.value;return `${text} ${abbr||zone}`;}catch{return '—';}}
export async function api(path:string,body?:unknown,method?:string){const r=await fetch('/api'+path,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const text=await r.text();let data:any;try{data=text?JSON.parse(text):null;}catch{throw new Error(`HTTP ${r.status} ${r.statusText||''}`.trim());}if(!r.ok)throw new Error(typeof data?.detail==='string'?data.detail:Array.isArray(data?.detail)?readableDetail(data.detail):data?.detail!==undefined?JSON.stringify(data.detail):`HTTP ${r.status} ${r.statusText||''}`.trim());return data;}
export const rowDefaults=()=>({strike:'152.5',call_bid:'6.52',call_ask:'6.72',call_last:'6.62',put_bid:'1.78',put_ask:'1.98',put_last:'1.88',call_reportedMid:'',put_reportedMid:''});
export const NEW_MONITOR_STRIKES=5;
// 与后端 MonitorConfig.nBelow/nAbove 一致：int, ge=0, le=50
export const STRIKES_MIN=0,STRIKES_MAX=50;
// 非负整数；空值或非数字回落到默认 5；小数向下取整；超范围夹到边界
export const normStrikes=(v:unknown):number=>{const t=String(v??'').trim();const n=t===''?NaN:Number(t);if(!Number.isFinite(n))return NEW_MONITOR_STRIKES;return Math.min(STRIKES_MAX,Math.max(STRIKES_MIN,Math.floor(n)));};
export const sideLabel=(side:unknown)=>side==='below'?'低于现价：Call价内 / Put价外':side==='at'?'现价（平值）：Call / Put 平值':'高于现价：Call价外 / Put价内';
