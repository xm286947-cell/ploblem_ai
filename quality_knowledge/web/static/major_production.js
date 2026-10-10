(function () {
  'use strict';
  const root = document.querySelector('[data-major-production]');
  if (!root) return;
  const api = (window.P0_MAJOR_API || root.dataset.apiPrefix || '/api/v2').replace(/\/$/, '') + '/major-production';
  const state = { caseId: null, eventId: null, batchId: null };
  // Client storage is only a pointer. Case, Event, Preview and Review authority
  // always comes from the server. Never store files, provider output or evidence.
  const CONTEXT_KEY = 'major-v11-context:' + window.location.pathname;
  const DRAFT_KEY = 'major-v11-draft:' + window.location.pathname;
  const safeId = id => typeof id === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(id);
  function saveContext() {
    try {
      window.localStorage.setItem(CONTEXT_KEY, JSON.stringify({
        caseId: safeId(state.caseId) ? state.caseId : null,
        eventId: safeId(state.eventId) ? state.eventId : null,
        batchId: safeId(state.batchId) ? state.batchId : null
      }));
    } catch (_) { /* private browsing / unavailable storage: manual recovery remains available */ }
  }
  function loadContext() {
    try {
      const v = JSON.parse(window.localStorage.getItem(CONTEXT_KEY) || '{}');
      return {
        caseId: safeId(v.caseId) ? v.caseId : null,
        eventId: safeId(v.eventId) ? v.eventId : null,
        batchId: safeId(v.batchId) ? v.batchId : null
      };
    } catch (_) { return {}; }
  }
  const message = root.querySelector('[data-major-message]');
  const say = (text, error) => {
    message.textContent = text;
    message.className = 'major-message' + (error ? ' error' : '');
  };
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  async function read(response) {
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('服务返回非 JSON 数据（HTTP ' + response.status + '），请查看后台日志。'); }
    if (!response.ok) {
      const code = (data && data.detail) || 'REQUEST_FAILED';
      const taskId = response.headers.get('X-Major-Runtime-Task-ID');
      if (code === 'MAJOR_ANALYSIS_INCOMPLETE') {
        throw new Error('AI 分析未完成，已导入的数据保持不变。' +
          (taskId ? ' Runtime Task：' + taskId + '。' : '') +
          '请按 Task ID 查看后台 MAJOR_ANALYSIS_INCOMPLETE 诊断日志；没有完整结果前不能人工确认。');
      }
      throw new Error(typeof code === 'string' ? code : JSON.stringify(code));
    }
    return data;
  }

  // Upload progress is measured on the browser connection only. Server parsing
  // has no trusted percentage; show elapsed time instead of inventing one.
  function postPreviewWithProgress(formData, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', api + '/excel/preview');
      xhr.upload.addEventListener('progress', event => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.min(100, Math.round(event.loaded * 100 / event.total)));
        }
      });
      xhr.onload = () => {
        let data;
        try { data = JSON.parse(xhr.responseText || '{}'); }
        catch (_) { reject(new Error('预检接口返回非 JSON：HTTP ' + xhr.status)); return; }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(typeof data.detail === 'string' ? data.detail : ('预检失败：HTTP ' + xhr.status)));
          return;
        }
        resolve(data);
      };
      xhr.onerror = () => reject(new Error('网络错误：未收到预检响应，请检查 Windows 服务日志与浏览器网络连接。'));
      xhr.onabort = () => reject(new Error('上传已取消；未执行确认导入。'));
      xhr.send(formData);
    });
  }
  function elapsedIndicator(onTick) {
    const started = Date.now();
    const timer = window.setInterval(() => onTick(Math.floor((Date.now() - started) / 1000)), 3000);
    return () => window.clearInterval(timer);
  }

  async function showExcelProvenance(caseId) {
    const box = root.querySelector('[data-major-provenance]');
    if (!box) return false;
    box.hidden = false;
    box.innerHTML = '<article class="major-candidate"><h3>来源事实核验中…</h3></article>';
    try {
      const data = await read(await fetch(api + '/cases/' +
        encodeURIComponent(caseId) + '/provenance'));
      const facts = Array.isArray(data.structured_source_facts) ? data.structured_source_facts : [];
      const docs = Array.isArray(data.document_evidence) ? data.document_evidence : [];
      const verified = facts.length > 0 && facts.every(item =>
        item.source_type === 'EXCEL' && item.linked === true &&
        item.source_fact_revision_id && item.source_hash && item.source_ref);
      const factHtml = facts.map(item => '<li><strong>Excel Structured Source Fact</strong> · ' +
        esc(item.source_ref) + ' · Revision ' + esc(item.revision_no) +
        ' · Source Fact ID ' + esc(item.source_fact_revision_id) +
        ' · SHA256 ' + esc(item.source_hash) +
        ' · Event Links ' + esc((item.source_link_ids || []).length) +
        '<details><summary>查看保留的完整问题描述</summary><p>' +
        esc(item.original_description) + '</p></details></li>').join('');
      const docHtml = docs.map(item => '<li><strong>Document Evidence</strong> · ' +
        esc(item.filename) + ' · Source Version ' + esc(item.version_id) +
        ' · Parse ' + esc(item.parse_status) + '</li>').join('');
      box.innerHTML = '<article class="major-candidate" data-source-fact-proof>' +
        '<h3>来源事实与证据 · ' + (verified ? 'SOURCE_FACT_PERSISTED' : 'SOURCE_FACT_NOT_VERIFIED') +
        '</h3><p>本区只读展示已保存的 Major 来源记录；Excel 是 Structured Source Fact，' +
        'PDF/DOCX 是独立的 Document Evidence，不互相替代。</p>' +
        '<ul>' + (factHtml || '<li>未发现已持久化并关联的 Excel Source Fact</li>') +
        '</ul><h3>复盘文档 Evidence</h3><ul>' +
        (docHtml || '<li>无文档 Evidence</li>') + '</ul></article>';
      return Boolean(verified);
    } catch (error) {
      box.innerHTML = '<article class="major-candidate" data-source-fact-proof>' +
        '<h3>SOURCE_FACT_NOT_VERIFIED</h3><p>只读来源核验失败：' +
        esc(error.message) + '</p></article>';
      return false;
    }
  }

  const excelForm = root.querySelector('[data-major-excel]');
  const excelPreview = root.querySelector('[data-major-excel-preview]');
  const excelStatus = root.querySelector('[data-major-excel-status]');
  const sourceStatus = root.querySelector('[data-major-source-status]');
  const setExcelStatus = (text, error) => {
    if (excelStatus) {
      excelStatus.textContent = text;
      excelStatus.className = 'major-message' + (error ? ' error' : '');
    }
    say(text, error);
  };
  let previewBusy = false;


  const IMPORT_PAGE_SIZE = 20;
  const PRECHECK_LABELS = {
    ROW_NOT_IMPORTABLE: 'Excel 行缺少必填信息',
    AMBIGUOUS_REPORT_MATCH: '复盘报告匹配到多个文件',
    MULTI_EVENT_DOCUMENT_REVIEW_REQUIRED: '多事件报告未绑定唯一 Event',
    MATCHED_REPORT_NOT_FOUND: '服务器找不到预检时匹配的报告',
    CASE_IDENTITY_CONFLICT: '案例业务身份冲突',
    MAJOR_EXCEL_BATCH_EMPTY: '预检结果为空',
    MAJOR_EXCEL_MAPPING_CHANGED_AFTER_PREVIEW: '预检后字段映射发生变化，请重新预检',
    MAJOR_EXCEL_PREVIEW_CHANGED_AFTER_PREVIEW: '预检快照完整性校验失败，请重新预检',
    MAJOR_EXCEL_GOVERNANCE_MISSING_REPREVIEW_REQUIRED: '缺少预检治理信息，请重新预检',
    MAJOR_EXCEL_BATCH_NOT_CONFIRMABLE: '此批次已完成或失败，不允许重复确认'
  };
  function renderPagedRows(host, items, rowMarkup, title) {
    const entries = Array.isArray(items) ? items : [];
    let page = 0;
    host.innerHTML = '<p data-paged-total></p><div class="table-wrap"><table>' +
      '<thead>' + title + '</thead><tbody data-paged-body></tbody></table></div>' +
      '<div class="major-actions"><button type="button" class="case-button secondary" data-paged-prev>上一页</button>' +
      '<span data-paged-page></span><button type="button" class="case-button secondary" data-paged-next>下一页</button></div>';
    const prev = host.querySelector('[data-paged-prev]');
    const next = host.querySelector('[data-paged-next]');
    const paint = () => {
      const totalPages = Math.max(1, Math.ceil(entries.length / IMPORT_PAGE_SIZE));
      page = Math.max(0, Math.min(page, totalPages - 1));
      const start = page * IMPORT_PAGE_SIZE;
      const current = entries.slice(start, start + IMPORT_PAGE_SIZE);
      host.querySelector('[data-paged-body]').innerHTML = current.map(rowMarkup).join('');
      host.querySelector('[data-paged-total]').textContent =
        '实际预检/保存 ' + entries.length + ' 条；正在查看第 ' +
        (entries.length ? start + 1 : 0) + '–' +
        Math.min(start + IMPORT_PAGE_SIZE, entries.length) + ' 条。';
      host.querySelector('[data-paged-page]').textContent = '第 ' + (page + 1) + ' / ' + totalPages + ' 页';
      prev.disabled = page === 0;
      next.disabled = page >= totalPages - 1;
    };
    prev.addEventListener('click', () => { page -= 1; paint(); });
    next.addEventListener('click', () => { page += 1; paint(); });
    paint();
  }

  function renderBatchTable(host, rows, errors) {
    const errorMap = new Map();
    (errors || []).forEach(error => {
      if (error.row === null || error.row === undefined) return;
      const k = String(error.row);
      errorMap.set(k, [...(errorMap.get(k) || []), error.error]);
    });
    renderPagedRows(host, rows, row => {
      const match = row.report_match || {};
      const resolution = row.event_resolution || {};
      const problems = (errorMap.get(String(row.excel_row)) || []).map(code =>
        PRECHECK_LABELS[code] || code).join('；');
      return '<tr><td>' + esc(row.excel_row) + '</td><td>' +
        esc((row.itrs || []).join(' / ')) + '</td><td>' + esc(row.title) +
        '</td><td>' + esc(row.completeness && row.completeness.importable ? 'IMPORTABLE' : 'BLOCKED') +
        '</td><td>' + esc(match.report_filename || '') + '</td><td>' +
        esc(match.match_status || match.match_type || 'NOT_FOUND') +
        (resolution.standard_itr ? ' · Event ' + esc(resolution.standard_itr) : '') +
        '</td><td>' + esc(problems || '—') + '</td></tr>';
    }, '<tr><th>Excel 行</th><th>ITR</th><th>标题</th><th>导入条件</th>' +
      '<th>复盘文件</th><th>匹配/Event</th><th>阻断原因</th></tr>');
  }

  function renderCommitResults(host, ids, importResult) {
    const uniqueIds = [...new Set((ids || []).filter(safeId))];
    const imported = Number(importResult && importResult.imported || 0);
    host.innerHTML = '<article class="major-candidate"><h3>批次导入结果（已保存）</h3>' +
      '<p>实际成功导入 ' + imported + ' 行；关联 ' + uniqueIds.length +
      ' 个唯一 Case。相同 Case 可以关联多行/多个 Event，不应把行数当作 Case 数。</p>' +
      '<div data-batch-case-list></div></article>';
    const area = host.querySelector('[data-batch-case-list]');
    renderPagedRows(area, uniqueIds, caseId =>
      '<tr><td>' + esc(caseId) + '</td><td>' +
      '<button type="button" class="case-button secondary" data-open-import-case="' +
      esc(caseId) + '">选择此 Case 继续 AI 分析</button></td></tr>',
    '<tr><th>Case ID</th><th>操作</th></tr>');
    area.addEventListener('click', event => {
      const button = event.target.closest('[data-open-import-case]');
      if (!button || !area.contains(button)) return;
      const id = button.dataset.openImportCase;
      restoreCase(id, null).then(() => {
        resumed('已打开 Case ' + id + '；如有多个 Event，请先选择 Event。');
        root.querySelector('[data-major-workflow]').scrollIntoView({block: 'start'});
      }).catch(error => resumed('打开案例失败：' + error.message, true));
    });
  }

  // A refresh used to discard the entire production state. Hydrate from
  // authoritative server snapshots instead of replaying a POST or an AI call.
  const resumeStatus = document.createElement('p');
  resumeStatus.className = 'major-message';
  resumeStatus.setAttribute('role', 'status');
  const recoveryPanel = document.createElement('section');
  recoveryPanel.className = 'case-card';
  recoveryPanel.innerHTML =
    '<h2>继续上次工作</h2><p>刷新后从服务端恢复已保存的批次、案例和人工审核。' +
    '文件选择不能由浏览器自动恢复；未上传的文件需重新选择。</p>' +
    '<form data-major-resume class="major-form">' +
    '<label>Case ID <input name="case_id" placeholder="KCASE-..."></label>' +
    '<label>Batch ID <input name="batch_id" placeholder="MIMP-..."></label>' +
    '<button type="submit" class="case-button secondary">恢复已有记录（不重新导入）</button></form>' +
    '<button type="button" class="case-button secondary" data-major-recent>查看最近已保存操作</button>' +
    '<div data-major-recent-list class="major-candidates"></div>';
  recoveryPanel.appendChild(resumeStatus);
  excelForm.closest('section').before(recoveryPanel);
  const resumeForm = recoveryPanel.querySelector('[data-major-resume]');
  const resumed = (message, isError) => {
    resumeStatus.textContent = message;
    resumeStatus.className = 'major-message' + (isError ? ' error' : '');
  };

  const draftForms = [excelForm, root.querySelector('[data-major-intake]')];
  let unsavedText = false;
  function saveDraft() {
    const draft = {};
    draftForms.forEach((form, i) => {
      if (!form) return;
      draft['form' + i] = {};
      form.querySelectorAll('input:not([type=file])').forEach(input => {
        if (input.name) draft['form' + i][input.name] = input.value;
      });
    });
    try { window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft)); } catch (_) {}
  }
  function restoreDraft() {
    let draft = {};
    try { draft = JSON.parse(window.sessionStorage.getItem(DRAFT_KEY) || '{}'); } catch (_) {}
    draftForms.forEach((form, i) => {
      if (!form) return;
      const fields = draft['form' + i] || {};
      form.querySelectorAll('input:not([type=file])').forEach(input => {
        if (input.name && typeof fields[input.name] === 'string') input.value = fields[input.name];
      });
      form.addEventListener('input', () => { unsavedText = true; saveDraft(); });
      form.addEventListener('change', () => { unsavedText = true; saveDraft(); });
    });
  }
  const hasSelectedFiles = () => draftForms.some(form => form && [...form.querySelectorAll('input[type=file]')]
    .some(input => input.files && input.files.length));
  window.addEventListener('beforeunload', event => {
    if (!hasSelectedFiles() && !unsavedText) return;
    event.preventDefault();
    event.returnValue = '';
  });
  function clearSelectedFiles(form) {
    form.querySelectorAll('input[type=file]').forEach(input => { input.value = ''; });
  }

  async function restoreCase(caseId, requestedEventId) {
    if (!safeId(caseId)) throw new Error('CASE_ID_INVALID');
    const detail = await read(await fetch(api + '/cases/' + encodeURIComponent(caseId)));
    const events = Array.isArray(detail.events) ? detail.events : [];
    const selected = events.some(e => e.event_id === requestedEventId) ? requestedEventId :
      (events.length === 1 ? events[0].event_id : null);
    state.caseId = caseId;
    state.eventId = selected;
    saveContext();
    const workflow = root.querySelector('[data-major-workflow]');
    workflow.hidden = false;
    root.querySelector('[data-major-identity]').textContent = 'Case ' + caseId +
      ' · ' + events.length + ' Event(s) · 数据来自已保存的服务端记录';
    const box = root.querySelector('[data-major-candidates]');
    box.innerHTML = '';
    if (events.length > 1) {
      const selector = document.createElement('label');
      selector.textContent = '当前 Event（多事件必须明确选择）：';
      const input = document.createElement('select');
      input.innerHTML = '<option value="">请选择 Event</option>' +
        events.map(e => '<option value="' + esc(e.event_id) + '">' +
          esc(e.standard_itr || e.event_title || e.event_id) + '</option>').join('');
      input.value = selected || '';
      input.addEventListener('change', () => {
        restoreCase(caseId, input.value || null).catch(error => resumed('恢复 Event 失败：' + error.message, true));
      });
      selector.appendChild(input);
      box.appendChild(selector);
    }
    const publishButton = root.querySelector('[data-major-publish]');
    publishButton.disabled = true;
    if (events.length > 1 && !selected) {
      root.querySelector('[data-major-state]').textContent = 'EVENT_SELECTION_REQUIRED';
      resumed('已恢复案例；请选择需要继续审核的 Event，不会自动混用其他 Event 的结果。');
      return detail;
    }
    const entries = (detail.entries || []).filter(e => e.event_id === selected ||
      e.scope_kind === 'CASE_SHARED' || (events.length === 1 && !e.event_id));
    const live = entries.filter(e => ['PENDING', 'CONFIRMED', 'CORRECTED'].includes(e.status) &&
      (e.origin === 'AI' || e.origin === 'HUMAN'));
    live.forEach(item => {
      const article = document.createElement('article');
      article.className = 'major-candidate';
      const reviewable = item.status === 'PENDING' && item.origin === 'AI';
      article.innerHTML = '<h3>' + esc(item.entry_type) + ' · ' + esc(item.status) +
        '</h3><p>' + esc(item.content) + '</p>' +
        (reviewable ? '<button class="case-button secondary" data-entry="' +
        esc(item.entry_id) + '">人工确认并创建修订</button>' : '<p>已保存的审核状态</p>');
      const button = article.querySelector('[data-entry]');
      if (button) button.addEventListener('click', () => confirm(button));
      box.appendChild(article);
    });
    if (!live.length) {
      box.insertAdjacentHTML('beforeend', '<p>已恢复来源与案例；尚无可继续审核的 AI 候选。' +
        '需要时可手动运行 AI 分析。</p>');
    }
    const required = ['ISSUE_FACT', 'ROOT_CAUSE', 'ACTION', 'VERIFICATION'];
    const fullyReviewed = !!selected && required.every(type =>
      entries.some(e => e.entry_type === type && e.event_id === selected &&
        (e.status === 'CONFIRMED' || e.status === 'CORRECTED')));
    const pending = live.some(e => e.status === 'PENDING');
    publishButton.disabled = !fullyReviewed || pending;
    root.querySelector('[data-major-state]').textContent = pending ? 'REVIEW_REQUIRED' :
      (fullyReviewed ? 'READY_TO_PUBLISH' : 'INTAKED');
    if ((detail.source_links || []).some(link => link.source_type === 'MAJOR_EXCEL_SOURCE_FACT')) {
      await showExcelProvenance(caseId);
    }
    resumed('已从服务端恢复 Case ' + caseId + '。已确认内容不会因刷新而重新生成。');
    return detail;
  }

  async function restoreBatch(batchId) {
    if (!safeId(batchId)) throw new Error('BATCH_ID_INVALID');
    const [batch, preflight] = await Promise.all([
      read(await fetch(api + '/excel/batches/' + encodeURIComponent(batchId))),
      read(await fetch(api + '/excel/batches/' + encodeURIComponent(batchId) + '/preflight'))
    ]);
    const preview = batch.preview || {};
    const rows = Array.isArray(preview.rows) ? preview.rows : [];
    state.batchId = batchId;
    saveContext();
    excelPreview.hidden = false;
    excelPreview.innerHTML = '<article class="major-candidate"><h3>已保存的 Excel 批次</h3>' +
      '<p>Batch ' + esc(batchId) + ' · 状态 ' + esc(batch.status) + ' · 来源 ' +
      esc(preview.source_file || batch.source_file || '') + '</p>' +
      '<p>Excel 预检 ' + rows.length + ' 行；可导入标记 ' +
      esc(preview.importable || 0) + ' 行。最终导入结果以服务端 Commit 为准。</p>' +
      '<details><summary>本批次 Mapping 版本</summary><p>' +
      esc(preview.mapping_version || 'UNKNOWN') + '</p></details>' +
      '<div data-major-batch-table></div><div data-major-blocking-reasons></div>' +
      '<div data-major-commit-results></div>' +
      '<button type="button" class="case-button primary" data-major-recovered-confirm>确认导入当前批次</button>' +
      '</article>';
    renderBatchTable(excelPreview.querySelector('[data-major-batch-table]'), rows, preflight.errors);
    const problems = preflight.errors || [];
    const blockers = excelPreview.querySelector('[data-major-blocking-reasons]');
    if (problems.length) {
      blockers.innerHTML = '<h3>确认导入被阻止：' + problems.length +
        ' 项问题（以下为全部行级原因）</h3><ul>' +
        problems.map(item => '<li>' +
          (item.row === null || item.row === undefined ? '批次：' : 'Excel 第 ' + esc(item.row) + ' 行：') +
          esc(PRECHECK_LABELS[item.error] || item.error) + '</li>').join('') +
        '</ul><p>请核对并修正 Excel 或报告匹配后重新预检。不能跳过错误行或修改已冻结的预检快照。</p>';
    } else {
      blockers.textContent = batch.status === 'PREVIEW'
        ? '全部 ' + rows.length + ' 行已通过服务端导入前检查，可继续确认。'
        : '该批次已不处于可确认的 PREVIEW 状态。';
    }
    const confirmButton = excelPreview.querySelector('[data-major-recovered-confirm]');
    if (!preflight.confirmable) {
      confirmButton.textContent = '查看无法确认的原因';
      confirmButton.addEventListener('click', () => {
        setExcelStatus('当前批次不可确认：' + problems.length + ' 项阻断。请查看预检表的“阻断原因”和下方详细列表。', true);
        blockers.scrollIntoView({block: 'center'});
      });
    } else {
      confirmButton.addEventListener('click', async () => {
        confirmButton.disabled = true;
        const stopCommitClock = elapsedIndicator(seconds => setExcelStatus(
          '后台正在导入批次… 已等待 ' + seconds + ' 秒；切勿重复提交。'));
        try {
          setExcelStatus('已发起确认导入，正在等待服务器返回…');
          const form = new FormData();
          form.append('batch_id', batchId);
          await read(await fetch(api + '/excel/confirm', {method: 'POST', body: form}));
          stopCommitClock();
          await restoreBatch(batchId);
          setExcelStatus('批次已提交。请在导入结果列表中选择要继续分析的 Case。');
        } catch (error) {
          stopCommitClock();
          // A network error after a successful commit is possible; never
          // blindly replay a non-idempotent action from this button.
          confirmButton.disabled = false;
          setExcelStatus('确认导入未核实完成：' + error.message +
            '。请通过恢复批次核对服务端状态，避免重复操作。', true);
        }
      });
    }
    const result = batch.result || {};
    const ids = Array.isArray(result.case_ids) ? result.case_ids : [];
    if (batch.status === 'COMPLETED' || batch.status === 'PARTIAL') {
      renderCommitResults(excelPreview.querySelector('[data-major-commit-results]'), ids, result);
      setExcelStatus('已完成批次：导入 ' + (result.imported || 0) +
        ' 行，关联 ' + [...new Set(ids)].length + ' 个 Case；可逐个选择并运行 AI 分析。');
      if (ids.length === 1 && !state.caseId) await restoreCase(ids[0], null);
    } else if (!preflight.confirmable) {
      setExcelStatus('预检未通过，' + problems.length + ' 项阻断原因已列出；不允许静默导入。', true);
    } else {
      setExcelStatus('预检完成，已展示全部 ' + rows.length + ' 行（每页 20 行）；可点击确认导入。');
    }
    resumed('已从服务端恢复 Batch ' + batchId + '（' + batch.status + '）。');
    return batch;
  }

  async function loadRecent() {
    const box = recoveryPanel.querySelector('[data-major-recent-list]');
    box.textContent = '正在读取最近服务端记录…';
    try {
      const data = await read(await fetch(api + '/recent'));
      const batches = (data.batches || []).map(item =>
        '<li><button class="case-button secondary" type="button" data-resume-batch="' +
        esc(item.batch_id) + '">批次 ' + esc(item.batch_id) + ' · ' +
        esc(item.status) + ' · ' + esc(item.source_file) + ' · ' +
        esc(item.created_at) + '</button></li>').join('');
      const cases = (data.cases || []).map(item =>
        '<li><button class="case-button secondary" type="button" data-resume-case="' +
        esc(item.case_id) + '">案例 ' + esc(item.title) + ' · ' +
        esc(item.status) + ' · ' + esc(item.case_id) + '</button></li>').join('');
      box.innerHTML = '<article class="major-candidate"><h3>服务器最近保存的批次（最多 20 条）</h3>' +
        '<ul>' + (batches || '<li>暂无批次</li>') + '</ul>' +
        '<h3>最近保存的案例（最多 20 条）</h3><ul>' +
        (cases || '<li>暂无案例</li>') + '</ul></article>';
      box.querySelectorAll('[data-resume-batch]').forEach(button =>
        button.addEventListener('click', () =>
          restoreBatch(button.dataset.resumeBatch).catch(error =>
            resumed('恢复批次失败：' + error.message, true))));
      box.querySelectorAll('[data-resume-case]').forEach(button =>
        button.addEventListener('click', () =>
          restoreCase(button.dataset.resumeCase, null).catch(error =>
            resumed('恢复案例失败：' + error.message, true))));
    } catch (error) {
      box.textContent = '查询最近记录失败：' + error.message;
    }
  }
  recoveryPanel.querySelector('[data-major-recent]').addEventListener('click', loadRecent);

  resumeForm.addEventListener('submit', async event => {
    event.preventDefault();
    const c = resumeForm.elements.namedItem('case_id').value.trim();
    const b = resumeForm.elements.namedItem('batch_id').value.trim();
    if (!c && !b) { resumed('请输入 Case ID 或 Batch ID。', true); return; }
    try {
      if (c) await restoreCase(c, null);
      if (b) await restoreBatch(b);
    } catch (error) { resumed('恢复失败：' + error.message, true); }
  });

  async function resumeOnLoad() {
    restoreDraft();
    const previous = loadContext();
    resumeForm.elements.namedItem('case_id').value = previous.caseId || '';
    resumeForm.elements.namedItem('batch_id').value = previous.batchId || '';
    if (!previous.caseId && !previous.batchId) {
      resumed('尚无本浏览器保存的工作编号，正在查找服务器最近记录。');
      await loadRecent();
      return;
    }
    try {
      if (previous.caseId) await restoreCase(previous.caseId, previous.eventId);
      if (previous.batchId) await restoreBatch(previous.batchId);
    } catch (error) {
      resumed('上次工作自动恢复失败：' + error.message +
        '。可以检查服务端记录，或输入其他 Case/Batch ID 手动恢复。', true);
    }
  }

  if (excelForm) {
    excelForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (previewBusy) return;
      previewBusy = true;
      const previewButton = excelForm.querySelector('button[type="submit"]');
      previewButton.disabled = true;
      excelPreview.hidden = true;
      const formData = new FormData(event.currentTarget);
      let uploaded = false;
      let uploadPercent = 0;
      const stopClock = elapsedIndicator(seconds => {
        setExcelStatus(uploaded
          ? '文件已发送，后台正在解析 Excel、检查 Mapping 和复盘匹配… 已等待 ' + seconds + ' 秒，请勿重复点击。'
          : '正在上传文件… ' + uploadPercent + '% · 已等待 ' + seconds + ' 秒');
      });
      try {
        setExcelStatus('正在上传 Excel 和复盘材料…');
        const data = await postPreviewWithProgress(formData, percent => {
          uploadPercent = percent;
          if (percent >= 100) uploaded = true;
          setExcelStatus(percent >= 100
            ? '上传已完成，后台正在进行预检，处理时长取决于文件大小…'
            : '正在上传文件：' + percent + '%');
        });
        state.batchId = data.batch_id;
        saveContext();
        clearSelectedFiles(excelForm);
        excelPreview.hidden = false;
        // Render the same server-authoritative 69-row preview as refresh.
        // Do not create a second UI contract with an invisible 20-row slice.
        await restoreBatch(data.batch_id);

      } catch (error) {
        setExcelStatus('批量预检失败：' + error.message, true);
      } finally {
        stopClock();
        previewBusy = false;
        previewButton.disabled = false;
      }
    });
  }

  root.querySelector('[data-major-intake]').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      say('正在导入单份 Major Source，此操作不会自动运行 AI 分析…');
      if (sourceStatus) sourceStatus.textContent = '正在导入、解析并建立来源证据…';
      const data = await read(await fetch(api + '/sources', { method: 'POST', body: new FormData(event.currentTarget) }));
      state.caseId = data.case.case_id;
      state.eventId = data.event.event_id;
      saveContext();
      clearSelectedFiles(event.currentTarget);
      unsavedText = false;
      root.querySelector('[data-major-workflow]').hidden = false;
      root.querySelector('[data-major-identity]').textContent = 'ITR ' + data.event.standard_itr +
        ' · Source ' + data.document.original_filename + ' · Version ' + data.document.version_no;
      if (sourceStatus) sourceStatus.textContent = '单份来源已导入成功，下一步请手动运行 AI 分析。';
      say('Major Source 已入库，已建立 Event 与证据版本。');
    } catch (error) {
      if (sourceStatus) sourceStatus.textContent = '单份来源导入失败：' + error.message;
      say(error.message, true);
    }
  });

  root.querySelector('[data-major-analyze]').addEventListener('click', async () => {
    if (!state.caseId) return;
    try {
      say('正在调用已配置的 AI Provider…');
      const suffix = state.eventId ? '?event_id=' + encodeURIComponent(state.eventId) : '';
      const data = await read(await fetch(api + '/cases/' + encodeURIComponent(state.caseId) + '/analysis' + suffix, { method: 'POST' }));
      const box = root.querySelector('[data-major-candidates]');
      box.innerHTML = data.candidates.map(item => '<article class="major-candidate"><h3>' + esc(item.entry_type) +
        ' · PENDING</h3><p>' + esc(item.content) + '</p><button class="case-button secondary" data-entry="' +
        esc(item.entry_id) + '">人工确认并创建修订</button></article>').join('');
      box.querySelectorAll('[data-entry]').forEach(button => button.addEventListener('click', () => confirm(button)));
      root.querySelector('[data-major-state]').textContent = 'REVIEW_REQUIRED';
      say('AI 候选已生成，必须逐条人工确认。');
    } catch (error) { say(error.message, true); }
  });

  async function confirm(button) {
    try {
      const content = button.closest('.major-candidate').querySelector('p').textContent;
      const data = await read(await fetch(api + '/entries/' + encodeURIComponent(button.dataset.entry) + '/confirm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewer: 'web-reviewer', content, reason: 'Web human review' })
      }));
      button.closest('.major-candidate').querySelector('h3').textContent = data.entry_type +
        ' · CONFIRMED · revision ' + data.revision_no;
      button.disabled = true;
      const all = [...root.querySelectorAll('[data-entry]')].every(item => item.disabled);
      if (all) {
        root.querySelector('[data-major-publish]').disabled = false;
        root.querySelector('[data-major-state]').textContent = 'READY_TO_PUBLISH';
      }
      say('已创建人工确认修订。');
    } catch (error) { say(error.message, true); }
  }

  root.querySelector('[data-major-publish]').addEventListener('click', async () => {
    try {
      const data = await read(await fetch(api + '/events/' + encodeURIComponent(state.eventId) + '/publish', { method: 'POST' }));
      root.querySelector('[data-major-state]').textContent = data.publication_status + ' · ' + data.status;
      say('已创建正式 Historical Case：' + data.case_id + '。可在案例库检索和复用。');
    } catch (error) { say(error.message, true); }
  });
  resumeOnLoad();
})();
