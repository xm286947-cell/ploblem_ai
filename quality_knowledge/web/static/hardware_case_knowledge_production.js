(() => {
  const root = document.querySelector('[data-r1-workbench]');
  if (!root) return;

  const api = root.dataset.api;
  const headers = {'X-Hardware-Case-Role': 'MAINTAINER'};
  const state = {
    batch: null,
    item: null,
    scrollY: 0,
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
    if (['REVIEW', 'QUEUED', 'WAITING', 'RUNTIME_BLOCKED', 'DEPENDENCY_BLOCKED'].includes(status)) return 'warn';
    if (['FAILED', 'PARSE_FAILED', 'STAGE_A_FAILED', 'STAGE_B_FAILED', 'GATE_FAILED'].includes(status)) return 'bad';
    return '';
  }

  function statusPill(value) {
    const display = value || '—';
    return '<span class="hc-status ' + statusClass(display) + '">' +
      escapeHtml(display) + '</span>';
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
      return 'Provider Timeout：使用 Retry Failed Stage，仅重试失败阶段；不要 Force Full Run。';
    }
    if (item.error_code) {
      return '失败原因：' + item.error_code + '。可先查看 Advanced Debug，再按失败阶段选择性重试。';
    }
    return '';
  }

  async function refreshBatchSnapshot(batchId) {
    const batch = await request('/batches/' + encodeURIComponent(batchId));
    renderBatch(batch);
    historySelect.value = batchId;
    return batch;
  }

  function startBatchPolling(batchId) {
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
    const timer = window.setInterval(tick, 1500);
    tick();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }

  function renderBatch(batch) {
    state.batch = batch;
    q('[data-batch-ref]').textContent =
      batch ? batch.batch_id + ' · ' + (batch.status || '—') : '尚未选择 Batch。';
    renderSummary(batch?.summary || {});
    runBatchButton.disabled = !batch;
    retryBatchButton.disabled = !batch || !(batch.summary?.FAILED > 0);

    if (!batch) {
      tbody.innerHTML = '<tr><td colspan="11" class="hc-empty">暂无 Batch。</td></tr>';
      return;
    }

    const filter = resultFilter.value;
    const items = (batch.items || []).filter((item) => {
      if (!filter) return true;
      return displayResult(item) === filter || item.orchestration_status === filter;
    });

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="11" class="hc-empty">当前筛选无 Case。</td></tr>';
      return;
    }

    tbody.innerHTML = items.map((item) => {
      const result = displayResult(item);
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
          <td>${statusPill(result)}</td>
          <td><small class="${item.error_code ? 'hc-error' : ''}">${escapeHtml(item.error_code || '—')}</small></td>
          <td>${Number(item.provider_calls || 0)}</td>
          <td>${Number(item.duration_ms || 0).toLocaleString()} ms</td>
          <td><small>${escapeHtml(item.updated_at || '—')}</small></td>
          <td>
            <button class="hc-button" type="button"
              data-open-item="${escapeHtml(item.item_id)}">打开结果</button>
          </td>
        </tr>
      `;
    }).join('');
  }

  async function loadHistory(preferredBatch = '') {
    try {
      const payload = await request('/batches?limit=50');
      const batches = payload.items || [];
      historySelect.innerHTML = '<option value="">Batch History</option>' +
        batches.map((batch) =>
          '<option value="' + escapeHtml(batch.batch_id) + '">' +
          escapeHtml(batch.batch_id + ' · ' + batch.status) +
          '</option>'
        ).join('');
      const target = preferredBatch || new URL(window.location.href).searchParams.get('batch') ||
        batches[0]?.batch_id || '';
      if (target) {
        historySelect.value = target;
        await loadBatch(target);
      } else {
        renderBatch(null);
        setMessage('暂无 Batch。请批量上传 Word。');
      }
    } catch (error) {
      setMessage('Batch History 加载失败：' + error.message, true);
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
        'Batch 已载入：' + batchId +
        (batch.status === 'PARTIAL_FAILURE' ? '（存在部分失败，成功 Case 已保留）' : '')
      );
    } catch (error) {
      setMessage('Batch 加载失败：' + error.message, true);
    }
  }

  async function uploadFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const data = new FormData();
    files.forEach((file) => data.append('files', file, file.name));
    setMessage('正在创建 Batch 并执行文件级 Parse / Source Binding Precheck…');
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
        'Batch 已创建。合法 Case 可继续 Run / Resume；单文件失败不会回滚其他 Case。'
      );
    } catch (error) {
      setMessage('批量上传失败：' + error.message, true);
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
        ? '正在执行 Retry Failed Only…'
        : '正在执行 Batch Run / Resume…');
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
        ? 'Batch Run / Resume 完成。'
        : 'Retry Failed Only 完成，选择 ' + selected + ' 个 Failed Case。');
    } catch (error) {
      setMessage('Batch 操作失败：' + error.message, true);
    }
  }

  function renderDetail(item) {
    state.item = item;
    detail.hidden = false;
    debugPanel.hidden = true;
    q('[data-case-heading]').textContent =
      (item.business_case_id || 'ID 待确认') + ' · ' + (item.source_file || '—');
    q('[data-detail-batch]').textContent = item.batch_id || '—';
    q('[data-detail-status]').innerHTML = statusPill(displayResult(item));
    q('[data-detail-failed-stage]').textContent = item.failed_stage || '—';
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
      ['Parse', item.parse],
      ['Stage A', item.stage_a],
      ['Stage B', item.stage_b],
      ['Gate', item.gate],
      ['Result', displayResult(item)],
    ].map(([name, value]) =>
      '<span><b>' + escapeHtml(name) + '</b>' + statusPill(value) + '</span>'
    ).join('');
    q('[data-candidate-preview]').textContent =
      JSON.stringify(item.candidate || null, null, 2);
    q('[data-evidence-summary]').textContent =
      JSON.stringify(item.evidence_validation || null, null, 2);
    q('[data-item-retry]').disabled =
      !(item.result === 'FAILED' && ['STAGE_A', 'STAGE_B'].includes(item.failed_stage));
    updateUrl({batchId: item.batch_id, itemId: item.item_id});
    detail.scrollIntoView({block: 'start', behavior: 'smooth'});
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
    if (!state.item) return;
    if (action === 'force-full-run') {
      if (!window.confirm('Force Full Run 将绕过 Stage A/B Cache，成本更高。继续？')) {
        return;
      }
    }
    try {
      setMessage('正在执行 ' + action + '…');
      const batchId = state.item.batch_id;
      const stopPolling = startBatchPolling(batchId);
      let item;
      try {
        item = await request(
          '/items/' + encodeURIComponent(state.item.item_id) + '/' + action,
          {method: 'POST'}
        );
      } finally {
        stopPolling();
      }
      syncItemIntoBatch(item);
      const itemId = item.item_id;
      await loadBatch(item.batch_id);
      await openItem(itemId);
      setMessage('Case 操作完成：' + action);
    } catch (error) {
      setMessage('Case 操作失败：' + error.message, true);
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
      setMessage('Advanced Debug 加载失败：' + error.message, true);
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
  q('[data-item-run]').addEventListener('click', () => itemAction('run-resume'));
  q('[data-item-retry]').addEventListener('click', () => itemAction('retry-failed-stage'));
  q('[data-open-debug]').addEventListener('click', openDebug);
  q('[data-close-debug]').addEventListener('click', () => {
    debugPanel.hidden = true;
    detail.scrollIntoView({block: 'start', behavior: 'smooth'});
  });
  q('[data-force-item]').addEventListener('click', () => itemAction('force-full-run'));

  const params = new URL(window.location.href).searchParams;
  resultFilter.value = params.get('status') || '';
  const initialItem = params.get('item');
  loadHistory(params.get('batch') || '').then(() => {
    if (initialItem) openItem(initialItem);
  });
})();
