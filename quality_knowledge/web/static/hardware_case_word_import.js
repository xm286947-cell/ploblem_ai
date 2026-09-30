(()=>{
  const root=document.querySelector('[data-hc-word-import]');if(!root)return;
  const api=root.dataset.api;
  const $=selector=>root.querySelector(selector);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const locator=value=>esc(JSON.stringify(value||{}));
  const empty='<div class="hc-empty">无</div>';
  function rows(items, renderer){return items&&items.length?items.map(renderer).join(''):empty}
  function render(snapshot){
    const identity=snapshot.identity||{}, source=snapshot.source||{}, structure=snapshot.structure||{}, counts=snapshot.counts||{};
    $('[data-word-result]').hidden=false;$('[data-word-structure]').hidden=false;
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
  }
  $('[data-word-upload]').addEventListener('submit',async event=>{
    event.preventDefault();
    const file=$('[data-word-file]').files&&$('[data-word-file]').files[0];
    if(!file)return;
    $('[data-word-message]').textContent='解析中…';
    const form=new FormData();form.append('file',file);
    try{
      const response=await fetch(api+'/r1/word-snapshot',{method:'POST',headers:{'X-Hardware-Case-Role':'MAINTAINER'},body:form});
      let body={};try{body=await response.json()}catch(_){}
      if(!response.ok)throw new Error(String(body.detail||'WORD_PARSE_FAILED'));
      render(body);$('[data-word-message]').textContent='PASS：已完成 Upload → Parse → Render。可选择下一份 Word。';
    }catch(error){
      $('[data-word-result]').hidden=true;$('[data-word-structure]').hidden=true;
      $('[data-word-message]').textContent='解析失败：'+error.message;
    }
  });
})();
