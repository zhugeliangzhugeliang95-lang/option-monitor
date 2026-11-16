import {fields,toDraft,fromDraft} from './editor-model.mjs';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const state={nextRefresh:null,connection:null,lastSuccess:null,source:'futu',basis:'LAST',record:null,selected:new Set(),comparison:null,busy:false,epoch:0,timer:null};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=3)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const num=v=>v.trim()===''?null:Number(v);
const when=v=>v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' 北京时间':'未提供';
const originLabels={FUTU:'FUTU / API 快照',MANUAL:'MANUAL / 人工数据',ATTACHMENT:'附件历史案例',ILLUSTRATION:'需求示意数值'};
const basisLabel=()=>state.basis==='LAST'?'最新成交价':'买卖中间价';
function message(text=''){$('#message').textContent=text;$('#message').hidden=!text;}
function errorText(data){const detail=data?.detail;if(Array.isArray(detail))return detail.map(x=>{const field=x.loc?.filter(y=>y!=='body').join('.');return (field?field+'：':'')+String(x.msg).replace(/^Value error, /,'');}).join('；');return typeof detail==='string'?detail:'请求失败，请重试。';}
async function api(path,body){const r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',signal:AbortSignal.timeout(45000),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});let data;try{data=await r.json();}catch{throw Error('服务没有返回有效结果，请确认程序仍在运行。');}if(!r.ok){const error=Error(errorText(data));error.status=r.status;throw error;}return data;}
function busy(value){state.busy=value;$$('[data-source], [data-example], #quote-rows input, #open-editor, #file').forEach(b=>b.disabled=value||(b.matches('#quote-rows input')&&!state.record?.input));$('#fetch-live').disabled=value;$('#fetch-live').textContent=value?'正在读取报价…':'获取富途报价 ↗';$('#calculate').disabled=value;$('#check-connection').disabled=value;$$('[data-ticker]').forEach(b=>b.disabled=value);$$('[data-basis]').forEach(b=>b.disabled=value||(state.record?.source==='ILLUSTRATION'&&b.dataset.basis==='MID'));$('#compare').disabled=value||state.selected.size!==3||!state.record?.input;}
function source(name,clear=true){state.epoch++;state.source=name;stopAuto(false);message();if(clear){state.record=null;state.comparison=null;state.selected.clear();render();}$$('[data-source]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.source===name)));for(const n of ['futu','manual','examples'])$('#'+n+'-controls').hidden=n!==name;}
function apply(record,preserve=false){
 const old=state.record,keep=preserve&&old?.report.symbol===record.report.symbol&&old?.report.expiration===record.report.expiration;
 const oldSelection=new Set([...state.selected].map(Number));
 state.record=record;state.comparison=null;if(record.source==='MANUAL')populateManual(record);
 if(!keep)state.basis=record.source==='ATTACHMENT'?'LAST':preferredBasis();
 if(record.source==='ILLUSTRATION')state.basis='LAST';
 const rows=record.report.modes[state.basis].rows;
 const surviving=rows.filter(r=>oldSelection.has(Number(r.strike))).map(r=>r.strike);
 const center=rows.findIndex(r=>Number(r.strike)===Number(record.report.center));
 const start=Math.max(0,Math.min(rows.length-3,(center>=0?center:Math.floor(rows.length/2))-1));
 state.selected=new Set(record.input?(keep&&surviving.length===oldSelection.size?surviving:rows.slice(start,start+3).map(r=>r.strike)):[]);
 render();
}
function readPrefs(){try{const p=JSON.parse(localStorage.getItem('strike-lab-preferences')||'{}');return p&&typeof p==='object'&&!Array.isArray(p)?p:{};}catch{return {};}}
function savePrefs(patch={}){try{localStorage.setItem('strike-lab-preferences',JSON.stringify({...readPrefs(),...patch}));}catch{}}
function preferredBasis(){return readPrefs().basis==='MID'?'MID':'LAST';}
const marketNames={PRE_MARKET_BEGIN:'美股盘前',PRE_MARKET_END:'美股盘前结束',MORNING:'美股常规交易时段',AFTERNOON:'美股常规交易时段',CLOSED:'美股已收盘',AFTER_HOURS_BEGIN:'美股盘后',AFTER_HOURS_END:'美股盘后结束',NIGHT_OPEN:'美股夜盘',NIGHT_END:'美股夜盘结束',REST:'美股休市',NONE:'美股休市/未定'};
function connectionView(info,error){state.connection=info||null;$('#connection-title').textContent=error?'行情连接未就绪':info?.quoteLoggedIn?'OpenD 行情已连接':'OpenD 尚未登录行情';$('#connection-detail').textContent=error||`正股权限 ${info.stockRight} · 期权权限 ${info.optionRight}\n${marketNames[info.marketUS]||info.marketUS} · ${when(info.checkedAt)}`;}
async function checkConnection(){if(state.busy)return;busy(true);$('#check-connection').disabled=true;try{connectionView(await api('/futu/status'));}catch(e){connectionView(null,e.message);}finally{busy(false);}}
async function updateComparison(epoch=state.epoch){
 if(!state.record?.input||state.selected.size!==3)return;
 const id=state.record.id,basis=state.basis,strikes=[...state.selected];
 const r=await api(`/studies/${id}/compare`,{basis,strikes});
 if(epoch===state.epoch&&id===state.record?.id&&basis===state.basis){state.comparison=r;renderComparison();}
}
async function loadExample(name){source('examples');const epoch=state.epoch;busy(true);try{const r=await api('/examples/'+name);if(epoch===state.epoch){apply(r);await updateComparison(epoch);}}catch(e){if(epoch===state.epoch)message(e.message);}finally{busy(false);}}
async function fetchLive(){
 if(state.busy)return;
 const symbol=$('#symbol').value.trim().toUpperCase();
 if(!symbol){message('请输入美股或 ETF 代码。');return;}
 savePrefs({symbol,rate:$('#live-rate').value});
 const epoch=++state.epoch;busy(true);message();
 try{const r=await api('/futu',{symbol,rate:Number($('#live-rate').value)/100});
  if(epoch===state.epoch){apply(r,true);if(r.connection)connectionView(r.connection);state.lastSuccess=r.receivedAt;await updateComparison(epoch);}
 }catch(e){if(epoch===state.epoch){if(e.status===503||e.status===504)connectionView(null,e.message);message((e.name==='TimeoutError'?'请求超时，请确认本机服务正常。':e.message)+(state.record?' 下方保留上一次研究快照及原时间。':'')+(state.timer?' 将在下一刷新周期重试。':''));}}
 finally{busy(false);if(state.timer)state.nextRefresh=Date.now()+60000;refreshLabel();}
}
function startAuto(immediate=false){stopAuto(false);$('#auto').checked=true;savePrefs({auto:true});state.nextRefresh=Date.now()+60000;state.timer=setInterval(()=>{refreshLabel();if(state.source==='futu'&&!document.hidden&&!state.busy&&Date.now()>=state.nextRefresh){state.nextRefresh=Date.now()+60000;fetchLive();}},1000);if(immediate)fetchLive();refreshLabel();}
function stopAuto(save=true){if(state.timer)clearInterval(state.timer);state.timer=null;state.nextRefresh=null;$('#auto').checked=false;if(save)savePrefs({auto:false});refreshLabel();}
function refreshLabel(){const ago=state.lastSuccess?`上次成功 ${when(state.lastSuccess)}。`:'';$('#refresh-state').textContent=state.timer&&document.hidden?'页面已在后台，暂停自动取数；返回后恢复。':state.timer?`${ago} 距下次刷新 ${Math.max(0,Math.ceil((state.nextRefresh-Date.now())/1000))} 秒`:`${ago} 自动刷新已暂停` ;}
function freshness(item){
 if(item.source!=='FUTU')return '';
 const rows=item.report.modes[state.basis].rows;
 const times=rows.flatMap(r=>[r.callQuote?.updatedAt,r.putQuote?.updatedAt]).filter(Boolean).map(Date.parse);
 const oldest=times.length?Math.min(...times):null;
 const stale=rows.filter(r=>r.issues.some(x=>x.includes('较旧')||x.includes('过旧'))).length;
 const phase=marketNames[item.connection?.marketUS]||item.connection?.marketUS||'市场状态未知';
 return `${phase}。${oldest!==null?`期权快照更新时间跨度：${when(new Date(oldest).toISOString())} 至 ${when(new Date(Math.max(...times)).toISOString())}。`:''}${stale?`${stale} 档含较旧快照，` :''}快照更新时间不等于独立成交/盘口时间；当前展示原始报价估值，不能视为可成交套利收益。`;
}
let manualLimits={};
function settings(){return {...manualLimits,symbol:$('#manual-symbol').value.trim().toUpperCase(),spot:num($('#manual-spot').value),expiration:$('#manual-expiry').value,valuationAt:$('#manual-asof').value.trim(),rate:Number($('#manual-rate').value)/100,timeRule:$('#manual-time-rule').value,remainingDays:$('#manual-time-rule').value==='days'?num($('#manual-days').value):null,pricingAt:$('#manual-time-rule').value==='timestamp'?$('#manual-pricing-at').value.trim():null};}
function timeFields(){const rule=$('#manual-time-rule').value;$('#manual-days-label').hidden=rule!=='days';$('#manual-pricing-label').hidden=rule!=='timestamp';}
function populateSettings(input){for(const [id,key] of [['manual-symbol','symbol'],['manual-spot','spot'],['manual-expiry','expiration'],['manual-asof','valuationAt'],['manual-days','remainingDays'],['manual-pricing-at','pricingAt']])$('#'+id).value=input[key]??'';$('#manual-rate').value=(input.rate??.05)*100;$('#manual-time-rule').value=input.timeRule||'days';manualLimits={maxAgeSeconds:input.maxAgeSeconds??60,maxSkewSeconds:input.maxSkewSeconds??5};timeFields();}
function render(){const expanded=$$('#quote-rows details[open]').map(d=>d.closest('tr').querySelector('[data-strike]').dataset.strike);const item=state.record;$('#empty').hidden=!!item;$('#study').hidden=!item;$('#export').disabled=!item;if(!item)return;
 const r=item.report,a=r.modes[state.basis],s=a.summary;
 $('#study-symbol').textContent=r.symbol;$('#source-badge').textContent=originLabels[item.source];$('#expiry-badge').textContent=r.expiration?'到期日 '+r.expiration:'期限未提供';$('#study-time').textContent='估值时刻：'+when(r.valuationAt);
 const provenance=item.source==='FUTU'?`来自本机富途 API${item.cached?'，使用 60 秒内缓存':''}。正股快照更新：${when(item.stockUpdatedAt)}。接收时刻：${when(item.receivedAt)}。${item.stockAnchor?`选档使用${item.stockAnchor.label} ${fmt(item.stockAnchor.price)}（常规 Last ${fmt(item.stockAnchor.regularLast)}）。`:''}盘口同步性请查看下方数据说明。`:item.source==='ILLUSTRATION'?'需求里的七个理论价，仅验证选档与中位数；没有原始 Call / Put，不补造行情。':item.source==='ATTACHMENT'?'附件中的 2026-10-30 GOOG 历史案例，未重新核实行情。正股现价与买卖盘口未提供；三档比较可独立计算。':'人工输入 / 导入数据。所有时刻按输入保留，报价未独立核实。';
 $('#provenance').textContent=provenance;$('#freshness').textContent=freshness(item);
 $$('[data-basis]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.basis===state.basis));b.disabled=item.source==='ILLUSTRATION'&&b.dataset.basis==='MID';});
 $('#summary').innerHTML=`<div class="summary-strip"><div class="summary-number"><span class="label">反推股价 · 中位数</span><strong>${fmt(s.median)}</strong><small>USD / 股 · ${esc(basisLabel())}</small></div><div class="summary-number"><span class="label">最小值</span><strong>${fmt(s.min)}</strong><small>所选档位中的可计算值</small></div><div class="summary-number"><span class="label">最大值</span><strong>${fmt(s.max)}</strong><small>所选档位中的可计算值</small></div></div><div class="summary-status"><span class="${s.complete?'':'incomplete'}">${r.spot==null?'未提供正股现价，七档汇总暂不可用':`${s.count} / 7 档可计算${s.complete?' · 七档完整':' · 样本不完整'}`}</span><span>正股锚点 ${fmt(r.spot)}${item.stockAnchor?' · '+esc(item.stockAnchor.label):''}</span>${s.gapBps==null?'':`<span>中位数相对正股 ${fmt(s.gapBps,2)} bp</span>`}</div>`;
 plot(a.rows,s);
 $('#selection-note').textContent=r.center?`中心行权价 ${r.center} · 前 3 ＋ 自身 ＋ 后 3`:`候选报价 ${a.rows.length} 档 · 尚未进行七档选档`;
 $('#selection-count').textContent=item.input?`已选 ${state.selected.size} / 3 档用于 Call 比较`:'示意例不含 Call / Put';
 $('#quote-rows').innerHTML=a.rows.map(row=>`<tr class="${state.selected.has(row.strike)?'selected ':''}${row.strike===r.center?'center':''}"><td><input type="checkbox" data-strike="${esc(row.strike)}" aria-label="比较行权价 ${esc(row.strike)}" ${state.selected.has(row.strike)?'checked':''} ${!item.input?'disabled':''}></td><td><strong>${esc(row.strike)}</strong>${row.strike===r.center?'<span class="center-tag">中心</span>':''}</td><td class="number">${fmt(row.call)}</td><td class="number">${fmt(row.put)}</td><td class="value">${fmt(row.implied)}</td><td class="issues"><details class="quote-detail"><summary>${row.qualityPassed?'输入事件时间通过检查':esc(row.issues[0]||'暂无质量信息')}</summary><p>${row.issues.map(esc).join('；')}<br>Call 成交事件：${esc(when(row.callQuote?.lastAt))}<br>Put 成交事件：${esc(when(row.putQuote?.lastAt))}<br>Call 快照更新：${esc(when(row.callQuote?.updatedAt))}<br>Put 快照更新：${esc(when(row.putQuote?.updatedAt))}</p></details></td></tr>`).join('');
 $('#method-content').innerHTML=`<p>模型 ${esc(r.modelVersion)} · 数据来源 ${esc(originLabels[item.source])}</p><p>年利率：${r.rate==null?'未提供':fmt(r.rate*100,2)+'%'}；T：${fmt(r.T,8)} 年。${esc(r.timeNote)}</p><p>估值时刻：${esc(when(r.valuationAt))}；定价参考时刻：${esc(when(r.pricingAt))}。</p><ul>${r.notes.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p>三档复制公式：wⱼ = (Kₖ − Kᵢ) / (Kₖ − Kⱼ)，wₖ = 1 − wⱼ；C参考 = Pᵢ + wⱼ(Cⱼ − Pⱼ) + wₖ(Cₖ − Pₖ)。溢价率 = (Call 市价 − 复制参考价) / 复制参考价。支持不等距行权价。</p><p>不同参考组会影响相对比较，排名仅针对当前三档。程序不扣交易成本，也不验证可成交性。</p>`;
 for(const d of $$('#quote-rows details'))if(expanded.includes(d.closest('tr').querySelector('[data-strike]').dataset.strike))d.open=true;
 renderComparison();busy(state.busy);quoteAge();
}
function plot(rows,s){const vals=rows.filter(r=>typeof r.implied==='number');if(!vals.length){$('#distribution').innerHTML='';return;}const values=vals.map(r=>r.implied),lo=Math.min(...values),hi=Math.max(...values),range=hi-lo||1;const x=i=>60+i*(720/Math.max(vals.length-1,1)),y=v=>92-56*(v-lo)/range;
 $('#distribution').innerHTML=`<svg viewBox="0 0 840 140" role="img" aria-label="所选行权价的反推股价分布"><line x1="35" y1="114" x2="810" y2="114" stroke="#e5eaf0"/>${s.median==null?'':`<line x1="35" y1="${y(s.median)}" x2="810" y2="${y(s.median)}" stroke="#b9c9ea" stroke-dasharray="4 5"/>`}${vals.map((v,i)=>`<line x1="${x(i)}" y1="${y(v.implied)}" x2="${x(i)}" y2="114" stroke="#dbe5f7"/><circle cx="${x(i)}" cy="${y(v.implied)}" r="4" fill="#244dc2"/><text class="dot-value" x="${x(i)}" y="${y(v.implied)-13}" text-anchor="middle">${fmt(v.implied,2)}</text><text x="${x(i)}" y="136" text-anchor="middle">K ${esc(v.strike)}</text>`).join('')}</svg>`;
}
function renderComparison(){const result=state.comparison;$('#comparison').hidden=!result;$('#compare-placeholder').hidden=!!result;if(!result){$('#comparison').innerHTML='';$('#compare-placeholder').textContent=state.record?.input?'选择三档后，查看市价相对复制参考价的溢价率。':'这个示意例没有原始 Call / Put 报价，不能用于相对价格比较。';return;}
 $('#comparison').innerHTML=`<div class="comparison-lead"><div class="rank-label">三档内相对溢价最低</div><div><strong>${result.rankable?'K '+result.lowest.map(esc).join(' / '):'暂无法排名'}</strong><p>${esc(basisLabel())} · ${result.qualityPassed?'输入事件时间通过检查':'原始价格比较，数据质量未全部通过'}</p></div></div><div class="table-wrap"><table><thead><tr><th>排名</th><th>行权价</th><th>Call 市价</th><th>复制参考价</th><th>相对溢价</th></tr></thead><tbody>${result.rows.map(r=>`<tr class="${r.rank===1?'winner':''}"><td><span class="rank">${r.rank??'—'}</span></td><td><strong>${esc(r.strike)}</strong></td><td class="number">${fmt(r.market)}</td><td class="number">${fmt(r.reference)}</td><td class="number ${r.premiumPct<0?'negative':''}">${r.premiumPct==null?'—':fmt(r.premiumPct,2)+'%'}</td></tr>`).join('')}</tbody></table></div><p class="comparison-note">${esc(result.note)} ${esc(result.issues.join('；'))}</p>${result.rows.map(r=>`<details class="derivation"><summary>K ${esc(r.strike)} 的计算明细</summary><p>${r.terms.map(t=>`${fmt(t.weight,4)} × ${t.right==='C'?'Call':'Put'}(${esc(t.strike)})`).join(' ＋ ')}<br><code>${r.terms.map(t=>`(${fmt(t.weight,4)} × ${fmt(t.price)})`).join(' + ')} = ${fmt(r.reference)}</code><br>溢价率 = (${fmt(r.market)} − ${fmt(r.reference)}) / ${fmt(r.reference)} = ${r.premiumPct==null?'不可计算':fmt(r.premiumPct,2)+'%'}</p></details>`).join('')}`;
}
async function compareSelected(){if(state.busy||!state.record?.input||state.selected.size!==3)return;const epoch=++state.epoch;busy(true);message();try{await updateComparison(epoch);}catch(e){if(epoch===state.epoch)message(e.message);}finally{busy(false);}}
function editorRow(values={},original=null){const tr=document.createElement('tr');tr.originalQuote=original;tr.innerHTML=fields.map(f=>`<td><input aria-label="${esc(f)}" data-field="${f}" type="number" min="0" step="any" value="${esc(values[f]??'')}" ${f==='strike'&&original?'readonly title="已有合约行权价；更换合约请新增一行"':''} placeholder="—"></td>`).join('')+'<td><button type="button" data-remove aria-label="删除此报价行">×</button></td>';$('#editor-rows').appendChild(tr);}
function editorDrafts(){return [...$('#editor-rows').children].map(tr=>({values:Object.fromEntries([...tr.querySelectorAll('input')].map(x=>[x.dataset.field,x.value])),original:tr.originalQuote??null}));}
function populateManual(record){populateSettings(record.input);$('#editor-rows').innerHTML='';for(const row of record.input.rows){const draft=toDraft(row,record.input);editorRow(draft.values,draft.original);}saveDraft();}
function openEditor(){if(!$('#editor-rows').children.length)for(let i=0;i<7;i++)editorRow();$('#editor-error').textContent='';$('#editor').showModal();}
async function calculate(){if(state.busy)return;const epoch=++state.epoch;busy(true);$('#editor-error').textContent='';try{const rows=editorDrafts().map(d=>fromDraft(d.values,d.original)).filter(Boolean);const r=await api('/analyze',{...settings(),rows});if(epoch===state.epoch){apply(r);$('#editor').close();message();try{await updateComparison(epoch);}catch(e){message('报价已计算，但三档比较失败：'+e.message);}}}catch(e){$('#editor-error').textContent=e.message;}finally{busy(false);}}
async function importFile(file){if(!file)return;if(file.size>1000000){message('文件不能超过 1 MB。');return;}const epoch=++state.epoch;busy(true);message();try{const r=await api('/import',{format:file.name.toLowerCase().endsWith('.csv')?'csv':'json',content:await file.text(),settings:settings()});if(epoch===state.epoch){apply(r);await updateComparison(epoch);}}catch(e){if(epoch===state.epoch)message(e.message);}finally{busy(false);$('#file').value='';}}
$$('[data-source]').forEach(b=>b.addEventListener('click',()=>{source(b.dataset.source);if(b.dataset.source==='futu'){if(readPrefs().auto!==false)startAuto();fetchLive();}}));
$$('[data-example]').forEach(b=>b.addEventListener('click',()=>loadExample(b.dataset.example)));
$$('[data-ticker]').forEach(b=>b.addEventListener('click',()=>{$('#symbol').value=b.dataset.ticker;if(!state.busy)fetchLive();}));
$$('[data-basis]').forEach(b=>b.addEventListener('click',()=>{state.epoch++;state.basis=b.dataset.basis;savePrefs({basis:state.basis});state.comparison=null;render();compareSelected();}));
$('#fetch-live').addEventListener('click',fetchLive);$('#symbol').addEventListener('keydown',e=>{if(e.key==='Enter')fetchLive();});$('#check-connection').addEventListener('click',checkConnection);$('#auto').addEventListener('change',e=>{if(e.target.checked)startAuto(true);else stopAuto();});
$('#quote-rows').addEventListener('change',e=>{const box=e.target;if(!box.matches('[data-strike]'))return;state.epoch++;message();if(box.checked&&state.selected.size>=3){box.checked=false;message('一次比较三个行权价，请先取消一档。');return;}box.checked?state.selected.add(box.dataset.strike):state.selected.delete(box.dataset.strike);state.comparison=null;render();compareSelected();});
$('#compare').addEventListener('click',compareSelected);$('#open-editor').addEventListener('click',openEditor);$('#add-row').addEventListener('click',()=>editorRow());$('#calculate').addEventListener('click',calculate);$('#editor-rows').addEventListener('click',e=>{if(e.target.closest('[data-remove]')){e.target.closest('tr').remove();saveDraft();}});$('#file').addEventListener('change',e=>importFile(e.target.files[0]));
$('#export').addEventListener('click',()=>{if(!state.record)return;const data={...state.record,comparison:state.comparison};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`strike-lab-${state.record.report.symbol}-${state.record.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('#manual-asof').value=new Date().toISOString();$('#manual-expiry').value=new Date(Date.now()+7*86400000).toISOString().slice(0,10);
$('#clear-rows').addEventListener('click',()=>{$('#editor-rows').innerHTML='';for(let i=0;i<7;i++)editorRow();saveDraft();});
function saveDraft(){try{localStorage.setItem('strike-lab-draft',JSON.stringify({version:2,settings:settings(),rows:editorDrafts()}));}catch{}}
$('#editor-rows').addEventListener('input',saveDraft);$('#manual-controls').addEventListener('input',saveDraft);
const prefs=readPrefs();$('#symbol').value=prefs.symbol||'SPY';$('#live-rate').value=prefs.rate??'5';
try{const draft=JSON.parse(localStorage.getItem('strike-lab-draft')||'null');if(draft?.settings){populateSettings(draft.settings);for(const row of (Array.isArray(draft.rows)?draft.rows:[]).slice(0,500))editorRow(draft.version===2?row.values:row,draft.version===2?row.original:null);}}catch{}
$('#manual-time-rule').addEventListener('change',()=>{timeFields();saveDraft();});timeFields();
window.addEventListener('beforeunload',()=>stopAuto(false));
(async()=>{await checkConnection();if(state.source==='futu'){if(prefs.auto!==false)startAuto();if(state.connection?.quoteLoggedIn)await fetchLive();}})();

function quoteAge(){const el=$('#quote-age');if(!el)return;const item=state.record;if(item?.source!=='FUTU'){el.textContent='';return;}const age=Math.max(0,Math.floor((Date.now()-Date.parse(item.receivedAt))/1000));el.textContent=`当前快照已接收 ${age<60?age+' 秒':Math.floor(age/60)+' 分 '+age%60+' 秒'}${age>=120?' · 已超过两个刷新周期，请刷新后再比较':''}`;el.classList.toggle('old',age>=120);}
setInterval(quoteAge,1000);
document.addEventListener('visibilitychange',()=>{refreshLabel();if(!document.hidden&&state.timer&&state.source==='futu'&&!state.busy&&Date.now()>=state.nextRefresh)fetchLive();});
