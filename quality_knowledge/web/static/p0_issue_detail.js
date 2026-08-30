(function () {
  'use strict';

  const root = document.querySelector('[data-p0-issue-detail]');
  if (!root) return;

  const SOURCE_TYPES = ['SOURCE_DATA', 'AI_STANDARDIZED', 'AI_INFERRED', 'HUMAN_CONFIRMED'];
  const api = (window.P0_ISSUES_API || root.dataset.apiPrefix || '/api/v2').replace(/\/$/, '');
  const knowledgeId = root.dataset.knowledgeId;
  const query = new URLSearchParams(location.search);
  const obj = value => value && typeof value === 'object' ? value : {};
  const arr = value => Array.isArray(value) ? value : [];
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const text = value => {
    if (value == null) return '';
    if (Array.isArray(value)) return value.map(text).filter(Boolean).join('、');
    if (typeof value === 'object') {
      return value.value || value.description || value.text || value.gap_description || value.excerpt || '';
    }
    return String(value);
  };

  function url(path, params) {
    const qs = new URLSearchParams(Object.entries(params || {}).filter(([, value]) => value !== '' && value != null));
    return api + path + (qs.toString() ? '?' + qs : '');
  }

  async function get(path, params) {
    const response = await fetch(url(path, params), { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      const error = new Error('HTTP_' + response.status);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function setState(name, visible) {
    const element = root.querySelector('[data-state="' + name + '"]');
    if (element) element.hidden = !visible;
  }

  function sourceLabel(source) {
    const value = String(source || 'AI_INFERRED').toUpperCase();
    return '<span class="p0-source p0-source-' + value.toLowerCase() + '">' + esc(value) + '</span>';
  }

  function flatten(value, prefix, rows) {
    if (value == null || typeof value !== 'object') {
      if (value !== '' && value != null) {
        rows.push('<div class="p0-raw-row"><dt>' + esc(prefix) + '</dt><dd>' + esc(value) + '</dd></div>');
      }
      return rows;
    }
    Object.entries(value).forEach(([key, nested]) => flatten(nested, prefix ? prefix + ' · ' + key : key, rows));
    return rows;
  }

  function renderFacts(issue) {
    const normalized = obj(issue.normalized_snapshot);
    const fact = obj(normalized.ISSUE_FACT || normalized.issue_fact || normalized.fact || normalized);
    const fields = [
      ['问题编号', issue.business_issue_id || fact.business_issue_id],
      ['产品', fact.product || issue.product_code],
      ['业务', issue.business_type],
      ['平台', fact.platform],
      ['月份', fact.month],
      ['严重度', fact.severity],
      ['问题类型', fact.issue_type]
    ];
    root.querySelector('[data-title]').textContent = fact.title || fact.description || issue.business_issue_id || knowledgeId;
    root.querySelector('[data-badges]').innerHTML = [issue.business_type, fact.month, fact.severity]
      .map(value => '<span class="p0-badge">' + esc(value || '-') + '</span>').join('');
    root.querySelector('[data-facts]').innerHTML = fields.map(([label, value]) =>
      '<div class="p0-fact"><label>' + esc(label) + '</label><strong>' + esc(value || '-') + '</strong></div>'
    ).join('');
    root.querySelector('[data-raw]').innerHTML = '<dl>' + flatten(issue.raw_json || {}, '', []).join('') + '</dl>';
    root.querySelector('[data-normalized]').innerHTML = '<dl>' + flatten(normalized, '', []).join('') + '</dl>';
  }

  function renderDiagnostics(analysis) {
    const mount = root.querySelector('[data-analysis-diagnostic]');
    const stages = arr(analysis.stages);
    if (!stages.length) {
      mount.hidden = true;
      return;
    }
    mount.hidden = false;
    mount.innerHTML = '<div class="p0-card-head"><div><span class="p0-kicker">ANALYSIS TRACE</span>' +
      '<h2>AI 分析过程与结果</h2><p>逐阶段显示执行状态、模型结果和失败原因。</p></div></div>' +
      '<div class="p0-stage-list">' + stages.map(stage => {
        const item = obj(stage);
        const status = String(item.status || 'UNKNOWN').toUpperCase();
        const error = item.validation_error || item.error || item.error_message || '';
        const result = text(item.parsed_result_json || item.result || item.value);
        return '<div class="p0-stage"><strong>' + esc(item.stage || '未命名阶段') + '</strong>' +
          '<span class="p0-badge p0-stage-' + status.toLowerCase() + '">' + esc(status) + '</span>' +
          (error ? '<p>' + esc(error) + '</p>' : result ? '<p>' + esc(result) + '</p>' : '<small>阶段已完成</small>') + '</div>';
      }).join('') + '</div>';
  }

  function renderMrc(analysis, effective) {
    const mrc = arr(analysis.mrc);
    const effectiveValues = obj(effective.values);
    const block = (title, side, path) => {
      const current = text(effectiveValues[path]);
      const values = mrc.filter(item => String(item.side || '').toUpperCase() === side);
      const body = current
        ? '<span class="p0-mrc">' + esc(current) + '</span><small class="p0-effective-label">当前有效结论（人工修订优先）</small>'
        : values.length
          ? values.map(item => '<span class="p0-mrc">' + esc(item.mrc_code || item.code || '-') + '</span>').join('')
          : '<span>原始数据未提供</span>';
      return '<section><label>' + title + '</label><p>' + body + '</p></section>';
    };
    root.querySelector('[data-mrc]').innerHTML =
      block('发生 MRC', 'OCCURRENCE', 'occurrence.mrc.primary') +
      block('流出 MRC', 'ESCAPE', 'escape.mrc.primary');
  }

  function renderRecurrence(analysis) {
    const values = arr(analysis.values).filter(item => String(item.stage || '').toLowerCase() === 'recurrence');
    const data = values.reduce((result, item) => {
      const key = String(item.value_path || item.key || 'value').replace(/^recurrence\./, '');
      result[key] = item.value_json || item.value || '';
      return result;
    }, {});
    const fields = [
      ['再发风险', text(data.recurrence_risk_level) || '未分析'],
      ['残余风险', text(data.residual_risk) || '—'],
      ['客户影响', text(data.customer_impact) || '—'],
      ['潜在影响范围', [text(data.potential_affected_products), text(data.potential_affected_versions)].filter(Boolean).join(' / ') || '—'],
      ['需要横向行动', data.horizontal_action_needed === true ? '是' : data.horizontal_action_needed === false ? '否' : '—']
    ];
    root.querySelector('[data-recurrence]').innerHTML = fields.map(([label, value]) =>
      '<section><label>' + esc(label) + '</label><p>' + esc(value) + '</p></section>'
    ).join('');
  }

  function renderGaps(analysis) {
    const gaps = arr(analysis.capability_gaps);
    root.querySelector('[data-gaps]').hidden = false;
    root.querySelector('[data-gap-list]').innerHTML = gaps.length ? gaps.map(gap =>
      '<article class="p0-gap"><strong>' + esc(gap.capability_code || '-') + '</strong>' +
      '<span class="p0-badge">' + esc(gap.capability_axis || '-') + '</span>' +
      '<p>' + esc(text(gap.gap_description || gap.details_json || gap.details) || '暂无缺口说明') + '</p>' +
      '<small>' + sourceLabel(gap.source_type || 'AI_INFERRED') + ' · ' + esc(gap.control_status || 'UNKNOWN') + '</small></article>'
    ).join('') : '<div class="p0-loading">尚未识别能力缺口。</div>';
  }

  function renderEvidence(analysis) {
    const evidence = arr(analysis.evidence);
    root.querySelector('[data-evidence]').hidden = false;
    root.querySelector('[data-evidence-list]').innerHTML = evidence.length ? evidence.map(item =>
      '<div class="p0-evidence"><b>' + esc(item.target_path || item.stage || '证据') + '</b> ' +
      sourceLabel(item.source_type) + '<p>' + esc(text(item.excerpt || item.content_json || item.evidence || item.value) || '—') +
      '</p><small>置信度 ' + esc(item.confidence == null ? '-' : item.confidence) + '</small></div>'
    ).join('') : '<div class="p0-loading">暂无结构化证据。</div>';
  }

  function questionRow(item, index) {
    const path = item.target_path || item.question_key || '';
    return '<div class="p0-question"><label>' + esc(item.question_text || item.question || item.question_key || '待确认事项') +
      ' · target_path: ' + esc(path || '-') + '</label>' +
      '<select name="status_' + index + '"><option value="PENDING">待确认</option><option value="CONFIRMED">确认</option>' +
      '<option value="CORRECTED">修正</option><option value="UNRESOLVED">无法确认</option><option value="NOT_APPLICABLE">不适用</option></select>' +
      '<textarea name="answer_' + index + '" rows="2" placeholder="填写人工判断或修正内容"></textarea>' +
      '<input type="hidden" name="target_' + index + '" value="' + esc(path) + '">' +
      '<input type="hidden" name="question_' + index + '" value="' + esc(item.question_key || item.question || '') + '"></div>';
  }

  function renderHumanAnalysis(analysis) {
    const questions = arr(analysis.open_questions);
    const defaults = [
      { question_text: '发生原因 / 工程与管理判断', target_path: 'occurrence.root_cause', question_key: 'manual_occurrence_root_cause' },
      { question_text: '发生 MRC', target_path: 'occurrence.mrc.primary', question_key: 'manual_occurrence_mrc' },
      { question_text: '流出 MRC', target_path: 'escape.mrc.primary', question_key: 'manual_escape_mrc' },
      { question_text: '客户影响与防控建议', target_path: 'recurrence.customer_impact', question_key: 'manual_customer_impact' }
    ];
    const rows = questions.length ? questions : defaults;
    root.querySelector('[data-confirmations]').hidden = false;
    root.querySelector('[data-question-list]').innerHTML = rows.map(questionRow).join('');
  }

  function renderAnalysis(analysis, effective) {
    if (!analysis) {
      setState('empty', true);
      return;
    }
    setState('empty', false);
    setState('running', false);
    root.querySelector('[data-analysis-grid]').hidden = false;
    renderDiagnostics(analysis);
    renderMrc(analysis, obj(effective));
    renderRecurrence(analysis);
    renderGaps(analysis);
    renderEvidence(analysis);
    renderHumanAnalysis(analysis);
    const revision = effective && effective.human_revision_id
      ? '<span class="p0-badge">已应用人工修订 · ' + esc(effective.human_revision_id) + '</span>' : '';
    root.querySelector('[data-scope-status]').innerHTML = '分析状态：' + esc(analysis.status || 'COMPLETED') +
      ' · Analysis Set ' + esc(analysis.analysis_set_id || '-') + ' ' + revision;
  }

  async function load() {
    try {
      const detail = await get('/issues/' + encodeURIComponent(knowledgeId));
      renderFacts(detail.issue || {});
      renderAnalysis(detail.analysis || null, detail.effective_analysis || null);
      const navigation = await get('/issues/' + encodeURIComponent(knowledgeId) + '/navigation', Object.fromEntries(query.entries()));
      root.querySelector('[data-position]').textContent = '第 ' + esc(navigation.position || '-') + ' / ' + esc(navigation.total || '-');
      const previous = root.querySelector('[data-previous]');
      const next = root.querySelector('[data-next]');
      previous.disabled = !navigation.previous_id;
      next.disabled = !navigation.next_id;
      previous.onclick = () => location.href = '/p0/issues/' + encodeURIComponent(navigation.previous_id) + '?' + query;
      next.onclick = () => location.href = '/p0/issues/' + encodeURIComponent(navigation.next_id) + '?' + query;
      root.querySelector('[data-back]').href = '/p0/issues' + (query.toString() ? '?' + query : '');
    } catch (error) {
      if (error.status === 409) setState('stale', true);
      else {
        setState('error', true);
        root.querySelector('[data-error-message]').textContent = '读取失败：' + error.message;
      }
    }
  }

  async function analyze() {
    setState('running', true);
    try {
      const response = await fetch(url('/issues/' + encodeURIComponent(knowledgeId) + '/analysis'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
      });
      if (!response.ok) throw new Error('HTTP_' + response.status);
      location.reload();
    } catch (error) {
      setState('running', false);
      setState('error', true);
      root.querySelector('[data-error-message]').textContent = '分析失败：' + error.message;
    }
  }

  async function confirm(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const answers = [];
    [...form.querySelectorAll('[name^="status_"]')].forEach((status, index) => {
      const targetPath = form.querySelector('[name="target_' + index + '"]')?.value || '';
      const questionKey = form.querySelector('[name="question_' + index + '"]')?.value || '';
      const answer = form.querySelector('[name="answer_' + index + '"]')?.value.trim() || '';
      const effective = ['CONFIRMED', 'CORRECTED'].includes(status.value);
      answers.push({
        target_path: targetPath,
        question_key: questionKey,
        confirmation_status: status.value,
        original_value: null,
        confirmed_value: effective ? answer : null,
        evidence: [],
        changes_insight: effective && (targetPath === 'occurrence.mrc.primary' || targetPath === 'escape.mrc.primary' || targetPath.startsWith('capability_gaps.'))
      });
    });
    const detail = await get('/issues/' + encodeURIComponent(knowledgeId));
    const analysis = obj(detail.analysis);
    const payload = {
      base_analysis_set_id: analysis.analysis_set_id,
      base_input_hash: analysis.input_hash,
      confirmed_by: 'web',
      answers
    };
    root.querySelector('[data-save-status]').textContent = '保存中…';
    try {
      const response = await fetch(url('/issues/' + encodeURIComponent(knowledgeId) + '/human-confirmations'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
      });
      if (response.status === 409) {
        setState('stale', true);
        return;
      }
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'HTTP_' + response.status);
      }
      root.querySelector('[data-save-status]').textContent = '已保存并应用有效修订';
      location.reload();
    } catch (error) {
      root.querySelector('[data-save-status]').textContent = '保存失败：' + error.message;
    }
  }

  root.querySelectorAll('[data-analyze]').forEach(button => button.addEventListener('click', analyze));
  root.querySelector('[data-reload]').addEventListener('click', () => location.reload());
  root.querySelector('[data-confirm-form]').addEventListener('submit', confirm);
  load();
})();
