export type Obj=Record<string,any>;
// 统计接口：reason / lastGapAt 为后端可选字段，前端不依赖其存在
export type Statistics=Obj&{reason?:string|null;lastGapAt?:string|null};
export const focusSymbols=['TSLA','NVDA','META','AMZN','GOOGL','AAPL','MSFT'];
export const focusRank=(m:Obj)=>{const rank=focusSymbols.indexOf(m.config.symbol);return rank<0?focusSymbols.length:rank;};
export const bases=['BID','ASK','MID','LAST','CUSTOM'];
export const labels:Obj={BID:'买一',ASK:'卖一',MID:'中间价',LAST:'最新成交',CUSTOM:'自定义'};
export const statusLabels:Obj={FUTU_RIGHTS_READY:'富途权限就绪 · 等待报价核验',FUTU_OPTION_PERMISSION_REQUIRED:'缺少 OpenAPI 美股期权权限',FUTU_STOCK_PERMISSION_REQUIRED:'缺少 OpenAPI 美股正股权限',OPEND_OFFLINE:'OpenD 未启动',OPEND_LOGIN_REQUIRED:'请登录 OpenD 并完成协议',FUTU_SDK_MISSING:'富途 SDK 未安装',FUTU_BUSY:'富途请求处理中',FUTU_REQUEST_FAILED:'富途请求失败',CONTRACT_METADATA_MISMATCH:'合约资料不匹配',UNSUPPORTED_SYMBOL:'不在已配置的美股/ETF标的中',EXPIRED:'合约已过期',MANUAL_PRICE_ONLY:'人工快照 · 价格计算',CLOSED_OR_UNKNOWN_SESSION:'休市 / 交易时段未确认',PAUSED:'采集已暂停',PLEASE_SET_COUNTS:'请设置选档数量',NO_SAME_DAY_EXPIRY:'今天没有可用到期期权',PARTIAL_OR_INELIGIBLE:'部分档位 / 质量未通过',INVALID_SELECTION_ANCHOR:'选档成交价过旧或缺时间',MISSING_ANCHOR:'缺少选档成交价',NOT_CONFIGURED:'未配置 API',WAITING_WORKER:'等待采集',MANUAL_INPUT_REQUIRED:'等待人工输入',RATE_LIMITED:'供应商限流',TIMEOUT:'请求超时',PERMISSION_DENIED:'行情权限不足',CONNECTION_OR_PROTOCOL_ERROR:'行情连接中断',INSUFFICIENT_SAMPLES:'样本积累中',ZERO_SE:'方差为零 / SE 过小',OK:'计算完成'};
export const fmt=(n:any,d=3)=>typeof n==='number'&&Number.isFinite(n)?n.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
// 422 pydantic 数组：取每项 msg，去掉 "Value error, " 前缀，拼成可读文字
export const readableDetail=(items:any[])=>items.map(x=>{const msg=String(x?.msg??'').replace(/^Value error,\s*/,'');const loc=Array.isArray(x?.loc)?x.loc.filter((p:any)=>p!=='body').join('.'):'';return loc?`${loc}：${msg}`:msg;}).filter(Boolean).join('；')||'请求参数不合法';
export const SYMBOL_RE=/^[A-Z0-9.\-]+$/;
export const ZONE_KEY='option-monitor.zone';
export const browserZone=()=>{try{return Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}catch{return 'UTC';}};
// 空/非法 → —；纯日期原样；无时区的时间戳按 UTC 解析；附带时区缩写（取不到则用 IANA 名）
export function formatTime(v:any,zone:string){if(v==null||v==='')return '—';if(typeof v==='string'){const s=v.trim();if(/^\d{4}-\d{2}-\d{2}$/.test(s))return s;v=/T\d{2}:\d{2}/.test(s)&&!/(Z|[+-]\d{2}:?\d{2})$/i.test(s)?s+'Z':s;}const d=new Date(v);if(Number.isNaN(d.getTime()))return '—';try{const text=d.toLocaleString('zh-CN',{timeZone:zone,hour12:false});const abbr=new Intl.DateTimeFormat('en-US',{timeZone:zone,timeZoneName:'short'}).formatToParts(d).find(p=>p.type==='timeZoneName')?.value;return `${text} ${abbr||zone}`;}catch{return '—';}}
export async function api(path:string,body?:unknown,method?:string){const r=await fetch('/api'+path,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const text=await r.text();let data:any;try{data=text?JSON.parse(text):null;}catch{throw new Error(`HTTP ${r.status} ${r.statusText||''}`.trim());}if(!r.ok)throw new Error(typeof data?.detail==='string'?data.detail:Array.isArray(data?.detail)?readableDetail(data.detail):data?.detail!==undefined?JSON.stringify(data.detail):`HTTP ${r.status} ${r.statusText||''}`.trim());return data;}
export const rowDefaults=()=>({strike:'152.5',call_bid:'6.52',call_ask:'6.72',call_last:'6.62',put_bid:'1.78',put_ask:'1.98',put_last:'1.88',call_reportedMid:'',put_reportedMid:''});
export const NEW_MONITOR_STRIKES=5;
// 与后端 MonitorConfig.nBelow/nAbove 一致：int, ge=0, le=50
export const STRIKES_MIN=0,STRIKES_MAX=50;
// 非负整数；空值或非数字回落到默认 5；小数向下取整；超范围夹到边界
export const normStrikes=(v:unknown):number=>{const t=String(v??'').trim();const n=t===''?NaN:Number(t);if(!Number.isFinite(n))return NEW_MONITOR_STRIKES;return Math.min(STRIKES_MAX,Math.max(STRIKES_MIN,Math.floor(n)));};
export const sideLabel=(side:unknown)=>side==='below'?'低于现价：Call价内 / Put价外':side==='at'?'现价（平值）：Call / Put 平值':'高于现价：Call价外 / Put价内';
