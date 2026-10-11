(()=>{
  const root=document.querySelector('[data-hc-intake]');if(!root)return;
  const api=root.dataset.api;
  const $=selector=>root.querySelector(selector);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const STATUS_LABELS={UPLOADED:'已上传 · 待处理',RUNNING:'处理中',CANDIDATE_READY:'已生成候选 · 待人工确认',FAILED:'处理失败',REVIEW:'待人工确认',PUBLISHED:'已发布'};
  const FIELD_LABELS={symptom:'问题现象',root_cause:'根因',actions:'处置措施'};
  const TREE_LABELS={CIRCUIT_FEATURE:'电路 / 特性',MATERIAL_DEVICE:'物料 / 器件'};
  const statusText=value=>STATUS_LABELS[value]||String(value||'状态未知');
  const headers={'X-Hardware-Case-Role':'MAINTAINER'};
  async function call(path,options={}){
    const response=await fetch(api+path,{...options,headers:{...headers,...options.headers}});
    const body=await response.json();
    if(!response.ok)throw new Error(String(body.detail||'REQUEST_FAILED'));
    return body;
  }
  function message(value){$('[data-intake-message]').textContent=value}
  async function prerequisites(){
    try{
      const versions=await Promise.all(['CIRCUIT_FEATURE','MATERIAL_DEVICE'].map(type=>call('/tree-imports/active-version/'+type)));
      const ready=versions.every(v=>v.active_version);
      $('[data-intake-tree-status]').textContent=ready?'两棵树均有 ACTIVE Version，可以处理案例。':'请先导入基础树，并分别 Apply 电路/特性树和物料/器件树。';
      return ready;
    }catch(error){$('[data-intake-tree-status]').textContent='无法确认树版本：'+error.message;return false}
  }
  async function list(){
    const items=(await call('/intakes')).items;
    $('[data-intake-list]').innerHTML=items.length?items.map(item=>
      '<article class="hc-case-item"><div><h3>'+escape(item.filename)+'</h3><p><code>'+escape(item.intake_id)+'</code> · <span title="原始状态：'+escape(item.status)+'">'+escape(statusText(item.status))+'</span></p>'+
      '<details class="hc-source-identity"><summary>技术来源标识</summary><code>'+escape(item.source_ref)+'</code></details>'+
      (item.error_code?'<p>错误代码：<code>'+escape(item.error_code)+'</code></p>':'')+'</div><div><button type="button" class="hc-button secondary" data-show-intake="'+escape(item.intake_id)+'">查看候选</button> '+
      (['UPLOADED','FAILED'].includes(item.status)?'<button type="button" class="hc-button primary" data-process-intake="'+escape(item.intake_id)+'">'+(item.status==='FAILED'?'失败重试':'开始处理')+'</button>':'')+'</div></article>'
    ).join(''):'<div class="hc-empty">尚未上传 Word 案例。</div>';
  }
  async function detail(id){
    const item=await call('/intakes/'+encodeURIComponent(id));
    const panel=$('[data-intake-detail]');panel.hidden=false;
    const candidate=item.candidate;
    $('[data-intake-title]').textContent=candidate?candidate.title:item.filename;
    $('[data-intake-case-id]').textContent=(item.case_id||'—')+' · '+statusText(item.status);
    $('[data-intake-facts]').innerHTML=candidate?['symptom','root_cause','actions'].map(key=>'<p><strong>'+escape(FIELD_LABELS[key])+'</strong>：'+escape(candidate.facts&&candidate.facts[key]||'待人工补充')+'</p>').join(''):'<p>尚未形成候选知识。</p>';
    const evidence=Array.isArray(candidate&&candidate.evidence)?candidate.evidence:[];
    const mappings=Array.isArray(candidate&&candidate.mappings)?candidate.mappings:[];
    $('[data-intake-evidence]').innerHTML=evidence.length?'<p>原文证据 '+evidence.length+' 条；使用“查看详情及原文证据”核对 Word 来源。</p>'+evidence.map(e=>'<p>'+escape(e.evidence_id)+' · '+escape(e.evidence_type==='WORD'?'Word 原文':e.evidence_type)+' · '+escape(e.locator&&e.locator.block_id)+' · '+escape(e.excerpt_or_caption||'')+'</p>').join(''):'<p>暂无原文证据。</p>';
    $('[data-intake-mappings]').innerHTML=mappings.length?mappings.map(m=>'<p>'+escape(TREE_LABELS[m.tree_type]||m.tree_type)+' · '+escape(m.node_path||m.node_id)+' · '+escape(m.mapping_status==='SUGGESTED'?'建议挂接 · 待确认':m.mapping_status)+'</p>').join(''):'<p>暂无建议挂接，可在案例确认中人工选择节点。</p>';
    for(const [selector,suffix] of [['[data-intake-review]','/review'],['[data-intake-case-detail]','?role=maintainer']]){
      const link=$(selector);link.hidden=!candidate;link.href=candidate?'/p0/hardware-cases/'+encodeURIComponent(item.case_id)+suffix:'#';
    }
  }
  const selected=$('[data-intake-selected-files]');
  if(selected)$('[data-intake-files]').addEventListener('change',()=>{
    const names=Array.from($('[data-intake-files]').files||[]).map(file=>file.name);
    selected.textContent=names.length===0?'未选择 Word 文件':names.length===1?names[0]:'已选择 '+names.length+' 份 Word 文件';
  });
  $('[data-intake-upload]').addEventListener('submit',async event=>{
    event.preventDefault();const files=Array.from($('[data-intake-files]').files||[]);
    try{for(const file of files){const form=new FormData();form.append('file',file);await call('/intakes',{method:'POST',body:form})}message(files.length+' 份 Word 已上传；点击开始处理。');await list()}
    catch(error){message('上传失败：'+error.message)}
  });
  $('[data-intake-refresh]').addEventListener('click',()=>list().catch(error=>message(error.message)));
  root.addEventListener('click',async event=>{
    const process=event.target.closest('[data-process-intake]');const show=event.target.closest('[data-show-intake]');
    try{
      if(process){message('AI 处理中…');const item=await call('/intakes/'+encodeURIComponent(process.dataset.processIntake)+'/process',{method:'POST'});message('处理结果：'+statusText(item.status));await list();await detail(item.intake_id)}
      else if(show)await detail(show.dataset.showIntake);
    }catch(error){message('处理失败：'+error.message)}
  });
  prerequisites();list().catch(error=>message(error.message));
})();
