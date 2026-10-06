(() => {
  const root = document.querySelector('[data-r1-workbench]');
  if (!root) return;

  const api = root.dataset.api;
  const headers = {'X-Hardware-Case-Role': 'MAINTAINER'};
  const state = {
    batch: null,
    item: null,
    promotion: null,
    scrollY: 0,
    itemAction: {pending: false, action: null, stage: null},
    reviewDraft: null,
    reviewEvidenceIds: [],
    consumptionReadyItemId: null,
  };

  const q = (selector) => root.querySelector(selector);
  const message = q('[data-workbench-message]');
  const historySelect = q('[data-batch-history]');
  const resultFilter = q('[data-result-filter]');
  const tbody = q('[data-batch-items]');
  const runBatchButton = q('[data-run-batch]');
  const retryBatchButton = q('[data-retry-failed]');
  const detail = q('[data-case-detail]');
  const debugPanel = q('[data-advanced-debug]');

  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  function setMessage(text, bad = false) {
    message.textContent = text;
    message.classList.toggle('hc-error', Boolean(bad));
  }

  function stageDisplay(stage) {
    if (stage === 'STAGE_A') return '问题事实提取';
    if (stage === 'STAGE_B') return '工程知识生成';
    if (stage === 'GATE') return '证据校验';
    if (stage === 'PARSE') return '文档解析';
    return stage || '失败环节';
  }

  function setItemActionStatus(text = '', bad = false) {
    const node = q('[data-item-action-status]');
    node.textContent = text;
    node.hidden = !text;
    node.classList.toggle('hc-error', Boolean(bad));
  }

  function paintItemActionControls(item = state.item) {
    const pending = Boolean(state.itemAction?.pending);
    const retry = q('[data-item-retry]');
    q('[data-item-run]').disabled = pending;
    q('[data-force-item]').disabled = pending;
    retry.disabled = pending || !(
      item?.result === 'FAILED' &&
      ['STAGE_A', 'STAGE_B'].includes(item?.failed_stage)
    );
    retry.textContent =
      pending && state.itemAction.action === 'retry-failed-stage'
        ? '正在重试' + stageDisplay(state.itemAction.stage) + '…'
        : '重试失败环节';
  }

  async function request(path, options = {}) {
    const response = await fetch(api + path, {
      ...options,
      headers: {...headers, ...(options.headers || {})},
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = {};
    }
    if (!response.ok) {
      const detailValue = payload?.detail;
      const code = typeof detailValue === 'string'
        ? detailValue
        : detailValue?.error_code || detailValue?.detail || response.statusText;
      throw new Error(code || 'REQUEST_FAILED');
    }
    return payload;
  }

  function statusClass(value) {
    const status = String(value || '');
    if (['PASS', 'CACHE_HIT', 'CANDIDATE_READY'].includes(status)) return 'ok';
    if (['REVIEW', 'QUEUED', 'RUNNING', 'WAITING', 'RUNTIME_BLOCKED', 'DEPENDENCY_BLOCKED'].includes(status)) return 'warn';
    if (['FAILED', 'PARSE_FAILED', 'STAGE_A_FAILED', 'STAGE_B_FAILED', 'GATE_FAILED'].includes(status)) return 'bad';
    return '';
  }

  const statusLabels = {
    PASS: '通过',
    CACHE_HIT: '复用已验证结果',
    CANDIDATE_READY: '待发布',
    REVIEW: '待人工确认',
    QUEUED: '待处理',
    RUNNING: '处理中',
    WAITING: '待执行',
    FAILED: '处理失败',
    PARSE_FAILED: '文档解析失败',
    STAGE_A_FAILED: '问题事实提取失败',
    STAGE_B_FAILED: '工程知识生成失败',
    GATE_FAILED: '证据校验失败',
    RUNTIME_BLOCKED: '运行环境阻断',
    DEPENDENCY_BLOCKED: '依赖阻断',
    READY_FOR_REVIEW: '待确认 / 待发布',
    PARTIAL_FAILURE: '部分失败',
    EMPTY: '暂无数据',
    MIXED: '混合状态',
  };

  function statusLabel(value) {
    const code = String(value || '');
    return statusLabels[code] || code || '—';
  }

  function statusPill(value, showCode = false) {
    const code = String(value || '');
    const display = statusLabel(code);
    return '<span class="hc-status ' + statusClass(code) + '" title="' +
      escapeHtml(code || '—') + '">' + escapeHtml(display) +
      (showCode && code ? '<small class="hc-tech-code">' + escapeHtml(code) + '</small>' : '') +
      '</span>';
  }

  function updateUrl({batchId, itemId} = {}) {
    const url = new URL(window.location.href);
    if (batchId !== undefined) {
      if (batchId) url.searchParams.set('batch', batchId);
      else url.searchParams.delete('batch');
    }
    if (itemId !== undefined) {
      if (itemId) url.searchParams.set('item', itemId);
      else url.searchParams.delete('item');
    }
    const filter = resultFilter.value;
    if (filter) url.searchParams.set('status', filter);
    else url.searchParams.delete('status');
    window.history.replaceState({}, '', url);
  }

  function renderSummary(summary = {}) {
    const values = [
      summary.TOTAL || 0,
      summary.QUEUED || 0,
      summary.RUNNING || 0,
      summary.CANDIDATE_READY || 0,
      summary.REVIEW || 0,
      summary.FAILED || 0,
    ];
    q('[data-batch-summary]').querySelectorAll('strong')
      .forEach((node, index) => { node.textContent = values[index]; });
  }

  function displayResult(item) {
    return item.result || item.orchestration_status || 'QUEUED';
  }

  function candidateForItem(item) {
    return item.candidate || item.pipeline_result?.knowledge_object || null;
  }

  function openReviewConflicts(item) {
    const candidate = candidateForItem(item);
    const conflicts = Array.isArray(candidate?.conflicts) ? candidate.conflicts : [];
    return conflicts.filter((conflict) =>
      conflict?.status === 'OPEN' ||
      conflict?.resolution_status === 'NEEDS_REVIEW'
    );
  }

  function reviewSourceLabel(source) {
    if (source === 'SOURCE_RAW_TITLE') return '标题';
    if (source === 'AI_BODY_CANDIDATE') return '正文识别';
    return source || '来源';
  }

  function reviewFieldLabel(field) {
    if (field === 'primary_subject') return '主体';
    return field || '字段';
  }

  function reviewConflictSummary(conflict) {
    const values = Array.isArray(conflict?.source_values)
      ? conflict.source_values
      : [];
    const valueText = values
      .map((entry) =>
        reviewSourceLabel(entry?.source) + '「' + String(entry?.value ?? '—') + '」'
      )
      .join(' ↔ ');
    return reviewFieldLabel(conflict?.field) + '不一致：' + (valueText || conflict?.type || '需要人工确认');
  }

  function localSummary(items = []) {
    const summary = {
      TOTAL: items.length,
      QUEUED: 0,
      RUNNING: 0,
      CANDIDATE_READY: 0,
      REVIEW: 0,
      FAILED: 0,
    };
    items.forEach((item) => {
      const value = displayResult(item);
      if (Object.prototype.hasOwnProperty.call(summary, value)) {
        summary[value] += 1;
      }
    });
    return summary;
  }

  function localBatchStatus(items = []) {
    const states = items.map((item) => displayResult(item));
    if (!states.length) return 'EMPTY';
    if (states.every((value) => value === 'QUEUED')) return 'QUEUED';
    if (states.some((value) => value === 'RUNNING')) return 'RUNNING';
    const failed = states.filter((value) => value === 'FAILED').length;
    const ready = states.filter((value) =>
      ['CANDIDATE_READY', 'REVIEW'].includes(value)).length;
    if (failed === states.length) return 'FAILED';
    if (failed && ready) return 'PARTIAL_FAILURE';
    if (ready === states.length) return 'READY_FOR_REVIEW';
    return 'MIXED';
  }

  function syncItemIntoBatch(item) {
    if (!state.batch || state.batch.batch_id !== item.batch_id) return;
    const items = [...(state.batch.items || [])];
    const index = items.findIndex((value) => value.item_id === item.item_id);
    if (index >= 0) items[index] = {...items[index], ...item};
    else items.push(item);
    renderBatch({
      ...state.batch,
      items,
      summary: localSummary(items),
      status: localBatchStatus(items),
    });
  }

  function failureHint(item) {
    if (item.error_code === 'PROVIDER_TIMEOUT' &&
        ['STAGE_A', 'STAGE_B'].includes(item.failed_stage)) {
      return 'AI 服务响应超时：建议使用“重试失败环节”，只重试失败步骤；不要直接“强制完整重跑”。';
    }
    if (item.error_code) {
      return '失败原因：' + item.error_code + '。可先查看“诊断信息（高级）”，再按失败环节选择性重试。';
    }
    return '';
  }

  async function refreshBatchSnapshot(batchId) {
    const batch = await request('/batches/' + encodeURIComponent(batchId));
    renderBatch(batch);
    historySelect.value = batchId;
    return batch;
  }

  let foregroundBatchPolling = 0;
  let passiveBatchPollingStop = null;

  function stopPassiveBatchPolling() {
    if (!passiveBatchPollingStop) return;
    const stop = passiveBatchPollingStop;
    passiveBatchPollingStop = null;
    stop();
  }

  function ensurePassiveBatchPolling(batch) {
    const shouldPoll = Boolean(
      batch?.batch_id &&
      batch.status === 'RUNNING' &&
      foregroundBatchPolling === 0
    );
    if (!shouldPoll) {
      stopPassiveBatchPolling();
      return;
    }
    if (passiveBatchPollingStop) return;

    const batchId = batch.batch_id;
    let stopped = false;
    let running = false;
    let timer = null;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      if (timer !== null) window.clearInterval(timer);
      if (passiveBatchPollingStop === stop) passiveBatchPollingStop = null;
    };
    const tick = async () => {
      if (stopped || running) return;
      if (state.batch?.batch_id !== batchId) {
        stop();
        return;
      }
      running = true;
      try {
        const latest = await refreshBatchSnapshot(batchId);
        if (latest.status !== 'RUNNING') stop();
      } catch (_) {
        // Keep the currently visible snapshot; the next tick may recover.
      } finally {
        running = false;
      }
    };
    timer = window.setInterval(tick, 1000);
    passiveBatchPollingStop = stop;
    tick();
  }

  function startBatchPolling(batchId) {
    stopPassiveBatchPolling();
    foregroundBatchPolling += 1;
    let stopped = false;
    let running = false;
    const tick = async () => {
      if (stopped || running) return;
      running = true;
      try {
        await refreshBatchSnapshot(batchId);
      } catch (_) {
        // Foreground action owns the user-visible error.
      } finally {
        running = false;
      }
    };
    const timer = window.setInterval(tick, 1000);
    tick();
    return () => {
      if (stopped) return;
      stopped = true;
      window.clearInterval(timer);
      foregroundBatchPolling = Math.max(0, foregroundBatchPolling - 1);
      if (state.batch?.status === 'RUNNING') {
        ensurePassiveBatchPolling(state.batch);
      }
    };
  }


  function startItemPolling(itemId) {
    let stopped = false;
    let running = false;
    const tick = async () => {
      if (stopped || running) return;
      running = true;
      try {
        const item = await request('/items/' + encodeURIComponent(itemId));
        syncItemIntoBatch(item);
        if (state.item?.item_id === itemId) {
          renderDetail(item, {scroll: false, refreshPromotionState: false});
        }
      } catch (_) {
        // The foreground POST owns the terminal user-visible error.
      } finally {
        running = false;
      }
    };
    const timer = window.setInterval(tick, 1000);
    tick();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }

  function renderBatch(batch) {
    state.batch = batch;
    ensurePassiveBatchPolling(batch);
    q('[data-batch-ref]').textContent =
      batch ? batch.batch_id + ' · ' + statusLabel(batch.status) : '尚未选择导入任务。';
    renderSummary(batch?.summary || {});
    runBatchButton.disabled = !batch;
    retryBatchButton.disabled = !batch || !(batch.summary?.FAILED > 0);

    if (!batch) {
      tbody.innerHTML = '<tr><td colspan="11" class="hc-empty">暂无导入任务。</td></tr>';
      return;
    }

    const filter = resultFilter.value;
    const items = (batch.items || []).filter((item) => {
      if (!filter) return true;
      return displayResult(item) === filter || item.orchestration_status === filter;
    });

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="11" class="hc-empty">当前筛选条件下没有案例。</td></tr>';
      return;
    }

    tbody.innerHTML = items.map((item) => {
      const result = displayResult(item);
      const conflicts = openReviewConflicts(item);
      const reviewInline = result === 'REVIEW' && conflicts.length
        ? '<div class="hc-review-inline">需确认：' +
          escapeHtml(reviewConflictSummary(conflicts[0])) +
          (conflicts.length > 1 ? ' +' + (conflicts.length - 1) : '') +
          '</div>'
        : '';
      return `
        <tr>
          <td>
            <strong>${escapeHtml(item.business_case_id || 'ID 待确认')}</strong>
            <small>${escapeHtml(item.source_file)}</small>
          </td>
          <td>${statusPill(item.parse)}</td>
          <td>${statusPill(item.stage_a)}</td>
          <td>${statusPill(item.stage_b)}</td>
          <td>${statusPill(item.gate)}</td>
          <td>${statusPill(result)}${reviewInline}</td>
          <td><small class="${item.error_code ? 'hc-error' : ''}">${escapeHtml(item.error_code || '—')}</small></td>
          <td>${Number(item.provider_calls || 0)}</td>
          <td>${Number(item.duration_ms || 0).toLocaleString()} ms</td>
          <td><small>${escapeHtml(item.updated_at || '—')}</small></td>
          <td>
            <button class="hc-button" type="button"
              data-open-item="${escapeHtml(item.item_id)}">查看详情</button>
          </td>
        </tr>
      `;
    }).join('');
  }

  async function loadHistory(preferredBatch = '') {
    try {
      const payload = await request('/batches?limit=50');
      const batches = payload.items || [];
      historySelect.innerHTML = '<option value="">历史导入记录</option>' +
        batches.map((batch) =>
          '<option value="' + escapeHtml(batch.batch_id) + '">' +
          escapeHtml(batch.batch_id + ' · ' + statusLabel(batch.status)) +
          '</option>'
        ).join('');
      const target = preferredBatch || new URL(window.location.href).searchParams.get('batch') ||
        batches[0]?.batch_id || '';
      if (target) {
        historySelect.value = target;
        await loadBatch(target);
      } else {
        renderBatch(null);
        setMessage('暂无导入任务。请先上传 Word。');
      }
    } catch (error) {
      setMessage('历史导入记录加载失败：' + error.message, true);
    }
  }

  async function loadBatch(batchId) {
    if (!batchId) {
      renderBatch(null);
      updateUrl({batchId: '', itemId: ''});
      return;
    }
    try {
      const batch = await request('/batches/' + encodeURIComponent(batchId));
      renderBatch(batch);
      historySelect.value = batchId;
      updateUrl({batchId, itemId: state.item?.item_id || ''});
      setMessage(
        '导入任务已载入：' + batchId +
        (batch.status === 'PARTIAL_FAILURE' ? '（存在部分失败，已成功的案例会保留）' : '')
      );
    } catch (error) {
      setMessage('导入任务加载失败：' + error.message, true);
    }
  }

  async function uploadFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const data = new FormData();
    files.forEach((file) => data.append('files', file, file.name));
    setMessage('正在创建导入任务，并执行文档解析和源文件校验…');
    try {
      const batch = await request('/batches', {
        method: 'POST',
        body: data,
      });
      state.item = null;
      detail.hidden = true;
      debugPanel.hidden = true;
      await loadHistory(batch.batch_id);
      setMessage(
        '导入任务已创建。可继续处理；单个文件失败不会影响其他案例。'
      );
    } catch (error) {
      setMessage('Word 上传失败：' + error.message, true);
    }
  }

  async function batchAction(action) {
    if (!state.batch) return;
    try {
      if (action === 'retry-failed-only') {
        const count = Number(state.batch.summary?.FAILED || 0);
        if (!window.confirm('将只重试当前 Batch 内 ' + count + ' 个 Failed Case。继续？')) {
          return;
        }
      }
      setMessage(action === 'retry-failed-only'
        ? '正在仅重试失败项…'
        : '正在开始 / 继续处理本次导入任务…');
      const batchId = state.batch.batch_id;
      const stopPolling = startBatchPolling(batchId);
      let payload;
      try {
        payload = await request(
          '/batches/' + encodeURIComponent(batchId) + '/' + action,
          {method: 'POST'}
        );
      } finally {
        stopPolling();
      }
      renderBatch(payload);
      const selected = payload.retry_selected_count;
      setMessage(selected === undefined
        ? '本次导入任务处理完成。'
        : '失败项重试完成，共选择 ' + selected + ' 个失败案例。');
    } catch (error) {
      setMessage('导入任务操作失败：' + error.message, true);
    }
  }

  function renderReviewRequired(item) {
    const panel = q('[data-review-required]');
    const summary = q('[data-review-summary]');
    const container = q('[data-review-conflicts]');
    const conflicts = openReviewConflicts(item);
    const show = displayResult(item) === 'REVIEW' && conflicts.length > 0;
    panel.hidden = !show;
    if (!show) {
      summary.textContent = '';
      container.innerHTML = '';
      return;
    }

    summary.textContent =
      'Stage A / Stage B / Gate 已通过；只需要确认下面 ' +
      conflicts.length +
      ' 个冲突项，不需要重跑。';

    container.innerHTML = conflicts.map((conflict) => {
      const values = Array.isArray(conflict.source_values)
        ? conflict.source_values
        : [];
      const comparisons = values.map((entry) =>
        '<div class="hc-review-value"><b>' +
        escapeHtml(reviewSourceLabel(entry?.source)) +
        '</b><span>' +
        escapeHtml(entry?.value ?? '—') +
        '</span><button class="hc-button hc-review-confirm" type="button" ' +
        'data-review-conflict="' + escapeHtml(conflict.conflict_id || '') + '" ' +
        'data-review-source="' + escapeHtml(entry?.source || '') + '">' +
        '确认采用</button></div>'
      ).join('');
      const blocks = Array.isArray(conflict.evidence_block_ids)
        ? conflict.evidence_block_ids.join(', ')
        : '—';
      return '<div class="hc-review-conflict">' +
        '<div class="hc-review-conflict-head"><strong>' +
        escapeHtml(reviewFieldLabel(conflict.field)) +
        '</strong><span>' +
        escapeHtml(conflict.type || 'NEEDS_REVIEW') +
        '</span></div>' +
        '<div class="hc-review-compare">' + comparisons + '</div>' +
        '<div class="hc-review-evidence-ref">Evidence: ' +
        escapeHtml(blocks) +
        '</div></div>';
    }).join('');
  }

  const humanReviewProtectedTop = new Set([
    'contract_version', 'identity', 'source_fact', 'evidence',
    'conflicts', 'review', 'provenance'
  ]);

  function humanReviewLabel(path) {
    return path.map((part) => String(part).replaceAll('_', ' ')).join(' › ');
  }

  function humanReviewEditor(value, path) {
    if (Array.isArray(value)) {
      return value.map((child, index) =>
        humanReviewEditor(child, [...path, index])
      ).join('');
    }
    if (!value || typeof value !== 'object') return '';
    if (!Object.prototype.hasOwnProperty.call(value, 'value')) {
      return Object.entries(value).map(([key, child]) =>
        humanReviewEditor(child, [...path, key])
      ).join('');
    }

    const semanticKeys = Object.prototype.hasOwnProperty.call(value, 'name')
      ? ['name', 'value', 'unit'].filter((key) =>
          Object.prototype.hasOwnProperty.call(value, key)
        )
      : ['value'];
    const evidenceRefs = Array.isArray(value.evidence_block_ids)
      ? value.evidence_block_ids.join(', ')
      : '—';
    const metadata = [
      value.extraction_status ? 'status=' + value.extraction_status : '',
      evidenceRefs !== '—' ? 'evidence=' + evidenceRefs : '',
      value.confidence !== undefined && value.confidence !== null
        ? 'confidence=' + value.confidence
        : '',
    ].filter(Boolean).join(' · ');
    const aiSummary = semanticKeys.map((key) =>
      key + '=' + String(value[key] ?? '—')
    ).join(' · ');
    const editors = semanticKeys.map((key) => {
      const fieldValue = value[key];
      const fieldPath = [...path, key];
      const encodedPath = encodeURIComponent(JSON.stringify(fieldPath));
      const type = Array.isArray(fieldValue)
        ? 'json'
        : fieldValue === null
          ? 'null'
          : typeof fieldValue;
      const editableValue = type === 'json'
        ? JSON.stringify(fieldValue, null, 2)
        : fieldValue ?? '';
      return '<label>' + escapeHtml(key) +
        '<textarea class="hc-review-textarea" rows="2" data-human-review-path="' +
        encodedPath + '" data-human-review-type="' + escapeHtml(type) + '">' +
        escapeHtml(editableValue) + '</textarea></label>';
    }).join('');

    const isKeyParameter =
      path[0] === 'engineering_context' &&
      path[1] === 'key_parameters' &&
      typeof path[2] === 'number' &&
      Object.prototype.hasOwnProperty.call(value, 'name');
    const isNewParameter = isKeyParameter && value.__review_new === true;
    const evidenceEditor = isNewParameter
      ? '<label>Evidence block<select multiple data-human-review-new-evidence="' +
        escapeHtml(path[2]) + '">' +
        state.reviewEvidenceIds.map((blockId) =>
          '<option value="' + escapeHtml(blockId) + '"' +
          ((value.evidence_block_ids || []).includes(blockId) ? ' selected' : '') +
          '>' + escapeHtml(blockId) + '</option>'
        ).join('') +
        '</select></label>'
      : '';
    const removeAction = isKeyParameter
      ? '<button type="button" class="hc-button secondary" data-human-review-remove-parameter="' +
        escapeHtml(path[2]) + '">删除参数</button>'
      : '';

    return '<article class="hc-review-fact"><div class="hc-review-fact-head"><h3>' +
      escapeHtml(humanReviewLabel(path)) +
      '</h3><span class="hc-status">' +
      (isNewParameter ? 'HUMAN ADDED' : semanticKeys.length > 1 ? 'PARAMETER SEMANTICS' : 'VALUE ONLY') +
      '</span></div>' +
      '<div class="hc-review-columns"><div class="hc-review-column"><label>AI Candidate</label><p>' +
      escapeHtml(isNewParameter ? '人工新增参数' : aiSummary) +
      '</p><small>' + escapeHtml(
        isNewParameter
          ? '新增参数必须绑定现有 Evidence'
          : metadata || 'Evidence metadata is read-only'
      ) +
      '</small></div><div class="hc-review-column"><label>人工确认值</label>' +
      editors + evidenceEditor + removeAction + '</div></div></article>';
  }

  function paintHumanReviewFields() {
    if (!state.reviewDraft) return;
    const editable = Object.entries(state.reviewDraft)
      .filter(([key]) => !humanReviewProtectedTop.has(key))
      .map(([key, value]) => humanReviewEditor(value, [key]))
      .join('');
    q('[data-human-review-fields]').innerHTML =
      (editable || '<div class="hc-empty">当前 Candidate 没有可编辑业务字段。</div>') +
      '<div class="hc-workbench-detail-actions">' +
      '<button type="button" class="hc-button secondary" data-human-review-add-parameter>' +
      '新增关键参数</button></div>';
  }

  function setHumanReviewDraftValue(path, raw, type) {
    if (!state.reviewDraft || !path.length) return;
    let target = state.reviewDraft;
    for (let index = 0; index < path.length - 1; index += 1) {
      if (!target[path[index]] || typeof target[path[index]] !== 'object') {
        target[path[index]] = {};
      }
      target = target[path[index]];
    }
    let value = raw;
    if (type === 'json') {
      value = JSON.parse(raw);
    } else if (type === 'number') {
      value = raw === '' ? null : Number(raw);
    } else if (type === 'boolean') {
      value = String(raw).trim().toLowerCase() === 'true';
    } else if (type === 'null' && raw === '') {
      value = null;
    }
    target[path[path.length - 1]] = value;
  }

  function renderHumanReview(item) {
    const panel = q('[data-human-review]');
    const candidate = candidateForItem(item);
    panel.hidden = !candidate;
    if (!candidate) {
      state.reviewDraft = null;
      return;
    }
    state.reviewDraft = JSON.parse(JSON.stringify(candidate));
    const evidence = Array.isArray(candidate.evidence) ? candidate.evidence : [];
    state.reviewEvidenceIds = evidence
      .map((entry) => String(entry?.block_id || '').trim())
      .filter(Boolean);
    const parameters = state.reviewDraft?.engineering_context?.key_parameters;
    if (Array.isArray(parameters)) {
      parameters.forEach((parameter, index) => {
        if (parameter && typeof parameter === 'object') {
          parameter.__review_original_index = index;
        }
      });
    }
    const asset = item.candidate_asset || {};
    const reviewStatus = asset.production_review_status || 'NOT_REQUIRED';
    q('[data-human-review-status]').textContent = reviewStatus;
    paintHumanReviewFields();

    q('[data-human-review-evidence]').innerHTML = evidence.length
      ? evidence.map((entry) =>
          '<article class="hc-evidence-item"><strong>' +
          escapeHtml(entry.block_id || 'Evidence') +
          '</strong><p>' + escapeHtml(entry.text || entry.caption || '—') +
          '</p><small>' +
          escapeHtml(JSON.stringify(entry.source_locator || {})) +
          '</small></article>'
        ).join('')
      : '<div class="hc-empty">暂无 Evidence。</div>';

    const history = Array.isArray(item.review_history) ? item.review_history : [];
    q('[data-human-review-history]').innerHTML = history.length
      ? history.map((entry) => {
          const disposition = entry.review_record?.disposition || 'CONFIRMED';
          return '<div class="hc-review-value"><b>' +
            escapeHtml(disposition) + '</b><span>' +
            escapeHtml(entry.reviewer || '—') + ' · ' +
            escapeHtml(entry.reason || '—') + ' · ' +
            escapeHtml(entry.created_at || '—') + '</span></div>';
        }).join('')
      : '<div class="hc-empty">暂无人工 Review Audit。</div>';

    const locked = String(asset.promotion_status || 'NOT_STARTED') !== 'NOT_STARTED';
    root.querySelectorAll(
      '[data-human-review-action], [data-human-review-add-parameter], [data-human-review-remove-parameter]'
    ).forEach((button) => {
      button.disabled = locked;
    });
    const reviewMessage = q('[data-human-review-message]');
    reviewMessage.hidden = true;
    reviewMessage.textContent = '';
  }

  async function humanReviewAction(decision) {
    if (!state.item) return;
    const reviewer = q('[data-human-reviewer]').value.trim();
    const reason = q('[data-human-review-reason]').value.trim();
    const reviewMessage = q('[data-human-review-message]');
    if (!reviewer || !reason) {
      reviewMessage.hidden = false;
      reviewMessage.textContent = 'Reviewer 和 Review Reason 必填。';
      reviewMessage.classList.add('hc-error');
      return;
    }
    if (decision === 'CONFIRM' && !window.confirm(
      '确认保存人工修正并将这份知识标记为“人工确认完成”？原始资料和证据不会被修改。'
    )) return;
    if (decision === 'REJECT' && !window.confirm(
      '拒绝后知识草稿会保留，但不能继续正式发布。确认拒绝？'
    )) return;
    try {
      reviewMessage.hidden = false;
      reviewMessage.classList.remove('hc-error');
      reviewMessage.textContent = '正在保存人工确认…';
      const payload = await request(
        '/items/' + encodeURIComponent(state.item.item_id) + '/human-review',
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            decision,
            reviewer,
            reason,
            confirmed_content: decision === 'CONFIRM' ? state.reviewDraft : null,
          }),
        }
      );
      syncItemIntoBatch(payload);
      renderDetail(payload, {scroll: false});
      setMessage(
        decision === 'CONFIRM'
          ? '人工修正确认已保存，可继续正式知识发布。'
          : '人工确认结果已记录，当前知识仍不能进入正式发布。'
      );
    } catch (error) {
      reviewMessage.hidden = false;
      reviewMessage.classList.add('hc-error');
      reviewMessage.textContent = '人工确认保存失败：' + error.message;
      setMessage('人工确认保存失败：' + error.message, true);
    }
  }

  function renderNextStep(item = state.item, promotion = state.promotion) {
    const title = q('[data-next-step-title]');
    const body = q('[data-next-step-body]');
    const flow = q('[data-knowledge-flow]');
    if (!title || !body || !flow || !item) return;

    const result = displayResult(item);
    const promotionStatus =
      promotion?.status || promotion?.promotion_status || 'NOT_STARTED';
    const reviewRequired =
      result === 'REVIEW' ||
      item.candidate_asset?.production_review_status === 'REQUIRED';
    const consumptionReady = state.consumptionReadyItemId === item.item_id;

    let active = 0;
    let titleText = '下一步：继续处理';
    let bodyText = '完成 AI 分析后，再进行人工确认和正式知识发布。';

    if (result === 'RUNNING' || result === 'QUEUED') {
      active = 0;
      titleText = result === 'RUNNING' ? '正在进行 AI 分析' : '下一步：开始 AI 分析';
      bodyText = '系统正在执行“文档解析 → 问题事实提取 → 工程知识生成 → 证据校验”。';
    } else if (result === 'FAILED') {
      active = 0;
      titleText = '下一步：处理失败项';
      bodyText = '先查看失败原因，再使用“重试失败环节”；已成功的步骤不会重复执行。';
    } else if (reviewRequired) {
      active = 1;
      titleText = '下一步：完成人工确认';
      bodyText = '对照 AI 识别结果和原文证据，确认或修正后即可进入正式知识发布。';
    } else if (consumptionReady) {
      active = 5;
      titleText = '已完成：正式知识可以检索使用';
      bodyText = '检索数据已生成，可直接进入“正式知识检索”搜索和查看证据。';
    } else if (['NOT_STARTED', 'PRECHECK_PASS', 'INTAKE_FAILED', 'CANDIDATE_INTAKED',
                'REVIEW_FAILED', 'REVIEW_CONFIRMED', 'PUBLISH_FAILED'].includes(promotionStatus)) {
      active = 2;
      if (promotionStatus === 'NOT_STARTED') titleText = '下一步：进行发布前检查';
      else if (['PRECHECK_PASS', 'INTAKE_FAILED'].includes(promotionStatus)) titleText = '下一步：提交正式审核';
      else if (['CANDIDATE_INTAKED', 'REVIEW_FAILED'].includes(promotionStatus)) titleText = '下一步：确认正式审核';
      else titleText = '下一步：发布正式知识';
      bodyText = '按下方“正式知识发布”步骤依次完成，不会自动发布。';
    } else if (['PUBLISHED_PENDING_QUERY_BACK', 'VERIFY_FAILED'].includes(promotionStatus)) {
      active = 3;
      titleText = '下一步：验证发布结果';
      bodyText = '确认已发布的正式知识能够按原身份正确读取后，再生成检索数据。';
    } else if (promotionStatus === 'VERIFIED') {
      active = 4;
      titleText = '下一步：生成检索数据';
      bodyText = '正式知识已经发布并验证通过；生成检索数据后即可被正式知识检索使用。';
    }

    const steps = ['AI 分析', '人工确认', '正式发布', '发布验证', '生成检索数据', '搜索使用'];
    flow.innerHTML = steps.map((step, index) =>
      '<span class="' + (index < active ? 'done' : index === active ? 'active' : '') + '">' +
      '<b>' + (index + 1) + '</b>' + escapeHtml(step) + '</span>'
    ).join('');
    title.textContent = titleText;
    body.textContent = bodyText;
  }

  function renderDetail(
    item,
    {scroll = true, refreshPromotionState = true} = {}
  ) {
    state.item = item;
    detail.hidden = false;
    debugPanel.hidden = true;
    q('[data-case-heading]').textContent =
      (item.business_case_id || 'ID 待确认') + ' · ' + (item.source_file || '—');
    q('[data-detail-batch]').textContent = item.batch_id || '—';
    q('[data-detail-status]').innerHTML = statusPill(displayResult(item), true);
    q('[data-detail-failed-stage]').textContent =
      item.failed_stage ? stageDisplay(item.failed_stage) + '（' + item.failed_stage + '）' : '—';
    q('[data-detail-error-code]').textContent = item.error_code || '—';
    const hint = q('[data-detail-failure-hint]');
    const hintText = failureHint(item);
    hint.textContent = hintText;
    hint.hidden = !hintText;
    hint.classList.toggle('hc-error', Boolean(item.error_code));
    q('[data-detail-provider-calls]').textContent = item.provider_calls ?? 0;
    q('[data-detail-duration]').textContent =
      Number(item.duration_ms || 0).toLocaleString() + ' ms';
    q('[data-detail-run-ref]').textContent = item.run_ref || '—';
    q('[data-detail-stages]').innerHTML = [
      ['文档解析', item.parse],
      ['问题事实提取', item.stage_a],
      ['工程知识生成', item.stage_b],
      ['证据校验', item.gate],
      ['处理结果', displayResult(item)],
    ].map(([name, value]) =>
      '<span><b>' + escapeHtml(name) + '</b>' + statusPill(value) + '</span>'
    ).join('');
    renderReviewRequired(item);
    renderHumanReview(item);
    renderNextStep(item, null);
    q('[data-candidate-preview]').textContent =
      JSON.stringify(item.candidate || null, null, 2);
    q('[data-evidence-summary]').textContent =
      JSON.stringify(item.evidence_validation || null, null, 2);
    paintItemActionControls(item);
    updateUrl({batchId: item.batch_id, itemId: item.item_id});
    const promotionPanel = q('[data-e2e-promotion]');
    if (promotionPanel) {
      promotionPanel.hidden = !item.candidate_id && !item.candidate?.candidate_id;
      if (!promotionPanel.hidden && refreshPromotionState) {
        refreshPromotion(item.item_id);
      }
    }
    if (scroll) {
      detail.scrollIntoView({block: 'start', behavior: 'smooth'});
    }
  }

  function paintPromotion(payload, note = '') {
    const panel = q('[data-e2e-promotion]');
    if (!panel) return;
    const promotion = payload?.promotion || payload || {};
    state.promotion = promotion;
    const status = promotion.status || promotion.promotion_status || 'NOT_STARTED';
    const promotionLabels = {
      NOT_STARTED: '尚未开始',
      PRECHECK_PASS: '发布前检查通过',
      INTAKE_FAILED: '提交正式审核失败',
      CANDIDATE_INTAKED: '已提交正式审核',
      REVIEW_FAILED: '正式审核失败',
      REVIEW_CONFIRMED: '正式审核已通过',
      PUBLISH_FAILED: '发布失败',
      PUBLISHED_PENDING_QUERY_BACK: '已发布，待验证',
      VERIFY_FAILED: '发布验证失败',
      VERIFIED: '发布验证通过',
      BLOCKED: '流程被阻断',
    };
    q('[data-promotion-status]').textContent = [
      '当前状态：' + (promotionLabels[status] || status) + (status ? '（' + status + '）' : ''),
      promotion.knowledge_id ? '正式知识编号：' + promotion.knowledge_id : '',
      promotion.error_code ? '错误：' + promotion.error_code : '',
      note,
    ].filter(Boolean).join(' · ');
    renderNextStep(state.item, promotion);
    const canAct = Boolean(
      state.item &&
      ['CANDIDATE_READY', 'REVIEW'].includes(displayResult(state.item)) &&
      state.item.candidate_asset?.production_review_status !== 'REQUIRED'
    );
    q('[data-promotion-precheck]').disabled = !canAct || !['NOT_STARTED', 'PRECHECK_PASS'].includes(status);
    q('[data-promotion-intake]').disabled = !canAct || !['PRECHECK_PASS', 'INTAKE_FAILED'].includes(status);
    q('[data-promotion-review]').disabled = !canAct || status !== 'CANDIDATE_INTAKED';
    q('[data-promotion-publish]').disabled = !canAct || !['REVIEW_CONFIRMED', 'PUBLISH_FAILED'].includes(status);
    q('[data-promotion-verify]').disabled = !canAct || !['PUBLISHED_PENDING_QUERY_BACK', 'VERIFY_FAILED'].includes(status);
    q('[data-project-consumption]').disabled = !canAct || status !== 'VERIFIED';
  }

  async function refreshPromotion(itemId = state.item?.item_id) {
    if (!itemId) return;
    try {
      paintPromotion(await request('/items/' + encodeURIComponent(itemId) + '/promotion'));
    } catch (error) {
      if (error.message === 'PROMOTION_NOT_FOUND') paintPromotion({status: 'NOT_STARTED'});
      else paintPromotion({status: 'BLOCKED', error_code: error.message});
    }
  }

  async function promotionAction(action) {
    if (!state.item) return;
    const itemId = encodeURIComponent(state.item.item_id);
    try {
      let payload;
      if (action === 'review') {
        const reviewer = q('[data-formal-reviewer]').value.trim();
        if (!reviewer || !window.confirm('确认对当前已人工确认的知识草稿执行正式审核？不会修改知识内容。')) return;
        payload = await request('/items/' + itemId + '/promotion/review', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({reviewer, review_comment: q('[data-formal-review-comment]').value.trim()}),
        });
      } else if (action === 'publish') {
        const publisher = q('[data-formal-reviewer]').value.trim();
        if (!publisher || !window.confirm('确认发布为正式知识？当前仍只会写入已配置的 NON_PROD Unified Knowledge。')) return;
        payload = await request('/items/' + itemId + '/promotion/publish', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({publisher}),
        });
      } else {
        payload = await request('/items/' + itemId + '/promotion/' + action, {method: 'POST'});
      }
      paintPromotion(payload);
      setMessage('正式知识发布步骤已完成：' + action);
    } catch (error) {
      paintPromotion(state.promotion || {}, '操作被阻止：' + error.message);
      setMessage('正式知识发布步骤失败：' + error.message, true);
    }
  }

  async function verifyPublication() {
    if (!state.item) return;
    try {
      const verified = await request(
        '/items/' + encodeURIComponent(state.item.item_id) + '/promotion/verify',
        {method: 'POST'}
      );
      paintPromotion(verified, '发布结果验证通过');
      setMessage('正式知识发布结果验证通过。下一步：生成检索数据。');
    } catch (error) {
      paintPromotion(state.promotion || {}, '发布结果验证失败：' + error.message);
      setMessage('发布结果验证失败：' + error.message, true);
    }
  }

  async function projectConsumption() {
    if (!state.item) return;
    const candidateId = state.item.candidate_id || state.item.candidate?.candidate_id;
    try {
      if (!candidateId) throw new Error('CANDIDATE_ID_MISSING');
      const response = await fetch(
        '/api/v2/hardware-cases/r1/workbench/consumption/project/' + encodeURIComponent(candidateId),
        {method: 'POST', headers: {'X-Hardware-Case-Role': 'MAINTAINER', Accept: 'application/json'}}
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || 'PROJECTION_UPDATE_FAILED');
      state.consumptionReadyItemId = state.item.item_id;
      paintPromotion(state.promotion || {status: 'VERIFIED'}, '检索数据已生成');
      renderNextStep(state.item, state.promotion || {status: 'VERIFIED'});
      setMessage('检索数据已生成，现在可以进入“正式知识检索”。');
    } catch (error) {
      paintPromotion(state.promotion || {}, '生成检索数据失败：' + error.message);
      setMessage('生成检索数据失败：' + error.message, true);
    }
  }

  async function openItem(itemId) {
    state.scrollY = window.scrollY;
    try {
      const item = await request('/items/' + encodeURIComponent(itemId));
      syncItemIntoBatch(item);
      renderDetail(item);
    } catch (error) {
      setMessage('Case Detail 加载失败：' + error.message, true);
    }
  }

  async function itemAction(action) {
    if (!state.item || state.itemAction.pending) return;
    if (action === 'force-full-run') {
      if (!window.confirm('“强制完整重跑”会绕过已有 AI 分析缓存，成本更高。确认继续？')) {
        return;
      }
    }

    const itemId = state.item.item_id;
    const batchId = state.item.batch_id;
    const retryStage =
      action === 'retry-failed-stage' ? state.item.failed_stage : null;
    const visibleAction =
      action === 'retry-failed-stage'
        ? '重试' + stageDisplay(retryStage)
        : action === 'run-resume'
        ? '继续处理'
        : '强制完整重跑';

    state.itemAction = {pending: true, action, stage: retryStage};
    paintItemActionControls(state.item);
    setItemActionStatus('正在' + visibleAction + '…');
    setMessage('正在执行 ' + visibleAction + '…');

    let stopPolling = startItemPolling(itemId);
    try {
      const item = await request(
        '/items/' + encodeURIComponent(itemId) + '/' + action,
        {method: 'POST'}
      );
      stopPolling();
      stopPolling = () => {};
      syncItemIntoBatch(item);
      await loadBatch(batchId);
      renderDetail(item, {scroll: false});
      const result = displayResult(item);
      const failed = result === 'FAILED';
      const suffix = item.error_code ? ' · ' + item.error_code : '';
      setItemActionStatus(
        visibleAction + ' 完成：' + result + suffix,
        failed
      );
      setMessage(
        visibleAction + ' 完成：' + result + suffix,
        failed
      );
    } catch (error) {
      setItemActionStatus(
        visibleAction + ' 失败：' + error.message,
        true
      );
      setMessage('案例处理失败：' + error.message, true);
      try {
        const current = await request('/items/' + encodeURIComponent(itemId));
        syncItemIntoBatch(current);
        renderDetail(current, {scroll: false, refreshPromotionState: false});
      } catch (_) {
        // Preserve the foreground error if refresh also fails.
      }
    } finally {
      stopPolling();
      state.itemAction = {pending: false, action: null, stage: null};
      paintItemActionControls(state.item);
    }
  }

  async function confirmReviewConflict(button) {
    if (!state.item) return;
    const conflictId = button.dataset.reviewConflict;
    const decisionSource = button.dataset.reviewSource;
    const candidate = candidateForItem(state.item);
    const conflict = (candidate?.conflicts || []).find(
      (value) => value?.conflict_id === conflictId
    );
    const selected = (conflict?.source_values || []).find(
      (value) => value?.source === decisionSource
    );
    const label = reviewSourceLabel(decisionSource);
    const value = selected?.value ?? '—';
    if (!window.confirm('确认采用' + label + '「' + value + '」？确认后无需重新执行 AI 分析。')) {
      return;
    }

    try {
      setMessage('正在保存人工确认…');
      const item = await request(
        '/items/' + encodeURIComponent(state.item.item_id) +
        '/review-conflicts/' + encodeURIComponent(conflictId) + '/resolve',
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            decision_source: decisionSource,
            reviewer: 'MAINTAINER',
          }),
        }
      );
      syncItemIntoBatch(item);
      renderDetail(item);
      setMessage(
        item.result === 'CANDIDATE_READY'
          ? '人工确认已保存，当前知识已进入“待发布”状态。'
          : '人工确认已保存，仍有其他内容需要确认。'
      );
    } catch (error) {
      setMessage('人工确认保存失败：' + error.message, true);
    }
  }

  async function openDebug() {
    if (!state.item) return;
    try {
      const payload = await request(
        '/items/' + encodeURIComponent(state.item.item_id) + '/advanced-debug'
      );
      q('[data-debug-json]').textContent = JSON.stringify(payload, null, 2);
      debugPanel.hidden = false;
      debugPanel.scrollIntoView({block: 'start', behavior: 'smooth'});
    } catch (error) {
      setMessage('诊断信息加载失败：' + error.message, true);
    }
  }

  q('[data-batch-files]').addEventListener('change', (event) => {
    uploadFiles(event.target.files);
    event.target.value = '';
  });
  runBatchButton.addEventListener('click', () => batchAction('run-resume'));
  retryBatchButton.addEventListener('click', () => batchAction('retry-failed-only'));
  historySelect.addEventListener('change', () => {
    state.item = null;
    detail.hidden = true;
    debugPanel.hidden = true;
    loadBatch(historySelect.value);
  });
  resultFilter.addEventListener('change', () => {
    if (state.batch) renderBatch(state.batch);
    updateUrl({});
  });

  tbody.addEventListener('click', (event) => {
    const button = event.target.closest('[data-open-item]');
    if (button) openItem(button.dataset.openItem);
  });

  q('[data-return-batch]').addEventListener('click', () => {
    state.item = null;
    detail.hidden = true;
    debugPanel.hidden = true;
    updateUrl({itemId: ''});
    window.scrollTo({top: state.scrollY, behavior: 'smooth'});
  });
  q('[data-review-conflicts]').addEventListener('click', (event) => {
    const button = event.target.closest('[data-review-conflict]');
    if (button) confirmReviewConflict(button);
  });
  q('[data-human-review-fields]').addEventListener('input', (event) => {
    const field = event.target.closest('[data-human-review-path]');
    if (!field) return;
    try {
      const path = JSON.parse(decodeURIComponent(field.dataset.humanReviewPath));
      setHumanReviewDraftValue(path, field.value, field.dataset.humanReviewType);
      field.classList.remove('hc-error');
    } catch (_) {
      field.classList.add('hc-error');
    }
  });
  q('[data-human-review]').addEventListener('click', (event) => {
    const addButton = event.target.closest('[data-human-review-add-parameter]');
    if (addButton) {
      const parameters = state.reviewDraft?.engineering_context?.key_parameters;
      if (!Array.isArray(parameters)) return;
      parameters.push({
        __review_new: true,
        name: '',
        value: '',
        unit: '',
        evidence_block_ids: [],
      });
      paintHumanReviewFields();
      return;
    }
    const removeButton = event.target.closest('[data-human-review-remove-parameter]');
    if (removeButton) {
      const parameters = state.reviewDraft?.engineering_context?.key_parameters;
      const index = Number(removeButton.dataset.humanReviewRemoveParameter);
      if (!Array.isArray(parameters) || !Number.isInteger(index)) return;
      parameters.splice(index, 1);
      paintHumanReviewFields();
      return;
    }
    const button = event.target.closest('[data-human-review-action]');
    if (button) humanReviewAction(button.dataset.humanReviewAction);
  });
  q('[data-human-review-fields]').addEventListener('change', (event) => {
    const evidence = event.target.closest('[data-human-review-new-evidence]');
    if (!evidence) return;
    const parameters = state.reviewDraft?.engineering_context?.key_parameters;
    const index = Number(evidence.dataset.humanReviewNewEvidence);
    if (!Array.isArray(parameters) || !parameters[index]) return;
    parameters[index].evidence_block_ids = Array.from(evidence.selectedOptions)
      .map((option) => option.value);
  });
  q('[data-item-run]').addEventListener('click', () => itemAction('run-resume'));
  q('[data-item-retry]').addEventListener('click', () => itemAction('retry-failed-stage'));
  q('[data-open-debug]').addEventListener('click', openDebug);
  q('[data-close-debug]').addEventListener('click', () => {
    debugPanel.hidden = true;
    detail.scrollIntoView({block: 'start', behavior: 'smooth'});
  });
  q('[data-force-item]').addEventListener('click', () => itemAction('force-full-run'));
  q('[data-promotion-precheck]').addEventListener('click', () => promotionAction('precheck'));
  q('[data-promotion-intake]').addEventListener('click', () => promotionAction('intake'));
  q('[data-promotion-review]').addEventListener('click', () => promotionAction('review'));
  q('[data-promotion-publish]').addEventListener('click', () => promotionAction('publish'));
  q('[data-promotion-verify]').addEventListener('click', verifyPublication);
  q('[data-project-consumption]').addEventListener('click', projectConsumption);

  const params = new URL(window.location.href).searchParams;
  resultFilter.value = params.get('status') || '';
  const initialItem = params.get('item');
  loadHistory(params.get('batch') || '').then(() => {
    if (initialItem) openItem(initialItem);
  });
})();
