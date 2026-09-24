(()=>{
  const root=document.querySelector('[data-hc-page]');
  if(!root)return;
  const api=(root.dataset.api||'/api/v2/hardware-cases').replace(/\/$/,'');
  const role=(root.dataset.role||'CONSUMER').toUpperCase();
  const page=root.dataset.hcPage;
  const qs=(s,p=root)=>p.querySelector(s);
  const qsa=(s,p=root)=>Array.from(p.querySelectorAll(s));
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text=v=>v===null||v===undefined||v===''?'—':String(v);
  const roleHeaders=r=>r==='MAINTAINER'?{'X-Hardware-Case-Role':'MAINTAINER'}:{};
  const toast=message=>{
    const el=qs('[data-hc-toast]'); if(!el)return;
    el.textContent=message;el.hidden=false;clearTimeout(el._timer);
    el._timer=setTimeout(()=>{el.hidden=true},2600);
  };
  async function request(path,options={},authRole=role){
    const headers={...roleHeaders(authRole),...(options.headers||{})};
    const response=await fetch(api+path,{...options,headers});
    const type=response.headers.get('content-type')||'';
    const data=type.includes('application/json')?await response.json():await response.text();
    if(!response.ok){
      const detail=data&&typeof data==='object'?(data.detail||data.error||response.statusText):data;
      throw new Error(String(detail||response.statusText||response.status));
    }
    return data;
  }
  const jsonOptions=(method,payload)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const statusClass=s=>s==='PUBLISHED'||s==='AVAILABLE'||s==='CONFIRMED'?'ok':s==='DEPRECATED'||s==='DEFERRED'||s==='SOURCE_UNAVAILABLE'?'warn':s==='STRUCTURE_EXTRACTION_FAILED'||s==='EVIDENCE_MISSING'?'bad':'';
  const fmtTime=value=>{
    if(!value)return '—';
    const d=new Date(value);return Number.isNaN(d.getTime())?String(value):d.toLocaleString('zh-CN',{hour12:false});
  };
  const productName=pc=>{
    if(!pc||typeof pc!=='object')return '—';
    return pc.product||pc.product_name||pc.model||Object.values(pc).find(v=>v!==null&&v!=='')||'—';
  };
  const factValue=(facts,name)=>{
    const value=facts&&facts[name];
    if(value&&typeof value==='object'){
      if(value.confirmed_value!==undefined&&value.confirmed_value!==null&&value.confirmed_value!=='')return value.confirmed_value;
      if(value.candidate_value!==undefined&&value.candidate_value!==null&&value.candidate_value!=='')return value.candidate_value;
      return '—';
    }
    return value===undefined||value===null||value===''?'—':value;
  };
  const mappingPaths=(item,type)=>{
    const m=item&&item.mapping_paths&&item.mapping_paths[type];
    if(Array.isArray(m))return m.filter(Boolean);
    const mappings=Array.isArray(item&&item.mappings)?item.mappings:[];
    return mappings.filter(x=>x.tree_type===type&&x.mapping_status==='CONFIRMED').map(x=>x.node_path||x.path_snapshot).filter(Boolean);
  };
  const caseHref=(id,maintainer=false)=>'/p0/hardware-cases/'+encodeURIComponent(id)+(maintainer?'?role=maintainer':'');
  const evidenceHealth=item=>item.evidence_health||'AVAILABLE';
  function caseCard(item,{maintainer=false}={}){
    const facts=item.facts||{};
    const circuit=mappingPaths(item,'CIRCUIT_FEATURE');
    const material=mappingPaths(item,'MATERIAL_DEVICE');
    const health=evidenceHealth(item);
    const status=item.case_status||'—';
    return '<article class="hc-case-item">'+
      '<div><h3><a href="'+caseHref(item.case_id,maintainer)+'">'+esc(item.title||item.case_id)+'</a></h3>'+
      '<div class="hc-case-meta"><code>'+esc(item.case_id)+'</code><span>'+esc(productName(item.product_context))+'</span>'+
      '<span class="hc-status '+statusClass(status)+'">'+esc(status)+'</span>'+
      '<span class="hc-status '+statusClass(health)+'">'+esc(health)+'</span></div>'+
      '<div class="hc-case-copy"><div><b>问题现象</b><p>'+esc(factValue(facts,'symptom'))+'</p></div><div><b>根因</b><p>'+esc(factValue(facts,'root_cause'))+'</p></div></div>'+
      '<div class="hc-path-row">'+circuit.slice(0,2).map(p=>'<span class="hc-path">电路 · '+esc(p)+'</span>').join('')+
      material.slice(0,2).map(p=>'<span class="hc-path material">器件 · '+esc(p)+'</span>').join('')+'</div></div>'+
      '<div class="hc-case-meta"><span>'+esc(fmtTime(item.published_at||item.updated_at))+'</span></div>'+
    '</article>';
  }
  async function safe(path,options={},authRole=role){
    try{return await request(path,options,authRole)}
    catch(error){toast(error.message);return null}
  }

  async function initHome(){
    const casesPayload=await safe('',{},'CONSUMER');
    const cases=(casesPayload&&casesPayload.results)||[];
    const recent=[...cases].sort((a,b)=>String(b.published_at||b.updated_at||'').localeCompare(String(a.published_at||a.updated_at||''))).slice(0,8);
    const recentBox=qs('[data-recent-cases]');
    recentBox.innerHTML=recent.length?recent.map(x=>caseCard(x)).join(''):'<div class="hc-empty">当前暂无已发布硬件案例。'+(role==='MAINTAINER'?'可进入案例确认或基础数据管理继续准备数据。':'')+'</div>';

    for(const type of ['CIRCUIT_FEATURE','MATERIAL_DEVICE']){
      const tree=await safe('/trees/'+type,{},'CONSUMER');
      const card=qs('[data-home-tree="'+type+'"]');
      if(!card)continue;
      const nodes=(tree&&tree.nodes)||[];
      qs('[data-node-count]',card).textContent=nodes.length;
      qs('[data-case-count]',card).textContent=cases.filter(item=>mappingPaths(item,type).length).length;
    }
    if(role==='MAINTAINER'){
      const all=await safe('',{},'MAINTAINER');
      const maintenance=(all&&all.results)||[];
      const pending=maintenance.filter(x=>!['PUBLISHED','DEPRECATED'].includes(x.case_status));
      qs('[data-pending-review]').textContent=pending.length;
      const mappingFlags=await Promise.all(pending.slice(0,50).map(async item=>{
        const p=await safe('/'+encodeURIComponent(item.case_id)+'/mappings',{},'MAINTAINER');
        const maps=(p&&p.mappings)||[];
        return maps.some(x=>x.mapping_status==='CONFIRMED')?0:1;
      }));
      qs('[data-pending-mapping]').textContent=mappingFlags.reduce((a,b)=>a+b,0);
      const anomalies=await safe('/maintenance/anomalies',{},'MAINTAINER');
      qs('[data-evidence-anomaly]').textContent=anomalies?Number(anomalies.total||0):0;
    }
    const form=qs('[data-home-search]');
    form&&form.addEventListener('submit',event=>{
      event.preventDefault();
      const q=qs('[data-home-query]').value.trim();
      const suffix=role==='MAINTAINER'?'&role=maintainer':'';
      location.href='/p0/hardware-cases/search?q='+encodeURIComponent(q)+suffix;
    });
  }

  let treeState={type:'CIRCUIT_FEATURE',nodes:[],selected:null};
  function nodePath(node){return Array.isArray(node.path)?node.path.join(' > '):(node.node_path||node.name||'—')}
  function renderTreeList(){
    const needle=(qs('[data-node-search]').value||'').trim().toLowerCase();
    const box=qs('[data-tree-list]');
    const nodes=treeState.nodes.filter(n=>!needle||nodePath(n).toLowerCase().includes(needle)||String(n.name||'').toLowerCase().includes(needle));
    if(!nodes.length){box.innerHTML='<div class="hc-empty">当前树没有可用节点。</div>';return}
    const ordered=[...nodes].sort((a,b)=>{
      const ap=Array.isArray(a.path)?a.path:[];const bp=Array.isArray(b.path)?b.path:[];
      return ap.join('/').localeCompare(bp.join('/'),'zh-CN');
    });
    box.innerHTML=ordered.map(node=>{
      const depth=Math.max((Array.isArray(node.path)?node.path.length:1)-1,0);
      const active=treeState.selected&&treeState.selected.node_id===node.node_id?' active':'';
      return '<button type="button" class="hc-tree-node'+active+'" data-node-id="'+esc(node.node_id)+'" style="padding-left:'+(8+depth*16)+'px"><span>›</span><span>'+esc(node.name||nodePath(node))+'</span></button>';
    }).join('');
  }
  async function loadTree(type){
    treeState.type=type;
    qsa('[data-tree-type]').forEach(b=>b.classList.toggle('active',b.dataset.treeType===type));
    const payload=await safe('/trees/'+encodeURIComponent(type),{},'CONSUMER');
    treeState.nodes=(payload&&payload.nodes)||[];treeState.selected=null;renderTreeList();
    qs('[data-node-name]').textContent='未选择节点';
    qs('[data-node-breadcrumb]').textContent=type==='CIRCUIT_FEATURE'?'电路 / 特性树':'物料 / 器件树';
    qs('[data-node-description]').textContent=treeState.nodes.length?'从左侧选择一个节点查看历史案例。':'基础树尚未生效。';
    qs('[data-node-path]').textContent='—';qs('[data-node-case-count]').textContent='—';
    qs('[data-tree-cases]').innerHTML='<div class="hc-empty">'+(treeState.nodes.length?'请选择节点。':'当前基础树没有 ACTIVE 节点。')+'</div>';
  }
  async function selectNode(id){
    const node=treeState.nodes.find(n=>String(n.node_id)===String(id));if(!node)return;
    treeState.selected=node;renderTreeList();
    qs('[data-node-breadcrumb]').textContent=nodePath(node);
    qs('[data-node-name]').textContent=node.name||'未命名节点';
    qs('[data-node-description]').textContent=node.description||'当前节点使用正式 ACTIVE Tree 数据。';
    qs('[data-node-path]').textContent=nodePath(node);
    qs('[data-node-case-count]').textContent='读取中…';
    const payload=await safe('/tree-nodes/'+encodeURIComponent(node.node_id)+'/cases',{},'CONSUMER');
    const results=(payload&&payload.results)||[];
    qs('[data-node-case-count]').textContent=results.length;
    qs('[data-tree-cases]').innerHTML=results.length?results.map(x=>caseCard(x)).join(''):'<div class="hc-empty">当前节点暂无已发布案例。可使用“搜索全库”继续查找。</div>';
  }
  async function initTree(){
    const params=new URLSearchParams(location.search);const type=params.get('type');
    treeState.type=type==='MATERIAL_DEVICE'?'MATERIAL_DEVICE':'CIRCUIT_FEATURE';
    qsa('[data-tree-type]').forEach(b=>b.addEventListener('click',()=>loadTree(b.dataset.treeType)));
    qs('[data-node-search]').addEventListener('input',renderTreeList);
    qs('[data-tree-list]').addEventListener('click',e=>{
      const b=e.target.closest('[data-node-id]');if(b)selectNode(b.dataset.nodeId);
    });
    const searchAll=qs('[data-search-all]');if(searchAll&&role==='MAINTAINER')searchAll.href+='?role=maintainer';
    await loadTree(treeState.type);
  }

  let searchItems=[];
  function applySearchFilters(){
    const product=(qs('[data-filter-product]').value||'').trim().toLowerCase();
    const circuit=(qs('[data-filter-circuit]').value||'').trim().toLowerCase();
    const material=(qs('[data-filter-material]').value||'').trim().toLowerCase();
    const filtered=searchItems.filter(item=>{
      const p=String(productName(item.product_context)).toLowerCase();
      const c=mappingPaths(item,'CIRCUIT_FEATURE').join(' ').toLowerCase();
      const m=mappingPaths(item,'MATERIAL_DEVICE').join(' ').toLowerCase();
      return (!product||p.includes(product))&&(!circuit||c.includes(circuit))&&(!material||m.includes(material));
    });
    qs('[data-search-summary]').textContent='共 '+filtered.length+' 条结果；普通消费面只展示 PUBLISHED。';
    qs('[data-search-results]').innerHTML=filtered.length?filtered.map(x=>caseCard(x)).join(''):'<div class="hc-empty">未找到匹配案例。可清空筛选或切换双树导航。</div>';
  }
  async function runSearch(q){
    const payload=await safe('?q='+encodeURIComponent(q||''),{},'CONSUMER');
    searchItems=(payload&&payload.results)||[];applySearchFilters();
  }
  async function initSearch(){
    const params=new URLSearchParams(location.search);const initial=params.get('q')||'';
    qs('[data-search-query]').value=initial;
    qs('[data-search-form]').addEventListener('submit',e=>{e.preventDefault();runSearch(qs('[data-search-query]').value.trim())});
    qsa('[data-filter-product],[data-filter-circuit],[data-filter-material]').forEach(el=>el.addEventListener('input',applySearchFilters));
    qs('[data-clear-filters]').addEventListener('click',()=>{qsa('[data-filter-product],[data-filter-circuit],[data-filter-material]').forEach(x=>x.value='');applySearchFilters()});
    await runSearch(initial);
  }

  const FACTS=[
    ['symptom','问题现象'],['impact','影响'],['occurrence_condition','发生条件'],
    ['analysis_process','分析过程'],['failure_mode','失效模式'],['root_cause','根因'],
    ['failure_mechanism','失效机理'],['actions','解决措施'],['verification_result','验证结果'],
    ['background','背景'],['conclusion','结论']
  ];
  function renderFacts(container,caseData,evidence){
    const facts=caseData.facts||{};
    const evMap=new Map((evidence||[]).map(e=>[e.evidence_id,e]));
    const html=FACTS.filter(([key])=>facts[key]!==undefined).map(([key,label])=>{
      const raw=facts[key];
      const value=factValue(facts,key);
      const refs=raw&&typeof raw==='object'&&Array.isArray(raw.evidence_refs)?raw.evidence_refs:[];
      const buttons=refs.map(id=>evMap.get(id)).filter(Boolean).map(ev=>'<button type="button" class="hc-button secondary" data-open-evidence="'+esc(ev.evidence_id)+'">查看 Evidence</button>').join('');
      return '<section class="hc-fact-section"><h3>'+esc(label)+'</h3><p>'+esc(value)+'</p>'+(buttons?'<div class="hc-fact-evidence">'+buttons+'</div>':'')+'</section>';
    }).join('');
    container.innerHTML=html||'<div class="hc-empty">当前案例暂无可展示的结构化事实。</div>';
  }
  function renderMappings(container,mappings){
    const groups=[['CIRCUIT_FEATURE','电路 / 特性'],['MATERIAL_DEVICE','物料 / 器件']];
    container.innerHTML=groups.map(([type,label])=>{
      const items=(mappings||[]).filter(x=>x.tree_type===type);
      return '<div class="hc-mapping-group"><h3>'+label+'</h3>'+(items.length?items.map(x=>
        '<div class="hc-mapping-item"><b>'+esc(x.node_path||x.path_snapshot||x.node_id)+'</b><small>'+esc(x.relation_role||'')+' · '+esc(x.mapping_status||'')+(x.tree_version?' · '+esc(x.tree_version):'')+'</small></div>'
      ).join(''):'<div class="hc-empty">未关联</div>')+'</div>';
    }).join('');
  }
  function renderEvidenceList(container,evidence,{review=false}={}){
    if(!evidence.length){container.innerHTML='<div class="hc-empty">暂无 Evidence。</div>';return}
    container.innerHTML=evidence.map(ev=>
      '<div class="hc-evidence-item" data-'+(review?'review-':'')+'evidence-id="'+esc(ev.evidence_id)+'">'+
      '<strong>'+esc(ev.evidence_type||'EVIDENCE')+' · '+esc(ev.evidence_status||'—')+'</strong>'+
      '<p>'+esc(ev.excerpt_or_caption||'无摘录')+'</p><small>'+esc(ev.source_ref||'—')+' · '+esc((ev.locator&&ev.locator.section)||((ev.locator&&ev.locator.block_id)||''))+'</small></div>'
    ).join('');
  }
  async function sourcePreview(caseId,evidenceId,authRole=role){
    return request('/'+encodeURIComponent(caseId)+'/evidence/'+encodeURIComponent(evidenceId)+'/source-preview',{},authRole);
  }
  function previewHtml(payload){
    if(!payload)return '<div class="hc-error">Evidence 来源不可用。</div>';
    if(payload.preview_status!=='AVAILABLE')return '<div class="hc-source-warning">'+esc(payload.preview_status||payload.source_status||'SOURCE_UNAVAILABLE')+'</div>';
    return (payload.blocks||[]).map(block=>
      '<div class="hc-evidence-preview-block '+(block.matched?'matched':'')+'"><div class="hc-evidence-locator">'+esc(block.block_type||'')+' · '+esc((block.section_path||[]).join(' > '))+' · '+esc((block.source_locator&&block.source_locator.block_id)||'')+'</div><pre>'+esc(block.text||block.image_ref||'—')+'</pre></div>'
    ).join('');
  }
  async function openEvidenceDrawer(caseId,evidence,evidenceList){
    const drawer=qs('[data-evidence-drawer]'),back=qs('[data-evidence-backdrop]');if(!drawer)return;
    drawer.hidden=false;back.hidden=false;qs('[data-evidence-title]').textContent=(evidence.evidence_type||'Evidence')+' · '+(evidence.evidence_status||'—');
    const body=qs('[data-evidence-body]');body.innerHTML='<div class="hc-empty">正在读取原始来源…</div>';
    try{
      const preview=await sourcePreview(caseId,evidence.evidence_id,role);
      body.innerHTML='<div class="hc-case-meta"><span>'+esc(evidence.source_ref||'—')+'</span><span class="hc-status '+statusClass(evidence.evidence_status)+'">'+esc(evidence.evidence_status||'—')+'</span></div>'+
        '<p>'+esc(evidence.excerpt_or_caption||'')+'</p>'+previewHtml(preview)+
        '<button type="button" class="hc-button secondary" data-source-download="'+esc(evidence.evidence_id)+'">打开原始文件</button>';
    }catch(error){
      body.innerHTML='<div class="hc-source-warning">'+esc(error.message)+'</div><p>案例已确认内容仍保留，但当前原始来源无法安全访问。</p>';
    }
  }
  async function downloadSource(caseId,evidenceId,authRole=role){
    const response=await fetch(api+'/'+encodeURIComponent(caseId)+'/evidence/'+encodeURIComponent(evidenceId)+'/source-file',{headers:roleHeaders(authRole)});
    if(!response.ok){let detail='SOURCE_UNAVAILABLE';try{detail=(await response.json()).detail||detail}catch(_){}throw new Error(detail)}
    const blob=await response.blob();const disposition=response.headers.get('content-disposition')||'';const match=/filename="?([^";]+)"?/i.exec(disposition);const name=match?decodeURIComponent(match[1]):'evidence-source';
    const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1200);
  }
  async function initDetail(){
    const id=root.dataset.caseId;
    const [caseData,mappingPayload,evidencePayload]=await Promise.all([
      safe('/'+encodeURIComponent(id),{},role),
      safe('/'+encodeURIComponent(id)+'/mappings',{},role),
      safe('/'+encodeURIComponent(id)+'/evidence',{},role)
    ]);
    if(!caseData){qs('[data-case-title]').textContent='案例不可访问';return}
    const mappings=(mappingPayload&&mappingPayload.mappings)||[];
    const evidence=(evidencePayload&&evidencePayload.evidence)||[];
    qs('[data-case-title]').textContent=caseData.title||id;qs('[data-case-id-text]').textContent=id;
    qs('[data-case-status]').textContent=caseData.case_status||'—';qs('[data-case-status]').className='hc-status '+statusClass(caseData.case_status);
    qs('[data-case-product]').textContent=productName(caseData.product_context);qs('[data-case-time]').textContent=fmtTime(caseData.published_at||caseData.updated_at);
    if(caseData.evidence_health==='SOURCE_UNAVAILABLE'||evidence.some(x=>x.evidence_status==='SOURCE_UNAVAILABLE')){
      const w=qs('[data-case-warning]');w.hidden=false;w.textContent='Evidence 当前不可访问 / SOURCE_UNAVAILABLE。已确认案例内容保留，但证据来源需要维护。';
    }
    renderFacts(qs('[data-facts]'),caseData,evidence);renderMappings(qs('[data-case-mappings]'),mappings);renderEvidenceList(qs('[data-case-evidence]'),evidence);
    root.addEventListener('click',async e=>{
      const open=e.target.closest('[data-open-evidence],[data-evidence-id]');
      if(open){
        const eid=open.dataset.openEvidence||open.dataset.evidenceId;const ev=evidence.find(x=>x.evidence_id===eid);if(ev)openEvidenceDrawer(id,ev,evidence);return;
      }
      if(e.target.closest('[data-close-evidence]')||e.target.matches('[data-evidence-backdrop]')){qs('[data-evidence-drawer]').hidden=true;qs('[data-evidence-backdrop]').hidden=true;return}
      const dl=e.target.closest('[data-source-download]');if(dl){try{await downloadSource(id,dl.dataset.sourceDownload,role)}catch(error){toast(error.message)}}
    });
  }

  let reviewState={caseId:'',caseData:null,mappings:[],evidence:[],trees:{CIRCUIT_FEATURE:[],MATERIAL_DEVICE:[]}};
  async function loadReviewQueue(){
    const payload=await safe('',{},'MAINTAINER');const all=(payload&&payload.results)||[];
    const items=all.filter(x=>!['PUBLISHED','DEPRECATED'].includes(x.case_status));
    const box=qs('[data-review-queue]');
    box.innerHTML=items.length?items.map(item=>'<button type="button" class="hc-review-queue-item '+(reviewState.caseId===item.case_id?'active':'')+'" data-review-case="'+esc(item.case_id)+'"><strong>'+esc(item.title||item.case_id)+'</strong><small>'+esc(item.case_id)+' · '+esc(item.processing_status||item.case_status||'—')+'</small></button>').join(''):'<div class="hc-empty">当前没有待处理案例。</div>';
    return items;
  }
  function reviewFactCard(key,label,field){
    const obj=field&&typeof field==='object'?field:{candidate_value:field,confirmed_value:null,review_disposition:'UNREVIEWED',evidence_refs:[]};
    const candidate=text(obj.candidate_value);const confirmed=obj.confirmed_value??'';
    return '<article class="hc-review-fact" data-review-field="'+esc(key)+'"><div class="hc-review-fact-head"><h3>'+esc(label)+'</h3><span class="hc-status '+statusClass(obj.review_disposition)+'">'+esc(obj.review_disposition||'UNREVIEWED')+'</span></div>'+
      '<div class="hc-review-columns"><div class="hc-review-column"><label>AI 候选</label><p>'+esc(candidate)+'</p></div><div class="hc-review-column"><label>人工确认值</label><textarea class="hc-review-textarea" data-confirmed-value>'+esc(confirmed||candidate==='—'?'':candidate)+'</textarea></div></div>'+
      '<div class="hc-review-actions"><button type="button" class="hc-button secondary" data-review-action="DEFERRED">暂缓</button><button type="button" class="hc-button secondary" data-review-action="CONFIRMED_UNKNOWN">确认未知</button><button type="button" class="hc-button primary" data-review-action="CONFIRMED">确认</button></div></article>';
  }
  function treeOptions(type){
    return (reviewState.trees[type]||[]).map(n=>'<option value="'+esc(n.node_id)+'">'+esc(nodePath(n))+'</option>').join('');
  }
  function renderReviewMappings(){
    const box=qs('[data-review-mappings]');
    box.innerHTML=['CIRCUIT_FEATURE','MATERIAL_DEVICE'].map(type=>{
      const title=type==='CIRCUIT_FEATURE'?'电路 / 特性树':'物料 / 器件树';
      const existing=reviewState.mappings.filter(x=>x.tree_type===type);
      const rows=existing.length?existing.map(m=>'<div class="hc-mapping-row"><span>'+esc(m.relation_role||'PRIMARY')+'</span><div><b>'+esc(m.node_path||m.path_snapshot||m.node_id)+'</b><small class="hc-status '+statusClass(m.mapping_status)+'">'+esc(m.mapping_status||'—')+'</small></div>'+(m.mapping_status==='SUGGESTED'?'<button type="button" class="hc-button primary" data-confirm-mapping="'+esc(m.mapping_id)+'">确认挂接</button>':'<span></span>')+'</div>').join(''):'<div class="hc-empty">当前无 Mapping。</div>';
      return '<section><h4>'+title+'</h4>'+rows+'<div class="hc-mapping-row"><span>人工挂接</span><select class="hc-mapping-select" data-add-mapping-tree="'+type+'"><option value="">选择 ACTIVE 节点</option>'+treeOptions(type)+'</select><button type="button" class="hc-button secondary" data-add-mapping="'+type+'">确认节点</button></div></section>';
    }).join('');
  }
  function renderGate(gate){
    const blockers=(gate&&gate.blockers)||[];
    const checks=[
      ['CORE_FACTS_NOT_REVIEWED','核心事实已确认'],
      ['NO_VALID_EVIDENCE','至少 1 条 AVAILABLE Evidence'],
      ['NO_CONFIRMED_MAPPING','至少一棵树 Mapping=CONFIRMED']
    ];
    qs('[data-publish-checklist]').innerHTML=checks.map(([code,label])=>'<span class="hc-gate-item '+(!blockers.includes(code)?'pass':'')+'">'+(blockers.includes(code)?'× ':'✓ ')+esc(label)+'</span>').join('');
    qs('[data-publish]').disabled=!(gate&&gate.passed);
  }
  async function refreshReviewCase(id){
    reviewState.caseId=id;await loadReviewQueue();
    const [caseData,maps,ev,gate]=await Promise.all([
      safe('/'+encodeURIComponent(id),{},'MAINTAINER'),
      safe('/'+encodeURIComponent(id)+'/mappings',{},'MAINTAINER'),
      safe('/'+encodeURIComponent(id)+'/evidence',{},'MAINTAINER'),
      safe('/'+encodeURIComponent(id)+'/publish-gate',{},'MAINTAINER')
    ]);
    if(!caseData)return;
    reviewState.caseData=caseData;reviewState.mappings=(maps&&maps.mappings)||[];reviewState.evidence=(ev&&ev.evidence)||[];
    qs('[data-review-empty]').hidden=true;qs('[data-review-workspace]').hidden=false;
    qs('[data-review-title]').textContent=caseData.title||id;qs('[data-review-id]').textContent=id;qs('[data-review-status]').textContent=caseData.case_status||'—';qs('[data-review-processing]').textContent=caseData.processing_status||'—';
    qs('[data-open-detail]').href=caseHref(id,true);
    if(caseData.processing_status==='STRUCTURE_EXTRACTION_FAILED')toast('STRUCTURE_EXTRACTION_FAILED：保留原材料，请人工整理或重新执行结构化。');
    qs('[data-review-facts]').innerHTML=FACTS.filter(([key])=>caseData.facts&&caseData.facts[key]!==undefined).map(([key,label])=>reviewFactCard(key,label,caseData.facts[key])).join('')||'<div class="hc-empty">暂无结构化字段。</div>';
    renderReviewMappings();renderEvidenceList(qs('[data-review-evidence]'),reviewState.evidence,{review:true});renderGate(gate);
  }
  async function reviewField(field,disposition,value){
    const id=reviewState.caseId;if(!id)return;
    const payload={field_name:field,disposition,confirmed_value:disposition==='CONFIRMED'?value:null};
    await request('/'+encodeURIComponent(id)+'/review',jsonOptions('POST',payload),'MAINTAINER');toast('字段已保存');await refreshReviewCase(id);
  }
  async function confirmMapping(mappingId){
    const mapping=reviewState.mappings.find(x=>x.mapping_id===mappingId);if(!mapping)return;
    await request('/'+encodeURIComponent(reviewState.caseId)+'/mappings',jsonOptions('POST',{...mapping,mapping_status:'CONFIRMED'}),'MAINTAINER');toast('Mapping 已确认');await refreshReviewCase(reviewState.caseId);
  }
  async function addMapping(type){
    const select=qs('[data-add-mapping-tree="'+type+'"]');const nodeId=select&&select.value;if(!nodeId){toast('请选择节点');return}
    const same=reviewState.mappings.filter(x=>x.tree_type===type);
    const payload={
      mapping_id:'MAP-WEB-'+Date.now()+'-'+Math.random().toString(16).slice(2,8),
      tree_type:type,node_id:nodeId,
      relation_role:same.some(x=>x.relation_role==='PRIMARY')?'SECONDARY':'PRIMARY',
      mapping_status:'CONFIRMED',confidence:null,basis_refs:[]
    };
    await request('/'+encodeURIComponent(reviewState.caseId)+'/mappings',jsonOptions('POST',payload),'MAINTAINER');toast('节点已挂接');await refreshReviewCase(reviewState.caseId);
  }
  async function reviewPreview(evidenceId){
    const box=qs('[data-review-preview]');box.innerHTML='<div class="hc-empty">正在读取原始来源…</div>';
    try{const payload=await sourcePreview(reviewState.caseId,evidenceId,'MAINTAINER');box.innerHTML=previewHtml(payload)}
    catch(error){box.innerHTML='<div class="hc-source-warning">'+esc(error.message)+'</div>'}
  }
  async function initReview(){
    const [cTree,mTree]=await Promise.all([safe('/trees/CIRCUIT_FEATURE',{},'MAINTAINER'),safe('/trees/MATERIAL_DEVICE',{},'MAINTAINER')]);
    reviewState.trees.CIRCUIT_FEATURE=(cTree&&cTree.nodes)||[];reviewState.trees.MATERIAL_DEVICE=(mTree&&mTree.nodes)||[];
    const items=await loadReviewQueue();
    const requested=root.dataset.caseId||'';const initial=requested||(items[0]&&items[0].case_id)||'';
    if(initial)await refreshReviewCase(initial);
    root.addEventListener('click',async e=>{
      const queue=e.target.closest('[data-review-case]');if(queue){await refreshReviewCase(queue.dataset.reviewCase);return}
      const action=e.target.closest('[data-review-action]');if(action){
        const card=action.closest('[data-review-field]');const field=card.dataset.reviewField;const value=qs('[data-confirmed-value]',card).value;
        try{await reviewField(field,action.dataset.reviewAction,value)}catch(error){toast(error.message)}return;
      }
      const confirm=e.target.closest('[data-confirm-mapping]');if(confirm){try{await confirmMapping(confirm.dataset.confirmMapping)}catch(error){toast(error.message)}return}
      const add=e.target.closest('[data-add-mapping]');if(add){try{await addMapping(add.dataset.addMapping)}catch(error){toast(error.message)}return}
      const ev=e.target.closest('[data-review-evidence-id]');if(ev){await reviewPreview(ev.dataset.reviewEvidenceId);return}
      if(e.target.closest('[data-save-draft]')){toast('当前修改已逐项保存为维护状态，不会自动发布。');return}
      if(e.target.closest('[data-publish]')){
        if(!reviewState.caseId)return;
        try{
          const result=await request('/'+encodeURIComponent(reviewState.caseId)+'/publish',{method:'POST'},'MAINTAINER');
          if(result.passed===false){toast('Publish Gate 未通过：'+(result.blockers||[]).join('、'));return}
          toast('案例已发布');await refreshReviewCase(reviewState.caseId);
        }catch(error){toast(error.message)}
      }
    });
  }

  if(page==='home')initHome();
  else if(page==='tree')initTree();
  else if(page==='search')initSearch();
  else if(page==='detail')initDetail();
  else if(page==='review')initReview();
})();
