(function(){
'use strict';
const root=document.querySelector('[data-qsl-page]');if(!root)return;
const api=(window.QS_LIBRARY_API||root.dataset.apiPrefix||'/api/v2').replace(/\/$/,'');
const state={items:[],facetsLoaded:false};
const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const form=root.querySelector('[data-filter-form]');
const rows=root.querySelector('[data-rows]');
const count=root.querySelector('[data-result-count]');
const loadStatus=root.querySelector('[data-load-status]');
const summary=root.querySelector('[data-filter-summary]');
const empty=root.querySelector('[data-empty-state]');
const noResult=root.querySelector('[data-no-result-state]');
const errorState=root.querySelector('[data-error-state]');
async function request(path){const r=await fetch(api+path);const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.detail||('HTTP_'+r.status));return data}
function query(){const p=new URLSearchParams();for(const [k,v] of new FormData(form).entries()){if(String(v).trim())p.set(k,String(v).trim())}return p}
function setState(kind,message=''){empty.hidden=kind!=='empty';noResult.hidden=kind!=='no-result';errorState.hidden=kind!=='error';root.querySelector('[data-error-message]').textContent=message||'请稍后重试。'}
function fmtTime(value){if(!value)return '—';const d=new Date(value);if(Number.isNaN(d.getTime()))return value;return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)}
function triggerLabel(v){return ({HIGH_PERCEPTION:'高感知',RND_VALUE:'研发价值'})[v]||''}
function render(){
  count.textContent=state.items.length;setState('');
  if(!state.items.length){rows.innerHTML='';const params=query();const hasNarrow=[...params.entries()].some(([k,v])=>k!=='status'&&v)||params.get('status')!=='PUBLISHED';setState(hasNarrow?'no-result':'empty');return}
  rows.innerHTML=state.items.map(x=>{
    const updated=(x.version&&x.version.updated_at)||'';
    const sources=(x.source_problem_refs||[]).length;
    const trigger=triggerLabel(x.trigger_source);
    return '<tr class="qsl-row" tabindex="0" data-detail="'+esc(x.scenario_id)+'">'+
      '<td class="qsl-name"><strong>'+esc(x.scenario_name||'未命名场景')+'</strong><small>'+esc(x.scenario_id)+' · V'+esc(x.scenario_version)+(trigger?' <span class="qsl-trigger">'+esc(trigger)+'</span>':'')+'</small></td>'+
      '<td>'+esc(x.product_name||x.product_code||'—')+'<small class="qsl-cell-sub">'+esc(x.product_code||'')+'</small></td>'+
      '<td>'+esc(x.lifecycle_stage_name||x.lifecycle_stage_code||'—')+'</td>'+
      '<td>'+esc(x.business_activity_name||x.business_activity_code||'—')+'</td>'+
      '<td>'+esc(x.quality_concern_name||x.quality_concern_code||'—')+'</td>'+
      '<td class="qsl-num">'+sources+'</td>'+
      '<td><span class="qsl-status '+esc(x.status)+'">'+esc(x.status)+'</span></td>'+
      '<td class="qsl-updated">'+esc(fmtTime(updated))+'</td></tr>'
  }).join('');
}
function optionHtml(map,placeholder){return '<option value="">'+esc(placeholder)+'</option>'+[...map].sort((a,b)=>String(a[1]).localeCompare(String(b[1]),'zh-CN')).map(([v,n])=>'<option value="'+esc(v)+'">'+esc(n||v)+'</option>').join('')}
async function loadFacets(){
  if(state.facetsLoaded)return;
  const x=await request('/quality-scenarios');
  const products=new Map(),lifecycles=new Map(),activities=new Map(),qualities=new Map();
  (x.items||[]).forEach(item=>{
    if(item.product_code)products.set(item.product_code,item.product_name||item.product_code);
    if(item.lifecycle_stage_code)lifecycles.set(item.lifecycle_stage_code,item.lifecycle_stage_name||item.lifecycle_stage_code);
    if(item.business_activity_code)activities.set(item.business_activity_code,item.business_activity_name||item.business_activity_code);
    if(item.quality_concern_code)qualities.set(item.quality_concern_code,item.quality_concern_name||item.quality_concern_code);
    else if(item.quality_concern_name)qualities.set(item.quality_concern_name,item.quality_concern_name);
  });
  root.querySelector('[data-product-filter]').innerHTML=optionHtml(products,'全部产品');
  root.querySelector('[data-lifecycle-filter]').innerHTML=optionHtml(lifecycles,'全部生命周期');
  root.querySelector('[data-activity-filter]').innerHTML=optionHtml(activities,'全部业务活动');
  root.querySelector('[data-quality-filter]').innerHTML=optionHtml(qualities,'全部质量关注');
  state.facetsLoaded=true;
}
function filterSummary(){const p=query();const parts=[];const statusValue=p.get('status');parts.push(statusValue?'状态 '+statusValue:'全部状态');for(const [k,label] of [['product_code','产品'],['lifecycle_stage_code','生命周期'],['business_activity_code','业务活动'],['quality_concern_code','质量关注'],['q','关键词']]){if(p.get(k))parts.push(label+' '+p.get(k))}summary.textContent='当前范围：'+parts.join(' · ')}
async function load(){
  loadStatus.className='';loadStatus.textContent='正在读取…';rows.innerHTML='<tr><td colspan="8" class="qsl-loading">正在读取质量场景…</td></tr>';setState('');filterSummary();
  try{const x=await request('/quality-scenarios?'+query().toString());state.items=x.items||[];render();loadStatus.textContent='';}
  catch(e){state.items=[];count.textContent='—';rows.innerHTML='';setState('error',e.message);loadStatus.className='error';loadStatus.textContent='读取失败';}
}
function openDetail(id){window.location.href='/p0/quality-scenarios/'+encodeURIComponent(id)}
form.addEventListener('submit',e=>{e.preventDefault();load()});
root.querySelector('[data-reset]').addEventListener('click',()=>{form.reset();form.elements.status.value='PUBLISHED';load()});
root.querySelector('[data-retry]').addEventListener('click',load);
rows.addEventListener('click',e=>{const tr=e.target.closest('[data-detail]');if(tr)openDetail(tr.dataset.detail)});
rows.addEventListener('keydown',e=>{const tr=e.target.closest('[data-detail]');if(tr&&(e.key==='Enter'||e.key===' ')){e.preventDefault();openDetail(tr.dataset.detail)}});
Promise.all([loadFacets(),load()]).catch(e=>{loadStatus.className='error';loadStatus.textContent='初始化失败：'+e.message});
})();