(function(){
'use strict';
const root=document.querySelector('[data-case-detail]');if(!root)return;
const api=(window.P0_CASES_API||root.dataset.apiPrefix||'/api/v2').replace(/\/$/,'');
const caseId=window.P0_CASE_ID||root.dataset.caseId;
const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const empty='未确认 / 无已确认内容';
function section(no,title,value,note){
  return '<section class="case-card"><div class="case-card-head"><div><span class="case-section-no">'+no+'</span><h2>'+esc(title)+'</h2></div></div>'+
    '<div class="case-section-body"><p>'+esc(value||empty)+'</p>'+(note?'<small>'+esc(note)+'</small>':'')+'</div></section>';
}
function evidence(item,index){
  const source=[item.source_type,item.source_id].filter(Boolean).join(' / ')||'未提供';
  const location=[item.file_name,item.page!=null?'Page '+item.page:'',item.section?'Section '+item.section:''].filter(Boolean).join(' · ')||'未提供';
  return '<article class="case-evidence"><div class="case-evidence-head"><strong>Evidence '+(index+1)+'</strong><span>'+esc(source)+'</span></div>'+
    '<dl><dt>Evidence ID</dt><dd>'+esc(item.evidence_id||item.id||'未提供')+'</dd><dt>文档 / 位置</dt><dd>'+esc(location)+'</dd>'+
    '<dt>支撑字段 / 结论</dt><dd>'+esc(item.target_path||item.supports||item.field_path||empty)+'</dd></dl>'+
    '<blockquote>'+esc(item.raw_text||item.excerpt||'当前知识存在，但没有可用原始 Evidence。')+'</blockquote>'+
    (item.url?'<a href="'+esc(item.url)+'" target="_blank" rel="noopener">查看来源</a>':'')+'</article>';
}
async function load(){
  const response=await fetch(api+'/historical-cases/'+encodeURIComponent(caseId),{headers:{Accept:'application/json'}});
  if(!response.ok){root.querySelector('[data-case-title]').textContent='案例读取失败';return}
  const d=await response.json();
  root.querySelector('[data-case-title]').textContent=d.title||d.case_id||caseId;
  root.querySelector('[data-case-badges]').innerHTML=[d.publication_status||d.status,d.itr,d.product].filter(Boolean).map(x=>'<span class="case-badge">'+esc(x)+'</span>').join('');
  const summary=[['Case ID',d.case_id],['ITR',d.itr],['产品',d.product],['状态',d.publication_status||d.status],['Case Version',d.case_version]];
  root.querySelector('[data-case-summary]').innerHTML=summary.map(([k,v])=>'<div class="case-summary-item"><label>'+esc(k)+'</label><strong>'+esc(v||empty)+'</strong></div>').join('');
  root.querySelector('[data-case-sections]').innerHTML=
    section('01','问题事实',d.problem_description)+
    section('02','问题现象',d.symptom||d.problem_description)+
    section('03','技术原因',d.root_cause,'当前 historical-case/v1 未区分技术/管理原因时，仅展示已确认原因，不自行分类。')+
    section('04','管理原因',null)+
    section('05','技术措施',d.solution,'当前 historical-case/v1 未区分技术/管理措施时，仅展示已确认措施，不自行分类。')+
    section('06','管理措施',null)+
    section('07','验证结果',d.verification_result);
  const ev=Array.isArray(d.evidence)?d.evidence:[];
  root.querySelector('[data-case-evidence-count]').textContent=ev.length+' 条';
  root.querySelector('[data-case-evidence]').innerHTML=ev.length?ev.map(evidence).join(''):'<div class="case-empty">当前知识存在，但没有可用原始 Evidence。</div>';
  const source=[['来源 ITR',d.itr],['发布时间',d.published_at],['Case Version',d.case_version],['Publication Status',d.publication_status],['Contract',d.contract_version],['Case ID',d.case_id]];
  root.querySelector('[data-case-source]').innerHTML=source.map(([k,v])=>'<div><label>'+esc(k)+'</label><p>'+esc(v||empty)+'</p></div>').join('');
}
load();
})();