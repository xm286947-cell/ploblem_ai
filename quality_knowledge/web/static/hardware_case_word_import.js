(()=>{
  const root=document.querySelector('[data-hc-word-import]');if(!root)return;
  const api=root.dataset.api;
  const $=selector=>root.querySelector(selector);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const locator=value=>esc(JSON.stringify(value||{}));
  const empty='<div class="hc-empty">无</div>';
  let currentSnapshot=null;
  function rows(items,renderer){return items&&items.length?items.map(renderer).join(''):empty}
  function render(snapshot){
    currentSnapshot=snapshot;
    const identity=snapshot.identity||{}, source=snapshot.source||{}, structure=snapshot.structure||{}, counts=snapshot.counts||{};
    $('[data-word-result]').hidden=false;$('[data-word-structure]').hidden=false;$('[data-markdown-view]').hidden=false;
    $('[data-agent-result]').hidden=true;
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
    const result=payload.structured_result||{}, validation=payload.evidence_validation||{}, facts=result.facts||{};
    $('[data-agent-result]').hidden=false;
    $('[data-agent-status]').textContent=payload.status||'—';
    $('[data-evidence-status]').textContent=validation.status||'—';
    $('[data-fabricated-count]').textContent=String(validation.fabricated_fact_count??'—');
    $('[data-agent-errors]').textContent=(validation.errors||[]).length?(validation.errors||[]).join('；'):'无';
    $('[data-agent-facts]').innerHTML=rows(Object.entries(facts),([name,item])=>'<article class="hc-case-item"><div><h3>'+esc(name)+'</h3><p>'+esc(JSON.stringify((item||{}).value??null))+'</p><p><code>Evidence '+esc(((item||{}).evidence_block_ids||[]).join(', ')||'—')+'</code></p></div></article>');
    $('[data-agent-evidence]').innerHTML=rows(validation.evidence,x=>'<article class="hc-case-item"><div><h3>'+esc(x.block_id||'—')+' · '+esc(x.block_type||'—')+'</h3><p>'+esc(x.text||x.image_ref||'—')+'</p><p><code>Source Locator '+locator(x.source_locator)+'</code></p></div></article>');
    $('[data-raw-agent-result]').textContent=JSON.stringify(payload,null,2);
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
      $('[data-word-result]').hidden=true;$('[data-word-structure]').hidden=true;$('[data-markdown-view]').hidden=true;$('[data-agent-result]').hidden=true;
      $('[data-word-message]').textContent='解析失败：'+error.message;
    }
  });
  $('[data-run-agent]').addEventListener('click',async()=>{
    if(!currentSnapshot)return;
    $('[data-agent-message]').textContent='Unified Runtime 执行中…';
    try{
      const response=await fetch(api+'/r1/agent-extract',{method:'POST',headers:{'Content-Type':'application/json','X-Hardware-Case-Role':'MAINTAINER'},body:JSON.stringify(currentSnapshot)});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok)throw new Error(String(body.detail||'RUNTIME_EXECUTION_FAILED'));
      renderAgent(body);
      $('[data-agent-message]').textContent=body.status==='PASS'?'PASS：Agent Extraction + Evidence Gate 已通过。':'NEEDS_REVIEW：请检查 Evidence Gate。';
    }catch(error){
      $('[data-agent-result]').hidden=true;
      $('[data-agent-message]').textContent='Agent 未执行：'+error.message+'。请检查本地 Runtime / Model 配置。';
    }
  });
})();
