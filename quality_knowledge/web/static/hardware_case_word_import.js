(()=>{
  const root=document.querySelector('[data-hc-word-import]');if(!root)return;
  const api=root.dataset.api;
  const $=selector=>root.querySelector(selector);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const locator=value=>esc(JSON.stringify(value||{}));
  const empty='<div class="hc-empty">无</div>';
  let currentSnapshot=null;
  let currentPreviewId=null;
  const cleanupConfirm='仅删除本地 Golden Preview 测试记录，不删除原始 Word、Source Fact 或正式知识。';
  function rows(items,renderer){return items&&items.length?items.map(renderer).join(''):empty}
  function render(snapshot){
    currentSnapshot=snapshot;
    const identity=snapshot.identity||{}, source=snapshot.source||{}, structure=snapshot.structure||{}, counts=snapshot.counts||{};
    $('[data-word-result]').hidden=false;$('[data-word-structure]').hidden=false;$('[data-markdown-view]').hidden=false;
    $('[data-agent-result]').hidden=true;$('[data-golden-knowledge]').hidden=true;
    $('[data-word-file-name]').textContent=source.file_name||'—';
    $('[data-business-case-id]').textContent=identity.business_case_id||'未识别（Fail-Safe）';
    $('[data-raw-title]').textContent=identity.raw_title||'—';
    $('[data-identity-status]').textContent=identity.identity_status||'—';
    $('[data-source-id]').textContent=source.source_id||identity.source_id||'—';
    $('[data-warnings]').textContent=(identity.warnings||[]).length?(identity.warnings||[]).join('；'):'无';
    $('[data-counts]').textContent='Heading '+Number(counts.headings||0)+' / Paragraph '+Number(counts.paragraphs||0)+' / Table '+Number(counts.tables||0)+' / Image '+Number(counts.images||0)+' / Blocks '+Number(counts.blocks||0);
    $('[data-headings]').innerHTML=rows(structure.headings,x=>'<article class="hc-case-item"><div><h3>'+esc(x.text||'—')+'</h3><p>Section: '+esc((x.section_path||[]).join(' > ')||'—')+'</p><p><code>Source Locator '+locator(x.source_locator)+'</code></p></div></article>');
    $('[data-paragraphs]').innerHTML=rows(structure.paragraphs,x=>'<article class="hc-case-item"><div><p>'+esc(x.text||'—')+'</p><p>Section: '+esc((x.section_path||[]).join(' > ')||'—')+'</p><p><code>Source Locator '+locator(x.source_locator)+'</code></p></div></article>');
    $('[data-tables]').innerHTML=rows(structure.tables,x=>{const table=x.table_rows||[];const cols=table.reduce((m,row)=>Math.max(m,(row||[]).length),0);return '<article class="hc-case-item"><div><h3>'+table.length+' 行 × '+cols+' 列</h3><pre>'+esc(table.map(row=>(row||[]).join(' | ')).join('\n'))+'</pre><p>Section: '+esc((x.section_path||[]).join(' > ')||'—')+'</p><p><code>Source Locator '+locator(x.source_locator)+'</code></p></div></article>'});
    $('[data-images]').innerHTML=rows(structure.images,x=>'<article class="hc-case-item"><div><h3>'+esc(x.image_ref||'IMAGE')+'</h3><p>Section: '+esc((x.section_path||[]).join(' > ')||'—')+'</p><p><code>Image Locator / Source Locator '+locator(x.source_locator)+'</code></p></div></article>');
    $('[data-raw-snapshot]').textContent=JSON.stringify(snapshot,null,2);
    const markdown=snapshot.markdown_view||{};
    $('[data-markdown-preview]').textContent=markdown.markdown||'';
    $('[data-agent-message]').textContent='Markdown 已生成。需要 Runtime/Model 配置时再点击 Run Agent Extraction。';
  }
  function renderAgent(payload){
    payload=payload||{};
    const result=payload.structured_result||payload.stage_a_result||{}, validation=payload.evidence_validation||{}, facts=result.facts||{};
    $('[data-agent-result]').hidden=false;
    $('[data-pipeline-status]').textContent=payload.pipeline_status||'—';
    $('[data-agent-status]').textContent=payload.status||'—';
    $('[data-failed-stage]').textContent=payload.failed_stage||'—';
    $('[data-error-code]').textContent=payload.error_code||'—';
    $('[data-run-id]').textContent=payload.run_id||'—';
    $('[data-task-id]').textContent=payload.task_id||'—';
    $('[data-provider-calls]').textContent=String(payload.provider_call_count??'—');
    $('[data-validation-retries]').textContent=String(payload.validation_retry_count??'—');
    $('[data-evidence-status]').textContent=validation.status||'—';
    $('[data-fabricated-count]').textContent=String(validation.fabricated_fact_count??'—');
    $('[data-fabricated-block-count]').textContent=String(validation.fabricated_block_id_count??'—');
    $('[data-agent-errors]').textContent=(validation.errors||[]).length?(validation.errors||[]).join('；'):'无';
    $('[data-agent-facts]').innerHTML=rows(Object.entries(facts),([name,item])=>'<article class="hc-case-item"><div><h3>'+esc(name)+'</h3><p>'+esc(JSON.stringify((item||{}).value??null))+'</p><p><code>Evidence '+esc(((item||{}).evidence_block_ids||[]).join(', ')||'—')+'</code></p></div></article>');
    $('[data-agent-evidence]').innerHTML=rows(validation.evidence,x=>'<article class="hc-case-item"><div><h3>'+esc(x.block_id||'—')+' · '+esc(x.block_type||'—')+'</h3><p>'+esc(x.text||x.image_ref||'—')+'</p><p><code>Source Locator '+locator(x.source_locator)+'</code></p></div></article>');
    $('[data-latency-trace]').textContent=JSON.stringify(payload.latency_trace||{},null,2);
    $('[data-raw-agent-result]').textContent=JSON.stringify(payload,null,2);
    if(payload.knowledge_object){
      renderGolden(payload.knowledge_object);
    }else{
      $('[data-golden-knowledge]').hidden=true;
    }
  }
  function candidateCard(name,item){
    item=item||{};
    return '<article class="hc-case-item"><div><h3>'+esc(name)+'</h3><p>'+esc(JSON.stringify(item.value??null))+'</p><p>Status: '+esc(item.extraction_status||'—')+' · Review: '+esc(item.review_status||'—')+'</p><p><code>Evidence '+esc((item.evidence_block_ids||[]).join(', ')||'—')+'</code></p></div></article>';
  }
  function renderGolden(ko){
    $('[data-golden-knowledge]').hidden=false;
    const review=ko.review||{}, prov=ko.provenance||{}, ctx=ko.engineering_context||{}, conflicts=ko.conflicts||[], reusable=ko.reusable_knowledge||{};
    $('[data-golden-status]').textContent=review.object_status||'—';
    $('[data-extraction-contract]').textContent=prov.extraction_contract_version||'—';
    $('[data-runtime-run]').textContent=prov.runtime_run_id||'—';
    $('[data-golden-context]').innerHTML=rows(Object.entries(ctx).filter(([k])=>k!=='key_parameters'),([name,item])=>candidateCard(name,item))+
      rows(ctx.key_parameters||[],item=>candidateCard('key_parameter: '+(item.name||'—'),item));
    $('[data-golden-conflicts]').innerHTML=rows(conflicts,item=>'<article class="hc-case-item"><div><h3>'+esc(item.type||'—')+'</h3><p>'+esc(JSON.stringify(item.source_values||[]))+'</p><p>Status: '+esc(item.status||'—')+' / '+esc(item.resolution_status||'—')+'</p><p><code>Evidence '+esc((item.evidence_block_ids||[]).join(', ')||'—')+'</code></p></div></article>');
    const layers=[['Observed Problem',ko.observed_problem||{}],['Engineering Analysis',ko.engineering_analysis||{}],['Engineering Resolution',ko.engineering_resolution||{}]];
    $('[data-golden-case-layers]').innerHTML=layers.map(([title,obj])=>'<h4>'+esc(title)+'</h4>'+rows(Object.entries(obj),([name,item])=>candidateCard(name,item))).join('');
    $('[data-golden-reusable]').innerHTML=rows(Object.entries(reusable),([name,item])=>candidateCard(name,item).replace('</div></article>','<p>Derived: '+esc((item.derived_from_fields||[]).join(', ')||'—')+'</p></div></article>'));
    $('[data-golden-evidence]').innerHTML=rows(ko.evidence||[],x=>'<article class="hc-case-item"><div><h3>'+esc(x.block_id||'—')+' · '+esc(x.block_type||'—')+'</h3><p>'+esc(x.text||x.image_ref||'—')+'</p><p><code>Source Locator '+locator(x.source_locator)+'</code></p></div></article>');
    $('[data-raw-golden]').textContent=JSON.stringify(ko,null,2);
  }
  async function loadPreview(previewId){
    $('[data-preview-message]').textContent='加载 Preview #'+previewId+'…';
    try{
      const response=await fetch(api+'/r1/previews/'+encodeURIComponent(previewId),{headers:{'X-Hardware-Case-Role':'MAINTAINER'}});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok)throw new Error(String(body.detail||'R1_PREVIEW_LOAD_FAILED'));
      currentPreviewId=String(body.preview_id??previewId);
      currentSnapshot=body.snapshot||null;
      if(currentSnapshot)render(currentSnapshot);
      renderAgent(body.result||{});
      $('[data-preview-message]').textContent='已恢复 Preview #'+previewId+' · '+(body.raw_title||'—');
    }catch(error){
      $('[data-preview-message]').textContent='Preview 加载失败：'+error.message;
    }
  }
  async function deletePreview(previewId){
    if(!window.confirm(cleanupConfirm))return;
    $('[data-preview-message]').textContent='正在删除 Preview #'+previewId+'…';
    try{
      const response=await fetch(api+'/r1/previews/'+encodeURIComponent(previewId),{method:'DELETE',headers:{'X-Hardware-Case-Role':'MAINTAINER'}});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok)throw new Error(String(body.detail||'R1_PREVIEW_DELETE_FAILED'));
      if(currentPreviewId===String(previewId)){
        currentPreviewId=null;
        $('[data-agent-result]').hidden=true;
        $('[data-golden-knowledge]').hidden=true;
      }
      await refreshPreviewHistory();
      $('[data-preview-message]').textContent='已删除 Preview #'+previewId+'；仅清理本地 Golden Preview 测试记录。';
    }catch(error){
      $('[data-preview-message]').textContent='Preview 删除失败：'+error.message;
    }
  }
  async function clearPreviewHistory(){
    if(!window.confirm(cleanupConfirm))return;
    $('[data-preview-message]').textContent='正在清空 Golden Preview 历史…';
    try{
      const response=await fetch(api+'/r1/previews',{method:'DELETE',headers:{'X-Hardware-Case-Role':'MAINTAINER'}});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok)throw new Error(String(body.detail||'R1_PREVIEW_CLEAR_FAILED'));
      currentPreviewId=null;
      $('[data-agent-result]').hidden=true;
      $('[data-golden-knowledge]').hidden=true;
      await refreshPreviewHistory();
      $('[data-preview-message]').textContent='已清空 '+Number(body.deleted_count||0)+' 条 Preview；原始 Word、Source Fact、DocumentSnapshot、正式知识与 Runtime 审计未删除。';
    }catch(error){
      $('[data-preview-message]').textContent='Preview 清空失败：'+error.message;
    }
  }
  async function refreshPreviewHistory({autoRestore=false}={}){
    try{
      const response=await fetch(api+'/r1/previews?limit=10',{headers:{'X-Hardware-Case-Role':'MAINTAINER'}});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok)throw new Error(String(body.detail||'R1_PREVIEW_LIST_FAILED'));
      const items=body.items||[];
      $('[data-preview-history]').innerHTML=rows(items,item=>'<article class="hc-case-item"><div><h3>'+esc(item.business_case_id||'未识别')+' · '+esc(item.raw_title||'—')+'</h3><p>Preview #'+esc(item.preview_id)+' · Run '+esc(item.runtime_run_id||'—')+' · '+esc(item.created_at||'—')+'</p><p><code>'+esc(item.source_id||'—')+'</code></p><button class="hc-button" type="button" data-preview-id="'+esc(item.preview_id)+'">打开结果</button> <button class="hc-button" type="button" data-delete-preview-id="'+esc(item.preview_id)+'">删除</button></div></article>');
      $('[data-preview-message]').textContent=items.length?'已有 '+items.length+' 条最近 Preview；刷新页面不会丢失。':'暂无已保存 Preview。';
      root.querySelectorAll('[data-preview-id]').forEach(button=>button.addEventListener('click',()=>loadPreview(button.dataset.previewId)));
      root.querySelectorAll('[data-delete-preview-id]').forEach(button=>button.addEventListener('click',()=>deletePreview(button.dataset.deletePreviewId)));
      if(autoRestore&&items.length)await loadPreview(items[0].preview_id);
    }catch(error){
      $('[data-preview-history]').innerHTML=empty;
      $('[data-preview-message]').textContent='Preview 历史不可用：'+error.message;
    }
  }
  $('[data-word-upload]').addEventListener('submit',async event=>{
    event.preventDefault();
    const file=$('[data-word-file]').files&&$('[data-word-file]').files[0];
    if(!file)return;
    currentSnapshot=null;$('[data-word-message]').textContent='解析中…';
    const form=new FormData();form.append('file',file);
    try{
      const response=await fetch(api+'/r1/word-snapshot',{method:'POST',headers:{'X-Hardware-Case-Role':'MAINTAINER'},body:form});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok)throw new Error(String(body.detail||'WORD_PARSE_FAILED'));
      render(body);$('[data-word-message]').textContent='PASS：Upload → Parse → Markdown View 已完成。';
    }catch(error){
      $('[data-word-result]').hidden=true;$('[data-word-structure]').hidden=true;$('[data-markdown-view]').hidden=true;$('[data-agent-result]').hidden=true;$('[data-golden-knowledge]').hidden=true;
      $('[data-word-message]').textContent='解析失败：'+error.message;
    }
  });
  async function runPipeline(forceRetry=false){
    if(!currentSnapshot)return;
    $('[data-agent-message]').textContent=forceRetry?'Force Retry：创建新的可追踪 Runtime Run…':'V1.3 Pipeline 执行中…';
    try{
      const suffix=forceRetry?'?force_retry=true':'';
      const response=await fetch(api+'/r1/agent-extract'+suffix,{method:'POST',headers:{'Content-Type':'application/json','X-Hardware-Case-Role':'MAINTAINER'},body:JSON.stringify(currentSnapshot)});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok){
        const detail=body&&typeof body.detail==='object'?body.detail:null;
        if(detail)renderAgent(detail);
        throw new Error(String((detail&&detail.error_code)||body.detail||'RUNTIME_EXECUTION_FAILED'));
      }
      renderAgent(body);
      if(body.pipeline_status==='GOLDEN_PREVIEW_READY'){
        $('[data-agent-message]').textContent=body.status==='NEEDS_REVIEW'?'GOLDEN_PREVIEW_READY：存在本地 Conflict，需要人工 Review。':'PASS：V1.3 Stage A + Stage B + Local Validation 已完成。';
      }else if(body.pipeline_status==='PARTIAL_REUSABLE_KNOWLEDGE_FAILED'){
        $('[data-agent-message]').textContent='PARTIAL：Stage A 已保留；Stage B 失败，错误='+String(body.error_code||'RUNTIME_EXECUTION_FAILED');
      }else{
        $('[data-agent-message]').textContent='FAILED：'+String(body.failed_stage||'—')+' / '+String(body.error_code||'RUNTIME_EXECUTION_FAILED');
      }
      await refreshPreviewHistory();
    }catch(error){
      $('[data-agent-message]').textContent='Pipeline 执行失败：'+error.message;
    }
  }
  $('[data-run-agent]').addEventListener('click',()=>runPipeline(false));
  $('[data-force-retry]').addEventListener('click',()=>runPipeline(true));
  $('[data-refresh-previews]').addEventListener('click',()=>refreshPreviewHistory());
  $('[data-clear-previews]').addEventListener('click',()=>clearPreviewHistory());
  refreshPreviewHistory({autoRestore:true});
})();
