(function () {
  'use strict';

  const root = document.querySelector('[data-major-production]');
  if (!root) return;

  const api = (window.P0_MAJOR_API || root.dataset.apiPrefix || '/api/v2').replace(/\/$/, '') + '/major-production';
  const state = { caseId: null, eventId: null, analysisMode: null };
  const message = root.querySelector('[data-major-message]');
  const excelPreview = root.querySelector('[data-major-excel-preview]');
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const say = (text, error) => {
    message.textContent = text;
    message.className = 'major-message' + (error ? ' error' : '');
  };
  async function read(response) {
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'REQUEST_FAILED');
    return data;
  }

  function renderPreview(data) {
    const summary = data.match_summary || {};
    const fields = Object.entries((data.mapping || {}).fields || {})
      .map(([key, value]) => '<li><strong>' + esc(key) + '</strong> ← ' + esc(value) + '</li>').join('');
    const missingColumns = (data.mapping && data.mapping.missing_columns || [])
      .map(value => '<span class="major-chip">' + esc(value) + '</span>').join('') || '无';
    const rows = (data.rows || []).map(row => {
      const missing = row.completeness && row.completeness.missing_features || [];
      const reasons = row.blocking_reasons || [];
      const match = row.report_match || {};
      const matchLabel = [match.match_type, match.parse_status, match.matched_report_path && match.matched_report_path.split(/[\\/]/).pop()]
        .filter(Boolean).join(' · ');
      const candidateNames = (match.candidate_paths || []).map(path => String(path).split(/[\\/]/).pop()).join('、');
      return '<tr><td>' + esc(row.excel_row) + '</td><td>' + esc(row.igr || '') + '</td>' +
        '<td>' + esc((row.itrs || []).join(' / ')) + '</td><td>' + esc(row.title || '') + '</td>' +
        '<td>' + esc(row.completeness && row.completeness.importable ? '可导入' : '阻断') + '</td>' +
        '<td>' + esc(missing.join('、') || '无') + '</td><td>' + esc(matchLabel || '未提供复盘材料') +
        (candidateNames ? '<small>候选：' + esc(candidateNames) + '</small>' : '') + '</td>' +
        '<td>' + esc(reasons.join('；') || '无') + '</td></tr>';
    }).join('');
    const blocked = Number(data.blocked || 0);
    excelPreview.hidden = false;
    excelPreview.innerHTML =
      '<article class="major-candidate"><h3>Preview · ' + esc(data.source_file) + '</h3>' +
      '<p>共 ' + esc(data.total) + ' 行；可导入 ' + esc(data.importable) + ' 行；阻断 ' + esc(blocked) +
      ' 行。Template ' + esc(data.template_version) + ' (' + esc(data.template_status) +
      ') · Mapping ' + esc(data.mapping && data.mapping.version || data.mapping_version) + '</p>' +
      '<p>Report Match：匹配 ' + esc(summary.matched_count || 0) + ' · 未匹配 ' +
      esc(summary.unmatched_count || 0) + ' · 歧义 ' + esc(summary.ambiguous_count || 0) +
      '；缺失列：' + missingColumns + '</p>' +
      '<details><summary>查看字段 Mapping</summary><ul>' + fields + '</ul></details>' +
      '<div class="table-wrap"><table><thead><tr><th>Excel 行</th><th>IGR</th><th>ITR</th><th>标题</th>' +
      '<th>完整性</th><th>缺失特征</th><th>Report Match</th><th>阻断原因</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>' +
      (blocked
        ? '<p class="major-blocked">批次有阻断项，当前不能确认。请修正数据或复盘材料后重新预览。</p>'
        : '<p>确认前将再次核对 Mapping 版本、Preview 内容、上传文件和 Case 身份。</p>' +
          '<button class="case-button primary" data-major-excel-confirm>确认导入</button>') +
      '</article>';
    const confirmButton = excelPreview.querySelector('[data-major-excel-confirm]');
    if (confirmButton) confirmButton.addEventListener('click', () => confirmExcel(data));
  }

  function renderBatchResult(result, batchId) {
    const caseIds = result.case_ids || [];
    const cases = caseIds.map(caseId =>
      '<li><code>' + esc(caseId) + '</code> <button class="case-button secondary" type="button" data-open-major-case="' +
      esc(caseId) + '">查看 Case / Event / Source Fact</button></li>').join('');
    const rows = [
      ['导入', result.imported], ['拒绝', result.rejected], ['失败', result.failed],
      ['新建 Case', result.created_cases], ['复用 Case', result.reused_cases],
      ['新增 Source Fact revision', result.source_fact_revisions], ['Event', result.events],
      ['复盘文档', result.documents], ['Atomic Rollback', result.atomic_rollback ? '已执行' : '否']
    ].map(([label, value]) => '<div><label>' + esc(label) + '</label><strong>' + esc(value == null ? 0 : value) + '</strong></div>').join('');
    excelPreview.innerHTML =
      '<article class="major-candidate"><h3>Batch Result · ' + esc(batchId) + '</h3>' +
      '<div class="major-result-grid">' + rows + '</div>' +
      (result.errors && result.errors.length
        ? '<details open><summary>错误 / 阻断详情</summary><pre>' + esc(JSON.stringify(result.errors, null, 2)) + '</pre></details>' : '') +
      '<h4>全部导入 Case</h4><ul class="major-case-list">' + (cases || '<li>没有导入 Case</li>') + '</ul>' +
      '<div data-major-case-details></div></article>';
    excelPreview.querySelectorAll('[data-open-major-case]').forEach(button => {
      button.addEventListener('click', () => openMajorCase(button.dataset.openMajorCase));
    });
    say(result.atomic_rollback ? '导入失败，系统已执行原子回滚。' : 'Excel 批次处理完成。');
  }

  async function openMajorCase(caseId) {
    const panel = excelPreview.querySelector('[data-major-case-details]');
    try {
      say('正在读取 Case、Event 和 Source Fact…');
      const detail = await read(await fetch(api + '/cases/' + encodeURIComponent(caseId)));
      const events = (detail.events || []).map(event =>
        '<li><strong>' + esc(event.event_title || event.internal_event_key || event.event_id) + '</strong> · ' +
        esc(event.standard_itr || '无 ITR') + ' · ' + esc(event.event_id) + '</li>').join('');
      const facts = (detail.source_fact_revisions || []).map(fact => {
        const normalized = fact.normalized || {};
        const fields = Object.entries(normalized).filter(([, value]) => value != null && value !== '')
          .map(([key, value]) => '<li><strong>' + esc(key) + '</strong>：' + esc(Array.isArray(value) ? value.join('、') : value) + '</li>').join('');
        return '<article><h5>EXCEL · Revision ' + esc(fact.revision_no) + '</h5><p>' +
          esc(fact.source_ref) + ' · ' + esc(fact.source_hash) + '</p><details><summary>查看规范化字段</summary><ul>' +
          fields + '</ul></details></article>';
      }).join('');
      panel.innerHTML = '<section class="major-case-detail"><h4>' + esc(detail.title) + '</h4>' +
        '<p>Case ID：' + esc(detail.case_id) + ' · 状态：' + esc(detail.status) + '</p>' +
        '<h5>Events</h5><ul>' + (events || '<li>无 Event</li>') + '</ul>' +
        '<h5>EXCEL Source Fact Revisions</h5>' + (facts || '<p>无 Source Fact</p>') + '</section>';
      say('Case、Event 与 Source Fact 已加载。');
    } catch (error) {
      say(error.message, true);
    }
  }

  async function confirmExcel(data) {
    try {
      say('正在确认并导入 Excel…');
      const form = new FormData();
      form.append('batch_id', data.batch_id);
      const response = await fetch(api + '/excel/confirm', { method: 'POST', body: form });
      const committed = await read(response);
      renderBatchResult(committed.result || {}, data.batch_id);
    } catch (error) {
      say(error.message, true);
    }
  }

  function renderTypedCandidates(candidates) {
    return candidates.map(item => {
      const metadata = item.analysis_metadata || {};
      const reviewRequired = metadata.review_status === 'REVIEW_REQUIRED';
      const sourceValues = (metadata.source_values || []).map(source => {
        const evidence = (source.evidence_refs || []).map(ref =>
          '<li><small>' + esc(ref.locator || ref.fragment_id || ref.source_fact_revision_id || '') +
          '</small><blockquote>' + esc(ref.excerpt || '') + '</blockquote></li>'
        ).join('');
        return '<div class="major-source-value"><strong>' + esc(source.source_type || 'SOURCE') +
          '</strong><p>' + esc(source.value || '') + '</p><ul>' + evidence + '</ul></div>';
      }).join('');
      const evidence = (item.evidence || []).map(ref =>
        '<li><small>' + esc(ref.locator || ref.fragment_id || ref.source_link_id || '') +
        '</small><blockquote>' + esc(ref.excerpt || '') + '</blockquote></li>'
      ).join('');
      const heading = esc(item.entry_type) + ' · ' + esc(item.status) +
        (metadata.source_status ? ' · ' + esc(metadata.source_status) : '');
      const reviewRequiredAttribute = metadata.review_status === 'REVIEW_REQUIRED' ? 'true' : 'false';
      const standardization = metadata.standardization || {};
      const standardizationPanel = standardization.status === 'PROPOSED'
        ? '<details class="major-standardization"><summary>Unified Runtime 标准化建议</summary><p data-standardized-content>' +
          esc(standardization.content || '') + '</p><small>仅为建议；来源正文与原 Evidence 保持不变。</small>' +
          '<button type="button" class="case-button secondary" data-use-standardization>填入更正框</button></details>'
        : '';
      const editor = item.status === 'MISSING'
        ? '<p class="major-blocked">MISSING：当前来源没有该语义，不生成内容。</p>'
        : '<label>人工确认后的内容<textarea data-candidate-content ' +
          (reviewRequired ? 'placeholder="请比较全部来源，填写人工确认的结论"' : '') + '>' +
          esc(reviewRequired ? '' : item.content || '') + '</textarea></label>' +
          '<label>审核理由<input data-review-reason placeholder="说明确认或更正依据" ' +
          (reviewRequired ? 'required' : '') + '></label>' +
          '<button class="case-button primary" data-review-action="CONFIRM" data-entry="' + esc(item.entry_id) + '">人工确认</button>' +
          '<button class="case-button secondary" data-review-action="CORRECT" data-entry="' + esc(item.entry_id) + '">更正并确认</button>';
      return '<article class="major-candidate" data-typed-candidate data-review-required="' + reviewRequiredAttribute + '"><h3>' + heading + '</h3>' +
        (reviewRequired ? '<p class="major-blocked">需人工比较来源并作出明确决定；系统不会择边。</p>' : '') +
        (sourceValues ? '<details open><summary>来源值与对应证据</summary>' + sourceValues + '</details>' : '') +
        '<details><summary>Repository Evidence</summary><ul>' + evidence + '</ul></details>' + standardizationPanel + editor + '</article>';
    }).join('');
  }

  const excelForm = root.querySelector('[data-major-excel]');
  if (excelForm) {
    excelForm.addEventListener('submit', async event => {
      event.preventDefault();
      try {
        say('正在预览 Excel 与复盘材料…');
        const data = await read(await fetch(api + '/excel/preview', {
          method: 'POST',
          body: new FormData(event.currentTarget)
        }));
        renderPreview(data);
        say('预览完成。请核对字段映射、缺失项和逐行结果。');
      } catch (error) {
        say(error.message, true);
      }
    });
  }

  root.querySelector('[data-major-intake]').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      say('正在导入 Major Source…');
      const data = await read(await fetch(api + '/sources', { method: 'POST', body: new FormData(event.currentTarget) }));
      state.caseId = data.case.case_id;
      state.eventId = data.event.event_id;
      root.querySelector('[data-major-workflow]').hidden = false;
      root.querySelector('[data-major-identity]').textContent =
        'ITR ' + data.event.standard_itr + ' · Source ' + data.document.original_filename + ' · Version ' + data.document.version_no;
      say('Major Source 已入库，已建立 Event 与证据版本。');
    } catch (error) {
      say(error.message, true);
    }
  });

  root.querySelector('[data-major-analyze]').addEventListener('click', async () => {
    if (!state.caseId) return;
    try {
      say('正在读取 Source 并生成语义 Review 候选…');
      const data = await read(await fetch(api + '/cases/' + encodeURIComponent(state.caseId) + '/analysis', { method: 'POST' }));
      state.analysisMode = data.mode || 'LEGACY_AI';
      const box = root.querySelector('[data-major-candidates]');
      if (state.analysisMode === 'SOURCE_FUSION') {
        box.innerHTML = renderTypedCandidates(data.candidates || []);
        box.querySelectorAll('[data-review-action]').forEach(button =>
          button.addEventListener('click', () => confirmEntry(button)));
        box.querySelectorAll('[data-use-standardization]').forEach(button =>
          button.addEventListener('click', () => {
            const card = button.closest('.major-candidate');
            const suggestion = card.querySelector('[data-standardized-content]');
            const editor = card.querySelector('[data-candidate-content]');
            if (suggestion && editor) editor.value = suggestion.textContent;
          }));
        root.querySelector('[data-major-state]').textContent = 'TYPED_REVIEW_REQUIRED';
        const runtimeStatus = data.standardization && data.standardization.status;
        say(runtimeStatus === 'FAILED'
          ? 'Runtime 标准化未完成；原始 Source Typed 候选仍可审核。'
          : 'Source Fusion 候选已生成；请逐条核对来源证据，MULTI_SOURCE / CONFLICT 需填写人工结论和理由。');
        return;
      }
      box.innerHTML = data.candidates.map(item =>
        '<article class="major-candidate"><h3>' + esc(item.entry_type) + ' · PENDING</h3><p>' +
        esc(item.content) + '</p><button class="case-button secondary" data-entry="' + esc(item.entry_id) +
        '">人工确认并创建修订</button></article>').join('');
      box.querySelectorAll('[data-entry]').forEach(button => button.addEventListener('click', () => confirmEntry(button)));
      root.querySelector('[data-major-state]').textContent = 'REVIEW_REQUIRED';
      say('AI 候选已生成，必须逐条人工确认。');
    } catch (error) {
      say(error.message, true);
    }
  });

  async function confirmEntry(button) {
    try {
      const card = button.closest('.major-candidate');
      const textarea = card.querySelector('[data-candidate-content]');
      const reasonInput = card.querySelector('[data-review-reason]');
      const legacyContent = card.querySelector('p');
      const content = textarea ? textarea.value : (legacyContent ? legacyContent.textContent : '');
      const requiresReason = card.dataset.reviewRequired === 'true' || button.dataset.reviewAction === 'CORRECT';
      if (card.dataset.reviewRequired === 'true' && !content.trim()) {
        say('请填写人工决定后的结论。', true);
        if (textarea) textarea.focus();
        return;
      }
      if (requiresReason && reasonInput && !reasonInput.value.trim()) {
        say('请填写审核或更正理由。', true);
        reasonInput.focus();
        return;
      }
      const data = await read(await fetch(api + '/entries/' + encodeURIComponent(button.dataset.entry) + '/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reviewer: 'web-reviewer',
          content,
          reason: reasonInput ? reasonInput.value : 'Web human review',
          action: button.dataset.reviewAction || 'CONFIRM'
        })
      }));
      card.querySelector('h3').textContent =
        data.entry_type + ' · ' + data.status + ' · revision ' + data.revision_no;
      card.querySelectorAll('[data-entry]').forEach(item => { item.disabled = true; });
      const all = [...root.querySelectorAll('[data-entry]')].every(item => item.disabled);
      if (all && state.analysisMode !== 'SOURCE_FUSION') {
        root.querySelector('[data-major-publish]').disabled = false;
        root.querySelector('[data-major-state]').textContent = 'READY_TO_PUBLISH';
      } else if (all) {
        root.querySelector('[data-major-state]').textContent = 'TYPED_REVIEW_COMPLETE_I3_PENDING';
      }
      say(data.status === 'CORRECTED' ? '已创建人工更正修订。' : '已创建人工确认修订。');
    } catch (error) {
      say(error.message, true);
    }
  }

  root.querySelector('[data-major-publish]').addEventListener('click', async () => {
    try {
      const data = await read(await fetch(api + '/events/' + encodeURIComponent(state.eventId) + '/publish', { method: 'POST' }));
      root.querySelector('[data-major-state]').textContent = data.publication_status + ' · ' + data.status;
      say('已创建正式 Historical Case：' + data.case_id + '。可在案例库检索和复用。');
    } catch (error) {
      say(error.message, true);
    }
  });
})();
