(function () {
  'use strict';
  const root = document.querySelector('[data-major-production]');
  if (!root) return;
  const api = (window.P0_MAJOR_API || root.dataset.apiPrefix || '/api/v2').replace(/\/$/, '') + '/major-production';
  const state = { caseId: null, eventId: null };
  const message = root.querySelector('[data-major-message]');
  const say = (text, error) => {
    message.textContent = text;
    message.className = 'major-message' + (error ? ' error' : '');
  };
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  async function read(response) {
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'REQUEST_FAILED');
    return data;
  }

  const excelForm = root.querySelector('[data-major-excel]');
  const excelPreview = root.querySelector('[data-major-excel-preview]');
  if (excelForm) {
    excelForm.addEventListener('submit', async event => {
      event.preventDefault();
      try {
        say('正在预检 Excel 并加载 Mapping…');
        const data = await read(await fetch(api + '/excel/preview', {
          method: 'POST', body: new FormData(event.currentTarget)
        }));
        excelPreview.hidden = false;
        const rows = (data.rows || []).slice(0, 20).map(row => {
          const match = row.report_match || {};
          const resolution = row.event_resolution || {};
          return '<tr><td>' + esc(row.excel_row) + '</td><td>' + esc((row.itrs || []).join(' / ')) +
            '</td><td>' + esc(row.title) + '</td><td>' +
            esc(row.completeness && row.completeness.importable ? 'IMPORTABLE' : 'BLOCKED') +
            '</td><td>' + esc(match.report_filename || '') + '</td><td>' +
            esc(match.match_status || match.match_type || 'NOT_FOUND') +
            (resolution.standard_itr ? ' · ' + esc(resolution.standard_itr) : '') + '</td></tr>';
        }).join('');
        const mapping = Object.entries((data.mapping || {}).fields || {}).map(([key, value]) =>
          '<li>' + esc(key) + ' ← ' + esc(value) + '</li>'
        ).join('');
        const hasUnsafeRows = (data.rows || []).some(row => {
          const match = row.report_match || {};
          const resolution = row.event_resolution || {};
          return match.match_status === 'AMBIGUOUS' ||
            (match.match_status === 'MATCHED' && (row.itrs || []).length > 1 && resolution.status !== 'MATCHED');
        });
        excelPreview.innerHTML = '<article class="major-candidate"><h3>Preview / Mapping</h3><p>Batch ' +
          esc(data.batch_id) + ' · ' + esc(data.total) + ' rows · ' + esc(data.importable) +
          ' importable</p><details><summary>字段 Mapping</summary><ul>' + mapping +
          '</ul></details><div class="table-wrap"><table><thead><tr><th>Row</th><th>ITR</th>' +
          '<th>Title</th><th>Status</th><th>复盘报告文件名</th><th>匹配 / Event</th></tr></thead><tbody>' +
          rows + '</tbody></table></div><button class="case-button primary" data-major-excel-confirm ' +
          (hasUnsafeRows ? 'disabled title="存在歧义或未解决的 Event 绑定，需先处理"' : '') +
          '>确认导入</button></article>';
        const confirmButton = excelPreview.querySelector('[data-major-excel-confirm]');
        if (!hasUnsafeRows) confirmButton.addEventListener('click', async () => {
          try {
            say('正在确认并导入 Excel…');
            const form = new FormData();
            form.append('batch_id', data.batch_id);
            const committed = await read(await fetch(api + '/excel/confirm', { method: 'POST', body: form }));
            const ids = (committed.result && committed.result.case_ids) || [];
            if (!ids.length) throw new Error('MAJOR_EXCEL_NO_IMPORTED_CASE');
            state.caseId = ids[0];
            const detail = await read(await fetch(api + '/cases/' + encodeURIComponent(state.caseId)));
            const events = detail.events || [];
            state.eventId = events.length === 1 ? events[0].event_id : null;
            root.querySelector('[data-major-workflow]').hidden = false;
            root.querySelector('[data-major-identity]').textContent = 'Excel Batch ' + data.batch_id +
              ' · Case ' + state.caseId + (events.length ? ' · ' + events.length + ' Event(s)' : '');
            root.querySelector('[data-major-state]').textContent = 'IMPORTED';
            say('Excel 导入完成，已进入现有 AI Analysis → Human Review → Publish 链路。');
          } catch (error) { say(error.message, true); }
        });
        say(hasUnsafeRows ? '预检发现歧义或 Event 未唯一绑定；已阻止确认导入。' : '预检完成。请核对 Mapping、复盘报告匹配与行级结果后确认导入。', hasUnsafeRows);
      } catch (error) { say(error.message, true); }
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
      root.querySelector('[data-major-identity]').textContent = 'ITR ' + data.event.standard_itr +
        ' · Source ' + data.document.original_filename + ' · Version ' + data.document.version_no;
      say('Major Source 已入库，已建立 Event 与证据版本。');
    } catch (error) { say(error.message, true); }
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
})();
