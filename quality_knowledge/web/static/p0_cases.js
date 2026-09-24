(function(){
'use strict';
const root=document.querySelector('[data-case-list]');if(!root)return;
const api=(window.P0_CASES_API||root.dataset.apiPrefix||'/api/v2').replace(/\/$/,'');
const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){
  const q=root.querySelector('[data-case-q]').value.trim();
  const product=root.querySelector('[data-case-product]').value.trim();
  const status=root.querySelector('[data-case-status]').value||'PUBLISHED';
  const params=new URLSearchParams({q,product,status});
  const response=await fetch(api+'/historical-cases?'+params.toString(),{headers:{Accept:'application/json'}});
  const body=root.querySelector('[data-case-body]');
  if(!response.ok){body.innerHTML='<tr><td colspan="8" class="case-empty">案例库读取失败</td></tr>';return}
  const data=await response.json();const items=Array.isArray(data.items)?data.items:[];
  root.querySelector('[data-case-count]').textContent=items.length+' 条';
  body.innerHTML=items.length?items.map(item=>'<tr>'+
    '<td>'+esc(item.itr||'未确认 / 无已确认内容')+'</td>'+
    '<td><a href="/p0/cases/'+encodeURIComponent(item.case_id)+'">'+esc(item.title||item.case_id)+'</a></td>'+
    '<td>'+esc(item.product||'未确认 / 无已确认内容')+'</td>'+
    '<td>'+esc(item.version||'未确认 / 无已确认内容')+'</td>'+
    '<td><span class="case-status">'+esc(item.status||'PUBLISHED')+'</span></td>'+
    '<td>'+esc(item.published_at||'未确认 / 无已确认内容')+'</td>'+
    '<td>'+esc(item.case_version||'未确认 / 无已确认内容')+'</td>'+
    '<td><a href="/p0/cases/'+encodeURIComponent(item.case_id)+'">查看</a></td></tr>').join(''):
    '<tr><td colspan="8" class="case-empty">当前没有符合条件的已发布案例。</td></tr>';
}
root.querySelector('[data-case-search]').addEventListener('click',load);
root.querySelector('[data-case-q]').addEventListener('keydown',e=>{if(e.key==='Enter')load()});
load();
})();