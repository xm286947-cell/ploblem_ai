(function(){
  'use strict';
  const root=document.querySelector('[data-hardware-tree-import]');if(!root)return;
  const importApi=(window.HC_TREE_IMPORT_API||root.dataset.importApi||'/api/v2/hardware-cases/tree-imports').replace(/\/$/,'');
  const treeApi=(window.HC_TREE_API||root.dataset.treeApi||'/api/v2/hardware-cases').replace(/\/$/,'');
  const operator=root.dataset.operator||'web-maintainer';
  const roleHeaders={'X-Hardware-Case-Role':'MAINTAINER'};
  const state={
    step:1,treeType:'CIRCUIT_FEATURE',job:null,workbook:null,raw:null,analysis:null,
    history:[],activeVersions:{},applying:false,excludedSnapshot:[]
  };

  const qs=(s,n=root)=>n.querySelector(s);
  const qsa=(s,n=root)=>[...n.querySelectorAll(s)];
  const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text=v=>String(v==null?'':v);
  const jsonOptions=(method,body)=>({method,headers:{...roleHeaders,'Content-Type':'application/json'},body:JSON.stringify(body||{})});

  async function request(url,options){
    const response=await fetch(url,options);
    const data=await response.json().catch(()=>({}));
    if(!response.ok){
      const error=new Error(data.detail||('HTTP_'+response.status));
      error.status=response.status;error.payload=data;throw error;
    }
    return data;
  }

  function toast(message){
    const box=qs('[data-toast]');box.textContent=message;box.hidden=false;
    clearTimeout(toast.timer);toast.timer=setTimeout(()=>box.hidden=true,3600);
  }

  function treeLabel(type){return type==='MATERIAL_DEVICE'?'物料 / 器件树':'电路 / 特性树'}
  function humanStatus(status){
    if(status==='APPLIED')return 'SUCCESS';
    if(status==='APPLIED_WITH_EXCLUSIONS')return status;
    if(String(status||'').endsWith('_FAILED'))return 'FAILED';
    if(status==='READY_TO_APPLY'||status==='APPLYING'||status==='REVIEW_REQUIRED'||status==='VALIDATING'||status==='PARSED'||status==='UPLOADED'||status==='MAPPING_REQUIRED')return 'IN_PROGRESS';
    return status||'—';
  }
  function statusClass(status){
    const h=humanStatus(status);
    if(h==='SUCCESS')return 'ok';if(h==='APPLIED_WITH_EXCLUSIONS'||h==='IN_PROGRESS')return 'warn';if(h==='FAILED')return 'bad';return '';
  }
  function formatTime(value){
    if(!value)return '—';
    const normalized=String(value).includes('T')?String(value):String(value).replace(' ','T')+'Z';
    const d=new Date(normalized);return Number.isNaN(d.getTime())?String(value):d.toLocaleString('zh-CN',{hour12:false});
  }
  function errorLabel(code){
    const map={
      EXCEL_FILE_EMPTY:'Excel 文件为空',EXCEL_FORMAT_UNSUPPORTED:'仅支持 .xlsx / .xlsm',
      EXCEL_PARSE_FAILED:'Workbook 解析失败',TREE_SHEET_NOT_FOUND:'Sheet 不存在',
      HEADER_ROW_INVALID:'表头行无效',HEADER_ROW_OUT_OF_RANGE:'表头行超出工作表范围',
      MAPPING_COLUMN_NOT_FOUND:'Mapping 列不存在',MAPPING_COLUMN_AMBIGUOUS:'Excel 存在同名列，无法唯一映射',
      SOURCE_HASH_MISMATCH:'上传文件已变化，请重新上传',UNRESOLVED_VALIDATION_ISSUES:'仍有阻断 Validation',
      UNRESOLVED_CONFLICT:'仍有未处理 Conflict',CHANGE_REVIEW_INCOMPLETE:'仍有 Change 未确认',
      IMPORT_NOT_READY_TO_APPLY:'当前任务尚未达到 Apply 条件',TREE_PARENT_NOT_FOUND:'树父节点关系无效',
      TREE_CYCLE:'树层级存在循环',APPLY_INTERNAL_ERROR:'Apply 内部失败'
    };return map[code]||code||'未知错误';
  }

  async function loadHome(){
    const cards=qsa('[data-tree-card]');
    cards.forEach(x=>x.classList.add('hti-loading'));
    try{
      const history=await request(importApi,{headers:roleHeaders});
      state.history=history.items||[];
      await Promise.all(['CIRCUIT_FEATURE','MATERIAL_DEVICE'].map(async type=>{
        const [version,tree]=await Promise.all([
          request(importApi+'/active-version/'+encodeURIComponent(type),{headers:roleHeaders}),
          request(treeApi+'/trees/'+encodeURIComponent(type),{headers:roleHeaders})
        ]);
        state.activeVersions[type]=version.active_version||null;
        renderTreeCard(type,tree.nodes||[]);
      }));
      renderHistory();
    }catch(error){toast('基础数据状态读取失败：'+errorLabel(error.message))}
    finally{cards.forEach(x=>x.classList.remove('hti-loading'))}
  }

  function renderTreeCard(type,nodes){
    const card=qs('[data-tree-card="'+type+'"]');if(!card)return;
    const version=state.activeVersions[type];
    const jobs=state.history.filter(x=>x.tree_type===type);
    const latest=jobs.length?jobs[jobs.length-1]:null;
    const anomalies=jobs.filter(x=>{
      const status=humanStatus(x.status);
      return status==='FAILED'||Number((x.counts||{}).validation_issue_count||0)>0;
    }).length;
    qsa('[data-active-version],[data-version-value]',card).forEach(x=>x.textContent=version?version.version_id:'未初始化');
    qs('[data-node-count]',card).textContent=nodes.length;
    qs('[data-latest-import]',card).textContent=latest?(humanStatus(latest.status)+' · '+formatTime(latest.updated_at||latest.created_at)):'暂无';
    qs('[data-anomaly-count]',card).textContent=anomalies?String(anomalies):'0';
  }

  function filteredHistory(){
    const tree=qs('[data-history-tree]').value;
    const result=qs('[data-history-result]').value;
    return state.history.filter(x=>(!tree||x.tree_type===tree)&&(!result||humanStatus(x.status)===result)).slice().reverse();
  }
  function renderHistory(){
    const body=qs('[data-history-body]');const items=filteredHistory();
    if(!items.length){body.innerHTML='<tr><td colspan="8" class="hti-empty-cell">暂无匹配的导入任务。</td></tr>';return}
    body.innerHTML=items.map(job=>{
      const issues=Number((job.counts||{}).validation_issue_count||0);
      return '<tr>'+
        '<td><code>'+esc(job.job_id)+'</code></td>'+
        '<td>'+esc(treeLabel(job.tree_type))+'</td>'+
        '<td>'+esc(job.source_filename)+'</td>'+
        '<td>'+esc(formatTime(job.created_at))+'</td>'+
        '<td><span class="hti-status '+statusClass(job.status)+'">'+esc(humanStatus(job.status))+'</span></td>'+
        '<td>'+esc(job.applied_version_id||'—')+'</td>'+
        '<td>'+issues+'</td>'+
        '<td>'+esc(job.operator||'—')+'</td>'+
      '</tr>';
    }).join('');
  }

  async function showVersions(treeType){
    const panel=qs('[data-version-panel]');const body=qs('[data-version-body]');
    panel.hidden=false;qs('[data-version-title]').textContent=treeLabel(treeType)+' · Tree Version';
    body.innerHTML='<tr><td colspan="5" class="hti-empty-cell">正在读取版本…</td></tr>';
    try{
      const data=await request(importApi+'/versions/'+encodeURIComponent(treeType),{headers:roleHeaders});
      const items=data.items||[];
      body.innerHTML=items.length?items.map(version=>
        '<tr>'+
          '<td><strong>'+esc(version.version_id)+'</strong></td>'+
          '<td><span class="hti-status '+(version.status==='ACTIVE'?'ok':'')+'">'+esc(version.status)+'</span></td>'+
          '<td>'+esc(formatTime(version.created_at))+'</td>'+
          '<td>'+esc(formatTime(version.activated_at))+'</td>'+
          '<td><code>'+esc(version.source_job_id)+'</code></td>'+
        '</tr>'
      ).join(''):'<tr><td colspan="5" class="hti-empty-cell">该树尚未生成正式版本。</td></tr>';
      panel.scrollIntoView({behavior:'smooth',block:'start'});
    }catch(error){
      body.innerHTML='<tr><td colspan="5" class="hti-empty-cell">版本读取失败：'+esc(errorLabel(error.message))+'</td></tr>';
    }
  }

  function resetWizard(treeType){
    state.step=1;state.treeType=treeType||'CIRCUIT_FEATURE';state.job=null;state.workbook=null;state.raw=null;state.analysis=null;state.applying=false;state.excludedSnapshot=[];
    qs('[data-tree-type]').value=state.treeType;
    qs('[data-excel-file]').value='';
    qs('[data-upload-summary]').hidden=true;
    qs('[data-sheet-select]').innerHTML='';
    qs('[data-header-row]').value='1';
    qs('[data-mapping-body]').innerHTML='<tr><td colspan="5" class="hti-empty-cell">选择 Sheet 和表头行后读取列。</td></tr>';
    qs('[data-run-analysis]').disabled=true;
    qs('[data-mapping-error]').hidden=true;
    qs('[data-validation-blocker]').hidden=true;
    qs('[data-atomic-confirm]').checked=false;
    qs('[data-apply]').disabled=true;
    qs('[data-result]').hidden=true;
    qs('[data-confirm-state]').hidden=false;
    showStep(1);
  }

  function openWizard(treeType){
    resetWizard(treeType);
    qs('[data-wizard]').hidden=false;
    qs('[data-wizard-title]').textContent='导入 / 更新 '+treeLabel(state.treeType);
    qs('[data-wizard]').scrollIntoView({behavior:'smooth',block:'start'});
  }
  function closeWizard(){if(state.applying)return;qs('[data-wizard]').hidden=true}
  function showStep(step){
    state.step=step;
    qsa('[data-step-panel]').forEach(x=>x.hidden=Number(x.dataset.stepPanel)!==step);
    qsa('[data-step-indicator]').forEach(x=>{
      const n=Number(x.dataset.stepIndicator);x.classList.toggle('active',n===step);x.classList.toggle('done',n<step);
    });
  }
  function setBusy(node,busy){if(!node)return;node.disabled=busy;node.classList.toggle('hti-loading',busy)}

  async function upload(){
    const file=qs('[data-excel-file]').files[0];if(!file){toast('请选择 Excel 文件');return}
    state.treeType=qs('[data-tree-type]').value;
    const button=qs('[data-upload]');setBusy(button,true);
    const form=new FormData();form.append('tree_type',state.treeType);form.append('file',file);
    try{
      const data=await request(importApi,{method:'POST',headers:{...roleHeaders,'X-Hardware-Case-Operator':operator},body:form});
      state.job=data.job;state.workbook=data.workbook;
      qs('[data-upload-summary]').hidden=false;
      qs('[data-upload-file]').textContent=data.job.source_filename;
      qs('[data-upload-type]').textContent=data.job.import_type;
      qs('[data-upload-status]').textContent=data.job.status;
      qs('[data-upload-job]').textContent=data.job.job_id;
      const sheets=(data.workbook.sheets||[]);
      qs('[data-sheet-select]').innerHTML=sheets.map(s=>'<option value="'+esc(s.sheet_name)+'">'+esc(s.sheet_name)+' · '+s.max_row+' 行 × '+s.max_column+' 列</option>').join('');
      if(!sheets.length)throw new Error('TREE_SHEET_NOT_FOUND');
      qs('[data-header-row]').value='1';
      showStep(2);
      toast('Workbook 已检查，请配置 Sheet、表头与动态层级 Mapping');
    }catch(error){toast('上传失败：'+errorLabel(error.message))}
    finally{setBusy(button,false)}
  }

  async function loadColumns(){
    if(!state.job)return;
    const sheet=qs('[data-sheet-select]').value;
    const headerRow=Number(qs('[data-header-row]').value||0);
    const button=qs('[data-load-columns]');setBusy(button,true);
    try{
      state.raw=await request(importApi+'/'+encodeURIComponent(state.job.job_id)+'/workbook-preview',jsonOptions('POST',{sheet_name:sheet,header_row:headerRow,max_rows:20}));
      renderMappingBuilder();
      validateMappingBuilder();
    }catch(error){
      qs('[data-mapping-error]').textContent=errorLabel(error.message);qs('[data-mapping-error]').hidden=false;
      qs('[data-run-analysis]').disabled=true;
    }finally{setBusy(button,false)}
  }

  function samplesForColumn(index){
    if(!state.raw)return [];
    const values=(state.raw.rows||[]).map(r=>text((r.values||[])[index])).filter(Boolean);
    return [...new Set(values)].slice(0,3);
  }
  function renderMappingBuilder(){
    const body=qs('[data-mapping-body]');const columns=(state.raw&&state.raw.columns)||[];
    qs('[data-mapping-error]').hidden=true;
    if(!columns.length){body.innerHTML='<tr><td colspan="5" class="hti-empty-cell">表头行没有可用列，请调整表头行。</td></tr>';return}
    body.innerHTML=columns.map((column,index)=>{
      const name=column.column_name||('未命名列 '+column.column_key);
      return '<tr data-map-row data-column-name="'+esc(column.column_name)+'">'+
        '<td>'+esc(name)+'</td>'+
        '<td><span class="hti-col-key">'+esc(column.column_key)+'</span></td>'+
        '<td><select class="mapping-role" data-map-role>'+
          '<option value="IGNORE">忽略</option><option value="LEVEL">LEVEL</option><option value="METADATA">Metadata</option><option value="BUSINESS_KEY">Business Key</option>'+
        '</select></td>'+
        '<td><input class="mapping-order" data-map-order type="number" min="1" disabled placeholder="顺序"></td>'+
        '<td class="hti-sample">'+esc(samplesForColumn(index).join(' ｜ ')||'—')+'</td>'+
      '</tr>';
    }).join('');
  }
  function validateMappingBuilder(){
    const rows=qsa('[data-map-row]');
    const levelRows=rows.filter(r=>qs('[data-map-role]',r).value==='LEVEL');
    const businessRows=rows.filter(r=>qs('[data-map-role]',r).value==='BUSINESS_KEY');
    const names=rows.map(r=>r.dataset.columnName).filter(Boolean);
    const duplicateNames=names.filter((n,i)=>names.indexOf(n)!==i);
    const orders=levelRows.map(r=>Number(qs('[data-map-order]',r).value||0));
    let error='';
    if(!rows.length)error='请先读取表头。';
    else if(duplicateNames.length)error='存在同名表头：'+[...new Set(duplicateNames)].join('、')+'。请修改 Excel 后重新上传。';
    else if(!levelRows.length)error='至少选择 1 个 LEVEL 层级列。';
    else if(levelRows.some(r=>!r.dataset.columnName))error='未命名列不能作为 LEVEL。';
    else if(orders.some(n=>n<1)||new Set(orders).size!==orders.length)error='LEVEL 必须设置唯一且连续可排序的层级顺序。';
    else if(businessRows.length>1)error='Business Key 最多选择 1 列。';
    qs('[data-mapping-error]').hidden=!error;qs('[data-mapping-error]').textContent=error;
    qs('[data-run-analysis]').disabled=Boolean(error);
    return !error;
  }
  function buildProfile(){
    if(!validateMappingBuilder())throw new Error('MAPPING_INVALID');
    const rows=qsa('[data-map-row]');
    const levels=rows.filter(r=>qs('[data-map-role]',r).value==='LEVEL').sort((a,b)=>Number(qs('[data-map-order]',a).value)-Number(qs('[data-map-order]',b).value));
    const metadata=rows.filter(r=>qs('[data-map-role]',r).value==='METADATA');
    const business=rows.find(r=>qs('[data-map-role]',r).value==='BUSINESS_KEY');
    return {
      sheet_name:qs('[data-sheet-select]').value,
      header_row:Number(qs('[data-header-row]').value),
      path_columns:levels.map(r=>r.dataset.columnName),
      metadata_columns:metadata.map(r=>r.dataset.columnName),
      business_key_column:business?business.dataset.columnName:null
    };
  }

  async function analyze(){
    if(!state.job)return;
    let profile;try{profile=buildProfile()}catch(_){toast('请完成结构映射');return}
    const button=qs('[data-run-analysis]');setBusy(button,true);
    try{
      const data=await request(importApi+'/'+encodeURIComponent(state.job.job_id)+'/analyze',jsonOptions('POST',profile));
      state.analysis=data;state.job=data.job;
      renderAnalysis();showStep(3);
    }catch(error){toast('解析/Validation 失败：'+errorLabel(error.message))}
    finally{setBusy(button,false)}
  }

  function parserBlockers(){
    return ((state.analysis&&state.analysis.issues)||[]).filter(x=>!x.resolved);
  }
  function renderAnalysis(){
    const p=state.analysis.preview||{};const s=p.stats||{};
    const metrics=[['扫描行',s.scanned_row_count||0],['数据行',s.data_row_count||0],['候选节点',s.candidate_node_count||0],['最大层级',s.max_depth||0],['重复记录',s.duplicate_row_count||0],['Validation',s.issue_count||0]];
    qs('[data-parse-summary]').innerHTML=metrics.map(x=>'<div><span>'+esc(x[0])+'</span><strong>'+esc(x[1])+'</strong></div>').join('');
    qs('[data-preview-node-count]').textContent=(p.nodes||[]).length+' 个候选节点';
    qs('[data-tree-preview]').innerHTML=(p.nodes||[]).map(node=>{
      const depth=Math.max((node.path||[]).length-1,0);
      return '<div class="hti-tree-row"><span class="hti-tree-indent" style="--depth:'+depth+'"></span><span>↳</span><b>'+esc(node.name)+'</b><code>'+esc(node.business_key||'')+'</code></div>';
    }).join('')||'<div class="hti-empty-cell">无候选节点</div>';
    renderRawPreview();
    renderValidation();
  }

  function renderRawPreview(){
    const raw=state.raw||{columns:[],rows:[]};
    qs('[data-raw-sheet]').textContent=raw.sheet_name?raw.sheet_name+' · 表头第 '+raw.header_row+' 行':'—';
    qs('[data-raw-head]').innerHTML='<tr><th>Row</th>'+(raw.columns||[]).map(c=>'<th>'+esc(c.column_name||c.column_key)+'</th>').join('')+'</tr>';
    qs('[data-raw-body]').innerHTML=(raw.rows||[]).map(row=>'<tr data-raw-row="'+row.row_number+'"><td>'+row.row_number+'</td>'+(row.values||[]).map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('');
  }

  function renderValidation(){
    const issues=(state.analysis.issues||[]);
    const list=qs('[data-validation-list]');qs('[data-validation-count]').textContent=issues.length+' 项';
    if(!issues.length){list.innerHTML='<div class="hti-validation-item"><strong>Validation 通过</strong><p>没有需要阻断 Apply 的结构问题。</p></div>'}
    else list.innerHTML=issues.map(issue=>
      '<div class="hti-validation-item" data-validation-row="'+esc(issue.row_number||'')+'">'+
        '<strong>'+esc(issue.issue_type)+'</strong>'+
        '<p>'+esc(issue.suggested_action||'请检查原始 Excel')+'</p>'+
        '<span class="location">'+esc(issue.sheet_name||'—')+' / Row '+esc(issue.row_number||'—')+' / '+esc(issue.column_name||'—')+'</span>'+
        (issue.resolved?'<span class="hti-status ok">已自动处理</span>':'<span class="hti-status bad">阻断</span>')+
      '</div>').join('');
    const blockers=parserBlockers();
    qs('[data-validation-blocker]').hidden=!blockers.length;
    qs('[data-to-diff]').disabled=Boolean(blockers.length);
  }

  function locateRawRow(rowNumber){
    qsa('[data-raw-row]').forEach(x=>x.classList.remove('located'));
    if(!rowNumber)return;
    const row=qs('[data-raw-row="'+CSS.escape(String(rowNumber))+'"]');
    if(row){row.classList.add('located');row.scrollIntoView({behavior:'smooth',block:'center'})}
    else toast('该行不在当前 20 行样例中，请根据 Sheet / Row / Column 在源 Excel 中定位。');
  }

  function changeCounts(){
    const counts={ADD:0,UPDATE:0,RENAME:0,MOVE:0,DEPRECATE:0,NO_CHANGE:0,CONFLICT:0,EXCLUDED:0};
    ((state.analysis&&state.analysis.changes)||[]).forEach(c=>{
      counts[c.change_type]=(counts[c.change_type]||0)+1;
      if(c.decision==='EXCLUDED')counts.EXCLUDED++;
    });return counts;
  }
  function renderChangeSummary(target){
    const c=changeCounts();
    const types=['ADD','UPDATE','RENAME','MOVE','DEPRECATE','NO_CHANGE','CONFLICT'];
    target.innerHTML=types.map(t=>'<span class="hti-change-chip '+(t==='CONFLICT'?'conflict':t==='DEPRECATE'?'deprecate':'')+'">'+t+' '+c[t]+'</span>').join('')+
      '<span class="hti-change-chip">排除项 '+c.EXCLUDED+'</span>';
  }
  function pathText(obj){return obj&&Array.isArray(obj.path)?obj.path.join(' > '):(obj&&obj.name)||'—'}
  function objectDiffText(obj){
    if(!obj)return '—';
    const parts=[];if(obj.name)parts.push('名称：'+obj.name);if(obj.path)parts.push('路径：'+obj.path.join(' > '));if(obj.business_key)parts.push('Business Key：'+obj.business_key);
    return parts.join('\n')||'—';
  }
  function renderDiff(){
    renderChangeSummary(qs('[data-change-summary]'));
    const filter=qs('[data-change-filter]').value;
    const changes=(state.analysis.changes||[]).filter(c=>!filter||c.change_type===filter);
    qs('[data-diff-body]').innerHTML=changes.map(change=>{
      const decision=change.decision||'PENDING';
      let actions='—';
      if(change.change_type==='NO_CHANGE')actions='<span class="hti-status ok">无需处理</span>';
      else if(change.change_type==='CONFLICT')actions=
        '<div class="hti-diff-actions">'+
          '<button class="hti-mini resolve" data-change-action="resolve" data-change-id="'+esc(change.change_id)+'">接受候选</button>'+
          '<button class="hti-mini exclude" data-change-action="exclude" data-change-id="'+esc(change.change_id)+'">排除</button>'+
          '<button class="hti-mini" data-change-action="manual" data-change-id="'+esc(change.change_id)+'">手工修正</button>'+
        '</div>';
      else actions=
        '<div class="hti-diff-actions">'+
          '<button class="hti-mini accept" data-change-action="accept" data-change-id="'+esc(change.change_id)+'">接受</button>'+
          '<button class="hti-mini exclude" data-change-action="exclude" data-change-id="'+esc(change.change_id)+'">排除</button>'+
        '</div>';
      return '<tr data-change-row="'+esc(change.change_id)+'">'+
        '<td><span class="hti-status '+(change.change_type==='CONFLICT'?'bad':change.change_type==='DEPRECATE'?'warn':'')+'">'+esc(change.change_type)+'</span></td>'+
        '<td><b>'+esc((change.after||change.before||{}).name||change.node_id||'—')+'</b><div class="hti-diff-value">'+esc(pathText(change.after||change.before))+'</div></td>'+
        '<td><div class="hti-diff-value">'+esc(objectDiffText(change.before))+'</div></td>'+
        '<td><div class="hti-diff-value">'+esc(objectDiffText(change.after))+'</div></td>'+
        '<td><span class="hti-status '+(decision==='EXCLUDED'?'warn':decision==='PENDING'?'bad':'ok')+'">'+esc(decision)+'</span>'+(change.issue_code?'<div class="hti-diff-value">'+esc(change.issue_code)+'</div>':'')+'</td>'+
        '<td>'+actions+'</td>'+
      '</tr>';
    }).join('')||'<tr><td colspan="6" class="hti-empty-cell">当前筛选无 Change。</td></tr>';
    updateDiffGate();
  }

  function findChange(id){return (state.analysis.changes||[]).find(x=>x.change_id===id)}
  async function decideChange(id,decision,resolvedAfter){
    const change=findChange(id);if(!change)return;
    try{
      const updated=await request(importApi+'/'+encodeURIComponent(state.job.job_id)+'/changes/'+encodeURIComponent(id)+'/decision',jsonOptions('POST',{decision,resolved_after:resolvedAfter||null}));
      Object.assign(change,updated);
      renderDiff();
    }catch(error){toast('变更处理失败：'+errorLabel(error.message))}
  }
  function updateDiffGate(){
    const changes=(state.analysis&&state.analysis.changes)||[];
    const unresolved=changes.filter(c=>c.change_type!=='NO_CHANGE'&&c.decision==='PENDING');
    const conflicts=changes.filter(c=>c.change_type==='CONFLICT'&&c.decision==='PENDING');
    qs('[data-conflict-status]').textContent=conflicts.length?'未处理 Conflict：'+conflicts.length:'Conflict 已清零';
    qs('[data-conflict-status]').className=conflicts.length?'hti-status bad':'hti-status ok';
    qs('[data-to-confirm]').disabled=Boolean(unresolved.length||parserBlockers().length);
  }

  async function acceptAll(){
    const pending=(state.analysis.changes||[]).filter(c=>c.change_type!=='CONFLICT'&&c.change_type!=='NO_CHANGE'&&c.decision==='PENDING');
    if(!pending.length){toast('没有待接受的非冲突变更');return}
    const button=qs('[data-accept-all]');setBusy(button,true);
    try{
      for(const change of pending)await decideChange(change.change_id,'CONFIRMED');
      toast('非冲突变更已全部接受');
    }finally{setBusy(button,false)}
  }

  async function prepareConfirm(){
    const changes=state.analysis.changes||[];
    const unresolved=changes.filter(c=>c.change_type!=='NO_CHANGE'&&c.decision==='PENDING');
    if(unresolved.length){toast('仍有 '+unresolved.length+' 项 Change 未处理');return}
    const version=await request(importApi+'/active-version/'+encodeURIComponent(state.treeType),{headers:roleHeaders}).catch(()=>({active_version:null}));
    state.activeVersions[state.treeType]=version.active_version||null;
    qs('[data-confirm-tree]').textContent=treeLabel(state.treeType);
    qs('[data-confirm-current]').textContent=version.active_version?version.active_version.version_id:'未初始化';
    qs('[data-confirm-conflicts]').textContent='0';
    renderChangeSummary(qs('[data-confirm-summary]'));
    qs('[data-atomic-confirm]').checked=false;qs('[data-apply]').disabled=true;
    showStep(5);
  }

  function appliedCount(){
    return (state.analysis.changes||[]).filter(c=>c.change_type!=='NO_CHANGE'&&c.decision!=='EXCLUDED').length;
  }
  function excludedChanges(){return (state.analysis.changes||[]).filter(c=>c.decision==='EXCLUDED')}

  async function apply(){
    if(state.applying)return;
    if(!qs('[data-atomic-confirm]').checked){toast('请先确认 Atomic Apply 提示');return}
    state.applying=true;const button=qs('[data-apply]');setBusy(button,true);
    const previous=state.activeVersions[state.treeType]&&state.activeVersions[state.treeType].version_id;
    state.excludedSnapshot=excludedChanges().map(x=>({change_id:x.change_id,change_type:x.change_type,path:pathText(x.after||x.before)}));
    try{
      await request(importApi+'/'+encodeURIComponent(state.job.job_id)+'/ready',{method:'POST',headers:roleHeaders});
      const result=await request(importApi+'/'+encodeURIComponent(state.job.job_id)+'/apply',{method:'POST',headers:roleHeaders});
      state.job=result.job;
      const status=result.job.status;
      if(status!=='APPLIED'&&status!=='APPLIED_WITH_EXCLUSIONS')throw new Error(status||'APPLY_FAILED');
      renderResult(status,{
        version:result.active_version&&result.active_version.version_id,
        applied:appliedCount(),excluded:Number((result.job.counts||{}).excluded_count||state.excludedSnapshot.length),failed:0
      });
      await loadHome();
    }catch(error){
      const detail=await request(importApi+'/'+encodeURIComponent(state.job.job_id),{headers:roleHeaders}).catch(()=>null);
      if(detail&&detail.job)state.job=detail.job;
      const active=await request(importApi+'/active-version/'+encodeURIComponent(state.treeType),{headers:roleHeaders}).catch(()=>({active_version:null}));
      const activeId=active.active_version&&active.active_version.version_id;
      renderResult('APPLY_FAILED',{version:activeId||previous||'未初始化',applied:0,excluded:0,failed:1,error:errorLabel((state.job&&state.job.error_code)||error.message),previous});
    }finally{state.applying=false;setBusy(button,false)}
  }

  function renderResult(status,data){
    qs('[data-confirm-state]').hidden=true;
    const box=qs('[data-result]');box.hidden=false;box.className='hti-result';
    let title='',message='',icon='✓';
    if(status==='APPLIED'){
      title='导入成功';message='Change Set 已全部 Atomic Apply 成功。';icon='✓';
    }else if(status==='APPLIED_WITH_EXCLUSIONS'){
      title='已生效，存在排除项';message='用户在 Apply 前已明确排除部分 Candidate，其余被接受的 Change Set 已全部 Atomic Apply 成功。';icon='!';
      box.classList.add('warn');
    }else{
      title='APPLY_FAILED';message='Atomic Apply 整体失败。没有生成新的 ACTIVE Version；上一 ACTIVE Version 保持不变。'+(data.error?' 原因：'+data.error:'');icon='×';
      box.classList.add('bad');
    }
    qs('[data-result-icon]').textContent=icon;qs('[data-result-title]').textContent=title;qs('[data-result-message]').textContent=message;
    qs('[data-result-applied]').textContent=data.applied||0;qs('[data-result-excluded]').textContent=data.excluded||0;qs('[data-result-failed]').textContent=data.failed||0;
    qs('[data-result-version]').textContent=data.version||'—';
    qs('[data-view-exclusions]').hidden=status!=='APPLIED_WITH_EXCLUSIONS';
    qs('[data-retry-apply]').hidden=status!=='APPLY_FAILED';
    qs('[data-return-conflict]').hidden=status!=='APPLY_FAILED';
    qs('[data-view-tree]').hidden=status==='APPLY_FAILED';
  }

  function resetAfterFailure(targetStep){
    qs('[data-result]').hidden=true;
    qs('[data-confirm-state]').hidden=false;
    qs('[data-atomic-confirm]').checked=false;
    qs('[data-apply]').disabled=true;
    showStep(targetStep);
  }

  function showExclusions(){
    const items=state.excludedSnapshot;
    if(!items.length){toast('本次没有排除项');return}
    toast('排除项：'+items.map(x=>x.change_type+' · '+x.path).join('；'));
  }

  root.addEventListener('change',event=>{
    if(event.target.matches('[data-history-tree],[data-history-result]'))renderHistory();
    if(event.target.matches('[data-tree-type]')){
      state.treeType=event.target.value;qs('[data-wizard-title]').textContent='导入 / 更新 '+treeLabel(state.treeType);
    }
    if(event.target.matches('[data-map-role]')){
      const row=event.target.closest('[data-map-row]');const order=qs('[data-map-order]',row);
      order.disabled=event.target.value!=='LEVEL';if(order.disabled)order.value='';
      else if(!order.value){
        const used=qsa('[data-map-row]').filter(r=>qs('[data-map-role]',r).value==='LEVEL').map(r=>Number(qs('[data-map-order]',r).value||0));
        order.value=Math.max(0,...used)+1;
      }
      validateMappingBuilder();
    }
    if(event.target.matches('[data-map-order]'))validateMappingBuilder();
    if(event.target.matches('[data-change-filter]'))renderDiff();
    if(event.target.matches('[data-atomic-confirm]'))qs('[data-apply]').disabled=!event.target.checked;
  });

  root.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.matches('[data-refresh-home]'))return loadHome();
    if(button.matches('[data-open-import]'))return openWizard('CIRCUIT_FEATURE');
    if(button.matches('[data-import-tree]'))return openWizard(button.dataset.importTree);
    if(button.matches('[data-show-versions]'))return showVersions(button.dataset.showVersions);
    if(button.matches('[data-close-versions]')){qs('[data-version-panel]').hidden=true;return}
    if(button.matches('[data-show-history]')){qs('[data-history-tree]').value=button.dataset.showHistory;renderHistory();qs('[data-history-body]').scrollIntoView({behavior:'smooth',block:'center'});return}
    if(button.matches('[data-close-wizard],[data-cancel-import]'))return closeWizard();
    if(button.matches('[data-upload]'))return upload();
    if(button.matches('[data-load-columns]'))return loadColumns();
    if(button.matches('[data-prev-step]'))return showStep(1);
    if(button.matches('[data-run-analysis]'))return analyze();
    if(button.matches('[data-validation-item]'))return;
    if(button.matches('[data-restart-import]'))return openWizard(state.treeType);
    if(button.matches('[data-to-diff]')){renderDiff();showStep(4);return}
    if(button.matches('[data-back-preview]'))return showStep(3);
    if(button.matches('[data-accept-all]'))return acceptAll();
    if(button.matches('[data-to-confirm]'))return prepareConfirm();
    if(button.matches('[data-back-diff]'))return showStep(4);
    if(button.matches('[data-apply]'))return apply();
    if(button.matches('[data-view-exclusions]'))return showExclusions();
    if(button.matches('[data-retry-apply]')){resetAfterFailure(5);toast('请再次确认 Atomic Apply 后重试。');return}
    if(button.matches('[data-return-conflict]')){resetAfterFailure(4);return}
    if(button.matches('[data-view-tree]')){qs('[data-wizard]').hidden=true;const card=qs('[data-tree-card="'+state.treeType+'"]');if(card)card.scrollIntoView({behavior:'smooth',block:'center'});toast('新 ACTIVE Version 已刷新；正式案例消费仍通过 P02 双树导航。');return}
    if(button.matches('[data-view-history]')){qs('[data-wizard]').hidden=true;qs('[data-history-tree]').value=state.treeType;renderHistory();return}
    if(button.matches('[data-finish]')){qs('[data-wizard]').hidden=true;return}
    if(button.matches('[data-change-action]')){
      const action=button.dataset.changeAction,id=button.dataset.changeId,change=findChange(id);
      if(action==='accept')return decideChange(id,'CONFIRMED');
      if(action==='exclude')return decideChange(id,'EXCLUDED');
      if(action==='resolve')return decideChange(id,'RESOLVED',change&&change.after);
      if(action==='manual'){toast('V0.1 手工修正通过修改源 Excel 后重新上传，前端不直接改写 node identity。');return}
    }
  });

  root.addEventListener('click',event=>{
    const item=event.target.closest('[data-validation-row]');if(item)locateRawRow(item.dataset.validationRow);
  });

  qsa('[data-history-tree],[data-history-result]').forEach(x=>x.addEventListener('change',renderHistory));

  loadHome();
})();