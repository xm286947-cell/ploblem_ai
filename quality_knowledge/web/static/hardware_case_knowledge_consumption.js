(() => {
  const root = document.querySelector('[data-hc-page="knowledge-consumption"]');
  if (!root) return;

  const api = (root.dataset.api || '/api/public/hardware-knowledge/v1').replace(/\/$/, '');
  const q = (selector, parent = root) => parent.querySelector(selector);
  const qa = (selector, parent = root) => Array.from(parent.querySelectorAll(selector));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const text = value => value === null || value === undefined || value === '' ? '—' : String(value);

  const labels = {
    title: '标题', symptom: '问题现象', root_cause: '根因', failure_mechanism: '失效机理',
    actions: '解决措施', verification_result: '验证结果', engineering_rule: '工程规则',
    design_constraint: '设计约束', verification_method: '验证方法', applicability: '适用范围',
    interface: '接口', signal: '信号', device_refs: '器件引用', key_parameters: '关键参数',
    failure_mode: '失效模式', diagnostic_clue: '诊断线索', conclusion: '结论',
    occurrence_condition: '发生条件', analysis_process: '分析过程', occurrence: '发生条件'
  };
  const scenarios = {
    research: ['title', 'symptom', 'root_cause', 'failure_mechanism', 'actions', 'verification_result', 'engineering_rule', 'design_constraint', 'verification_method', 'applicability', 'interface', 'signal', 'device_refs', 'key_parameters'],
    risk: ['device_refs', 'interface', 'signal', 'key_parameters', 'failure_mode', 'failure_mechanism', 'design_constraint', 'diagnostic_clue', 'verification_method', 'applicability', 'conclusion'],
    market: ['title', 'symptom', 'occurrence_condition', 'device_refs', 'interface', 'signal', 'root_cause', 'actions', 'verification_result', 'conclusion', 'applicability']
  };
  const groups = [
    ['工程与观察', ['title', 'symptom', 'occurrence_condition', 'failure_mode', 'interface', 'signal']],
    ['分析与解决', ['root_cause', 'failure_mechanism', 'analysis_process', 'actions', 'verification_result']],
    ['可复用知识', ['engineering_rule', 'design_constraint', 'diagnostic_clue', 'verification_method', 'applicability', 'conclusion']],
    ['器件与参数', ['device_refs', 'key_parameters']]
  ];
  const sceneMap = {
    research: 'RND_DIAGNOSIS',
    risk: 'DEVICE_RISK',
    market: 'MARKET_ISSUE'
  };
  let activeScenario = 'research';
  let results = [];
  let query = { text: '', interface: '', signal: '', device: '' };

  function valueText(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (Array.isArray(value)) {
      if (!value.length) return '—';
      return value.map(item => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          if (item.name && item.value !== undefined) return `${item.name}: ${item.value}${item.unit ? ` ${item.unit}` : ''}`;
          return ['generic_name_or_series', 'manufacturer', 'manufacturer_part_no', 'internal_material_no', 'category']
            .map(key => item[key]).filter(Boolean).join(' / ') || JSON.stringify(item);
        }
        return String(item);
      }).join('；');
    }
    if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${item}`).join('；');
    return String(value);
  }

  function renderFields(item) {
    return scenarios[activeScenario].filter(field => item[field] !== undefined && item[field] !== null && item[field] !== '' && valueText(item[field]) !== '—')
      .map(field => `<div class="hc-knowledge-field"><strong>${esc(labels[field] || field)}</strong><span>${esc(valueText(item[field]))}</span></div>`).join('');
  }

  function renderReasons(item) {
    const reasons = Array.isArray(item.match_reasons) ? item.match_reasons : [];
    if (!reasons.length) return '';
    return `<div class="hc-knowledge-reasons" aria-label="可解释匹配原因">${reasons.map(reason => {
      const field = reason.matched_field;
      const actual = valueText(item[field]);
      return `<span class="hc-knowledge-reason"><strong>${esc(labels[field] || field)}</strong> 命中 ${esc(reason.matched_text)} · ${esc(actual)} · 权重 ${esc(reason.weight)}</span>`;
    }).join('')}</div>`;
  }

  function deviceLabel(ref) {
    if (!ref || typeof ref !== 'object' || ref.status === 'MISSING') return '';
    return [
      ref.generic_name_or_series,
      ref.manufacturer_part_no,
      ref.internal_material_no,
      ref.manufacturer,
      ref.category
    ].filter(Boolean).join(' / ');
  }

  function renderDeviceRiskSummary() {
    const panel = q('[data-device-risk-summary]');
    if (activeScenario !== 'risk') {
      panel.hidden = true;
      panel.innerHTML = '';
      return;
    }

    panel.hidden = false;
    const groups = new Map();
    results.forEach(item => {
      const labels = Array.from(new Set(
        (Array.isArray(item.device_refs) ? item.device_refs : [])
          .map(deviceLabel)
          .filter(Boolean)
      ));
      labels.forEach(label => {
        if (!groups.has(label)) {
          groups.set(label, {
            knowledgeIds: new Set(),
            caseIds: new Set(),
            mechanisms: new Set(),
            constraints: new Set()
          });
        }
        const group = groups.get(label);
        group.knowledgeIds.add(item.knowledge_id);
        group.caseIds.add(item.business_case_id);
        if (item.failure_mechanism) group.mechanisms.add(item.failure_mechanism);
        if (item.design_constraint) group.constraints.add(item.design_constraint);
      });
    });

    if (!groups.size) {
      panel.innerHTML = '<h3>器件风险聚合</h3><p>当前结果没有显式 DeviceRef，不做器件推断。</p>';
      return;
    }

    const cards = Array.from(groups.entries())
      .map(([label, group]) => ({
        label,
        knowledgeCount: group.knowledgeIds.size,
        caseCount: group.caseIds.size,
        mechanisms: Array.from(group.mechanisms).slice(0, 3),
        constraints: Array.from(group.constraints).slice(0, 3)
      }))
      .sort((a, b) =>
        b.caseCount - a.caseCount ||
        b.knowledgeCount - a.knowledgeCount ||
        a.label.localeCompare(b.label)
      );

    panel.innerHTML =
      '<h3>器件风险聚合</h3>' +
      '<div class="hc-device-risk-grid">' +
      cards.map(card =>
        '<article class="hc-device-risk-card">' +
        '<strong>' + esc(card.label) + '</strong>' +
        '<small>' + card.caseCount + ' 个 Case · ' + card.knowledgeCount + ' 条正式知识</small>' +
        '<span>失效机理：' + esc(card.mechanisms.length ? card.mechanisms.join('；') : '—') + '</span>' +
        '<span>设计约束：' + esc(card.constraints.length ? card.constraints.join('；') : '—') + '</span>' +
        '</article>'
      ).join('') +
      '</div>';
  }

  function renderResults() {
    const box = q('[data-knowledge-results]');
    q('[data-knowledge-summary]').textContent = `${results.length} 条正式知识 · ${activeScenario === 'research' ? '研发设计复用' : activeScenario === 'risk' ? '器件与电路风险' : '市场与应用问题检索'}`;
    renderDeviceRiskSummary();
    if (!results.length) {
      box.innerHTML = '<div class="hc-knowledge-empty">未检索到已发布的正式硬件知识</div>';
      return;
    }
    box.innerHTML = results.map(item => {
      const evidenceRefs = Array.isArray(item.evidence_refs) ? item.evidence_refs : [];
      return `<article class="hc-knowledge-result">
        <div class="hc-knowledge-result-head">
          <div><h3><button type="button" data-open-knowledge="${esc(item.knowledge_id)}">${esc(text(item.title))}</button></h3>
            <div class="hc-knowledge-meta"><code>knowledge_id: ${esc(item.knowledge_id)}</code><code>business_case_id: ${esc(item.business_case_id)}</code><span>${esc(item.source_domain)} / ${esc(item.source_object_type)}</span></div>
          </div><div class="hc-knowledge-score"><b>${esc(item.match_score ?? 0)}</b><small>match score</small></div>
        </div>
        <div class="hc-knowledge-fields">${renderFields(item)}</div>
        ${renderReasons(item)}
        <div class="hc-knowledge-evidence">Evidence refs (${evidenceRefs.length}): ${evidenceRefs.length ? evidenceRefs.map(ref => `<code>${esc(ref)}</code>`).join('、') : '—'}</div>
      </article>`;
    }).join('');
  }

  function setUnavailable(message = '正式知识消费索引当前不可用，请先重建 Consumption Projection。') {
    q('[data-knowledge-unavailable]').hidden = false;
    q('[data-knowledge-unavailable]').textContent = message;
    const riskPanel = q('[data-device-risk-summary]');
    riskPanel.hidden = true;
    riskPanel.innerHTML = '';
    q('[data-knowledge-results]').innerHTML = '';
    q('[data-knowledge-summary]').textContent = '消费索引不可用';
  }

  async function fetchJson(path) {
    const response = await fetch(api + path, { headers: { Accept: 'application/json' } });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const error = new Error(String(payload.detail || response.statusText || response.status));
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function searchParams() {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value) params.set(key, value); });
    params.set('scene', sceneMap[activeScenario] || 'RND_DIAGNOSIS');
    params.set('limit', '100');
    return `?${params.toString()}`;
  }

  async function runSearch() {
    q('[data-knowledge-unavailable]').hidden = true;
    q('[data-knowledge-summary]').textContent = '正在检索正式知识…';
    try {
      const payload = await fetchJson('/search' + searchParams());
      results = Array.isArray(payload.results) ? payload.results : [];
      renderResults();
    } catch (error) {
      results = [];
      if (error.status === 503) setUnavailable();
      else setUnavailable(`正式知识消费索引请求失败：${error.message}`);
    }
  }

  function renderDetail(item) {
    const detail = q('[data-knowledge-detail]');
    detail.dataset.knowledgeId = item.knowledge_id || '';
    detail.dataset.businessCaseId = item.business_case_id || '';
    const body = q('[data-detail-body]');
    const meta = [['knowledge_id', item.knowledge_id], ['public_ref', item.public_ref], ['business_case_id', item.business_case_id], ['source_domain', item.source_domain], ['source_object_type', item.source_object_type], ['formal_revision', item.formal_revision], ['formal_status', item.formal_status]];
    const metadata = `<section class="hc-knowledge-detail-section"><h3>正式知识身份</h3><div class="hc-knowledge-detail-grid">${meta.map(([key, value]) => `<div class="hc-knowledge-detail-item"><strong>${esc(key)}</strong><span>${esc(text(value))}</span></div>`).join('')}</div></section>`;
    const content = groups.map(([title, fields]) => {
      const present = fields.filter(field => item[field] !== undefined && item[field] !== null && item[field] !== '' && valueText(item[field]) !== '—');
      if (!present.length) return '';
      return `<section class="hc-knowledge-detail-section"><h3>${esc(title)}</h3><div class="hc-knowledge-detail-grid">${present.map(field => `<div class="hc-knowledge-detail-item"><strong>${esc(labels[field] || field)}</strong><span>${esc(valueText(item[field]))}</span></div>`).join('')}</div></section>`;
    }).join('');
    const refs = Array.isArray(item.evidence_refs) ? item.evidence_refs : [];
    body.innerHTML = metadata + content + `<section class="hc-knowledge-detail-section"><h3>Evidence refs</h3><div class="hc-knowledge-detail-item"><span>${refs.length ? refs.map(ref => `<code>${esc(ref)}</code>`).join('、') : '—'}</span></div></section>`;
    q('[data-detail-title]').textContent = text(item.title);
    q('[data-detail-subtitle]').textContent = `${item.knowledge_id || '—'} · ${item.business_case_id || '—'}`;
    q('[data-knowledge-detail]').hidden = false;
  }

  async function openDetail(knowledgeId) {
    q('[data-detail-title]').textContent = '正在读取正式知识…';
    q('[data-detail-subtitle]').textContent = knowledgeId;
    q('[data-detail-body]').innerHTML = '<div class="hc-knowledge-empty">正在读取…</div>';
    q('[data-knowledge-detail]').hidden = false;
    try {
      renderDetail(await fetchJson('/objects/' + encodeURIComponent(knowledgeId)));
    } catch (error) {
      if (error.status === 503) setUnavailable();
      q('[data-detail-body]').innerHTML = `<div class="hc-error">${esc(error.status === 503 ? '正式知识消费索引当前不可用，请先重建 Consumption Projection。' : `正式知识详情读取失败：${error.message}`)}</div>`;
    }
  }

  qa('[data-scenario]').forEach(button => button.addEventListener('click', () => {
    activeScenario = scenarios[button.dataset.scenario] ? button.dataset.scenario : 'research';
    qa('[data-scenario]').forEach(item => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    runSearch();
  }));
  q('[data-knowledge-form]').addEventListener('submit', event => {
    event.preventDefault();
    query = {
      text: q('[data-knowledge-text]').value.trim(),
      interface: q('[data-knowledge-filter="interface"]').value.trim(),
      signal: q('[data-knowledge-filter="signal"]').value.trim(),
      device: q('[data-knowledge-filter="device"]').value.trim()
    };
    runSearch();
  });
  q('[data-knowledge-clear]').addEventListener('click', () => {
    q('[data-knowledge-text]').value = '';
    qa('[data-knowledge-filter]').forEach(input => { input.value = ''; });
    query = { text: '', interface: '', signal: '', device: '' };
    runSearch();
  });
  q('[data-knowledge-results]').addEventListener('click', event => {
    const button = event.target.closest('[data-open-knowledge]');
    if (button) openDetail(button.dataset.openKnowledge);
  });
  q('[data-detail-close]').addEventListener('click', () => {
    const detail = q('[data-knowledge-detail]');
    detail.hidden = true;
    delete detail.dataset.knowledgeId;
    delete detail.dataset.businessCaseId;
  });
  runSearch();
})();
