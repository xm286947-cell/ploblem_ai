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


  let repeatInspection = null;
  let repeatResult = null;

  async function post(path, payload) {
    const response = await fetch(url(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload || {})
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      const error = new Error(detail.detail || 'HTTP_' + response.status);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function showRepeatState(name) {
    root.querySelectorAll('[data-repeat-state]').forEach(element => {
      element.hidden = element.dataset.repeatState !== name;
    });
  }

  function repeatContextLabel() {
    const optional = repeatInspection && obj(repeatInspection.optional_context);
    const toggle = root.querySelector('[data-missed-toggle]');
    const useMissed = Boolean(toggle && toggle.checked && optional.missed_test_available);
    return useMissed
      ? '当前 ITR + 关联漏测问题 ' + (optional.missed_test_ref || '')
      : '仅当前 ITR';
  }

  function renderRepeatSubject(payload) {
    repeatInspection = payload;
    const subject = obj(payload.subject);
    const snapshot = obj(subject.itr_snapshot);
    root.querySelector('[data-repeat-subject]').innerHTML =
      '<div class="p0-repeat-subject-grid">' +
      '<div><label>查询主体</label><strong>当前 ITR · ' + esc(snapshot.itr_id || subject.itr_ref || '-') + '</strong></div>' +
      '<div><label>问题</label><span>' + esc(snapshot.problem_description || '未提供') + '</span></div>' +
      '<div><label>产品 / 版本</label><span>' + esc([snapshot.product, snapshot.version].filter(Boolean).join(' / ') || '未提供') + '</span></div>' +
      '<div><label>场景</label><span>' + esc(snapshot.scene || '未提供') + '</span></div>' +
      '</div>';

    const optional = obj(payload.optional_context);
    const mount = root.querySelector('[data-repeat-optional]');
    mount.hidden = false;
    if (optional.missed_test_available) {
      mount.innerHTML =
        '<label class="p0-repeat-check"><input type="checkbox" data-missed-toggle> ' +
        '<span><strong>带入关联漏测问题</strong><small>' + esc(optional.missed_test_ref || '') +
        ' · 仅作为 Optional Context，不改变当前 ITR 主 Subject</small></span></label>';
      mount.querySelector('[data-missed-toggle]').addEventListener('change', () => {
        root.querySelector('[data-repeat-context-summary]').textContent = '本次查询：' + repeatContextLabel();
      });
    } else {
      mount.innerHTML = '<p class="p0-repeat-note">当前 ITR 无关联漏测问题，本次查询仅使用当前 ITR。</p>';
    }
    root.querySelector('[data-repeat-context-summary]').textContent = '本次查询：' + repeatContextLabel();
  }

  function evidenceHtml(item, index) {
    const evidenceId = item.evidence_id || item.id || '';
    const source = [item.source_type, item.source_id].filter(Boolean).join(' / ') || '未提供';
    const location = [
      item.file_name,
      item.page != null ? 'Page ' + item.page : '',
      item.section ? 'Section ' + item.section : ''
    ].filter(Boolean).join(' · ') || '未提供';
    const raw = item.raw_text || item.excerpt || item.content || '';
    const support = item.target_path || item.supports || item.field_path || '';
    const sourceLink = item.url
      ? '<a href="' + esc(item.url) + '" target="_blank" rel="noopener">查看来源</a>'
      : '<span>无可用来源链接</span>';
    return '<article class="p0-drawer-evidence">' +
      '<div class="p0-evidence-index">Evidence ' + esc(index + 1) + '</div>' +
      '<dl><dt>Evidence ID</dt><dd>' + esc(evidenceId || '未提供') + '</dd>' +
      '<dt>来源</dt><dd>' + esc(source) + '</dd>' +
      '<dt>文档 / 位置</dt><dd>' + esc(location) + '</dd>' +
      '<dt>支撑字段 / 结论</dt><dd>' + esc(support || '未确认 / 无已确认内容') + '</dd></dl>' +
      '<blockquote>' + esc(raw || '当前知识存在，但没有可用原始 Evidence。') + '</blockquote>' +
      '<div class="p0-source-link">' + sourceLink + '</div></article>';
  }

  function openEvidence(candidate) {
    const drawer = root.querySelector('[data-repeat-evidence-drawer]');
    const body = root.querySelector('[data-evidence-body]');
    const evidence = arr(candidate.evidence);
    drawer.hidden = false;
    document.body.classList.add('p0-drawer-open');
    body.innerHTML =
      '<div class="p0-drawer-context"><strong>' + esc(candidate.title || candidate.case_id || '历史案例') + '</strong>' +
      '<span>' + esc(candidate.case_id || '') + '</span></div>' +
      (evidence.length
        ? evidence.map(evidenceHtml).join('')
        : '<div class="p0-evidence-missing"><strong>当前知识存在，但没有可用原始 Evidence。</strong><p>系统不会补造 Evidence。</p></div>');
  }

  function closeEvidence() {
    root.querySelector('[data-repeat-evidence-drawer]').hidden = true;
    document.body.classList.remove('p0-drawer-open');
  }

  function candidateHtml(candidate, index) {
    const rationale = arr(candidate.why_relevant);
    const rootCauses = arr(candidate.root_causes);
    const measures = arr(candidate.measures);
    const score = typeof candidate.retrieval_score === 'number'
      ? Math.round(candidate.retrieval_score * 100) + '%'
      : '-';
    const evidenceCount = arr(candidate.evidence).length;
    return '<article class="p0-repeat-candidate">' +
      '<header><div><span class="p0-kicker">HISTORICAL CASE · #' + esc(candidate.rank || index + 1) + '</span>' +
      '<h3>' + esc(candidate.title || candidate.case_id || '历史案例') + '</h3>' +
      '<small>Case ' + esc(candidate.case_id || '-') + ' · 历史来源 ' + esc(candidate.source_ref || '-') + '</small></div></header>' +
      '<section class="p0-rationale"><label>为什么值得关注</label>' +
      (rationale.length
        ? '<ul>' + rationale.map(item => '<li>' + esc(text(item)) + '</li>').join('') + '</ul>'
        : '<p>' + esc(candidate.explanation_message || '当前检索结果未返回足够的可解释关联依据。') + '</p>') +
      '</section>' +
      '<div class="p0-repeat-case-grid">' +
      '<section><label>历史问题现象</label><p>' + esc(candidate.historical_phenomenon || '未确认 / 无已确认内容') + '</p></section>' +
      '<section><label>历史根因</label><p>' + esc(rootCauses.join('；') || '未确认 / 无已确认内容') + '</p></section>' +
      '<section><label>历史措施</label><p>' + esc(measures.join('；') || '未确认 / 无已确认内容') + '</p></section>' +
      '</div>' +
      '<div class="p0-repeat-secondary"><span><b>Verification</b> ' + esc(candidate.verification || '未确认 / 无已确认内容') + '</span>' +
      '<span><b>Similarity</b> ' + esc(score) + '</span>' +
      '<span><b>Evidence</b> ' + esc(evidenceCount) + '</span></div>' +
      '<footer><a class="p0-ghost" href="/p0/cases/' + encodeURIComponent(candidate.case_id || '') + '">查看完整案例</a>' +
      '<button class="p0-ghost" type="button" data-repeat-evidence="' + esc(index) + '">查看 Evidence</button></footer>' +
      '</article>';
  }

  function decisionHtml(result) {
    const decision = obj(result.human_decision);
    if (decision.decision && decision.decision !== 'PENDING') {
      return '<section class="p0-repeat-decision p0-repeat-decision-saved"><div><span class="p0-kicker">HUMAN DECISION</span>' +
        '<h3>人工结论：' + esc(decision.decision) + '</h3></div>' +
        '<dl><dt>确认人</dt><dd>' + esc(decision.decided_by || '-') + '</dd>' +
        '<dt>确认时间</dt><dd>' + esc(decision.decided_at || '-') + '</dd>' +
        '<dt>判断说明</dt><dd>' + esc(decision.reason || '未填写') + '</dd></dl></section>';
    }
    return '<section class="p0-repeat-decision"><div><span class="p0-kicker">HUMAN DECISION</span>' +
      '<h3>人工判断</h3><p>人工结论针对整次 Repeat Risk Run，不针对单个 Candidate。</p></div>' +
      '<label>结论<select data-repeat-decision><option value="">请选择</option>' +
      '<option value="REPEAT">REPEAT</option><option value="SIMILAR">SIMILAR</option>' +
      '<option value="NOT_REPEAT">NOT_REPEAT</option><option value="INSUFFICIENT_EVIDENCE">INSUFFICIENT_EVIDENCE</option></select></label>' +
      '<label>判断说明<textarea data-repeat-decision-reason rows="3" placeholder="请记录判断依据"></textarea></label>' +
      '<button class="p0-primary" type="button" data-repeat-decision-save>保存人工判断</button>' +
      '<span data-repeat-decision-status></span></section>';
  }

  function attachRepeatResultEvents(result) {
    root.querySelectorAll('[data-repeat-evidence]').forEach(button => {
      button.addEventListener('click', () => {
        const candidate = arr(result.candidates)[Number(button.dataset.repeatEvidence)];
        if (candidate) openEvidence(candidate);
      });
    });
    const save = root.querySelector('[data-repeat-decision-save]');
    if (save) save.addEventListener('click', saveRepeatDecision);
  }

  function resultBody(result) {
    const candidates = arr(result.candidates);
    const context = obj(result.query_snapshot);
    const contextText = context.include_missed_test
      ? '当前 ITR + 漏测问题 ' + (context.missed_test_ref || '')
      : '仅当前 ITR';
    return '<div class="p0-repeat-result-head"><div><span class="p0-kicker">QUERY CONTEXT</span><strong>' +
      esc(contextText) + '</strong></div><span>Query ' + esc(result.query_id || '-') + '</span></div>' +
      '<div class="p0-repeat-result-title">找到 <b>' + esc(result.candidate_count || 0) + '</b> 个值得关注的历史案例</div>' +
      candidates.map(candidateHtml).join('') +
      decisionHtml(result);
  }

  function renderRepeatResult(result) {
    repeatResult = result;
    const status = result && result.result_status;
    if (status === 'NO_CANDIDATES') {
      showRepeatState('empty');
      return;
    }
    if (status === 'SEARCH_UNAVAILABLE') {
      showRepeatState('unavailable');
      return;
    }
    if (status === 'INCOMPLETE') {
      showRepeatState('incomplete');
      const mount = root.querySelector('[data-repeat-incomplete-result]');
      mount.innerHTML = resultBody(result);
      attachRepeatResultEvents(result);
      return;
    }
    showRepeatState('result');
    const mount = root.querySelector('[data-repeat-result]');
    mount.innerHTML = resultBody(result);
    attachRepeatResultEvents(result);
  }

  async function loadRepeat() {
    try {
      const state = await get('/issues/' + encodeURIComponent(knowledgeId) + '/repeat-risk');
      renderRepeatSubject(state);
      if (state.latest_result) renderRepeatResult(state.latest_result);
      else showRepeatState('idle');
    } catch (error) {
      showRepeatState('unavailable');
    }
  }

  async function runRepeatQuery() {
    if (!repeatInspection) return;
    const button = root.querySelector('[data-repeat-query]');
    const toggle = root.querySelector('[data-missed-toggle]');
    const includeMissed = Boolean(toggle && toggle.checked);
    button.disabled = true;
    showRepeatState('running');
    root.querySelector('[data-repeat-running-context]').textContent =
      '查询主体：当前 ITR；本次 Context：' + repeatContextLabel();
    try {
      const payload = await post(
        '/issues/' + encodeURIComponent(knowledgeId) + '/repeat-risk/queries',
        { include_missed_test: includeMissed, top_k: 5 }
      );
      renderRepeatResult(obj(payload.result));
    } catch (error) {
      showRepeatState('unavailable');
    } finally {
      button.disabled = false;
    }
  }

  async function saveRepeatDecision() {
    if (!repeatResult || !repeatResult.query_id) return;
    const decision = root.querySelector('[data-repeat-decision]')?.value || '';
    const reason = root.querySelector('[data-repeat-decision-reason]')?.value.trim() || '';
    const status = root.querySelector('[data-repeat-decision-status]');
    if (!decision) {
      if (status) status.textContent = '请选择人工结论。';
      return;
    }
    if (status) status.textContent = '保存中…';
    try {
      const result = await post(
        '/repeat-risk/queries/' + encodeURIComponent(repeatResult.query_id) + '/decision',
        { decision, reason, decided_by: 'web' }
      );
      renderRepeatResult(result);
    } catch (error) {
      if (status) status.textContent = '保存失败：' + error.message;
    }
  }

  root.querySelectorAll('[data-analyze]').forEach(button => button.addEventListener('click', analyze));
  root.querySelector('[data-reload]').addEventListener('click', () => location.reload());
  root.querySelector('[data-confirm-form]').addEventListener('submit', confirm);
  root.querySelector('[data-repeat-query]').addEventListener('click', runRepeatQuery);
  root.querySelector('[data-repeat-retry]').addEventListener('click', runRepeatQuery);
  root.querySelector('[data-evidence-close]').addEventListener('click', closeEvidence);
  load();
  loadRepeat();
})();
