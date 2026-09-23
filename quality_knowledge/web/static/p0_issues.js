(function () {
  'use strict';
  const root = document.querySelector('[data-p0-issues]');
  if (!root) return;
  const api = (window.P0_ISSUES_API || root.dataset.apiPrefix || '/api/v2').replace(/\/$/, '');
  const form = root.querySelector('[data-issues-filter]');
  const state = { page: 1, pageSize: 20, selected: new Set(), running: false, runtimeReady: false };
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const obj = value => value && typeof value === 'object' ? value : {};
  const arr = value => Array.isArray(value) ? value : [];

  function url(path, params) {
    const query = new URLSearchParams(Object.entries(params || {}).filter(([, value]) => value !== '' && value != null));
    return api + path + (query.toString() ? '?' + query : '');
  }
  async function get(path, params) {
    const response = await fetch(url(path, params), {headers:{Accept:'application/json'}});
    if (!response.ok) throw new Error('HTTP_' + response.status);
    return response.json();
  }
  async function post(path, payload) {
    const response = await fetch(api + path, {method:'POST', headers:{'Content-Type':'application/json', Accept:'application/json'}, body:JSON.stringify(payload)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'HTTP_' + response.status);
    return data;
  }
  function optionList(select, items) {
    arr(items).forEach(item => {
      const source = typeof item === 'string' ? {value:item,label:item} : obj(item);
      const value = source.product_code || source.code || source.value || '';
      const label = source.product_name || source.label_zh || source.label || value;
      if (!value) return;
      const option = document.createElement('option'); option.value = value; option.textContent = label; select.appendChild(option);
    });
  }
  function filters() { return Object.fromEntries([...form.elements].filter(item => item.name).map(item => [item.name, item.value])); }
  function queryString() { const query = new URLSearchParams(filters()); query.set('page_size', String(state.pageSize)); return query.toString(); }
  function setState(name, visible) { const element = root.querySelector('[data-state="' + name + '"]'); if (element) element.hidden = !visible; }
  function updateSelection() {
    const checks = [...root.querySelectorAll('[data-select-issue]')];
    checks.forEach(check => { check.checked = state.selected.has(check.value); });
    const selectAll = root.querySelector('[data-select-all]');
    selectAll.checked = checks.length > 0 && checks.every(check => check.checked);
    selectAll.indeterminate = checks.some(check => check.checked) && !selectAll.checked;
    root.querySelector('[data-selected-count]').textContent = '已选择 ' + state.selected.size + ' 条';
    root.querySelector('[data-batch-analyze]').disabled = state.running || state.selected.size === 0 || !state.runtimeReady;
  }
  async function meta() {
    const [products, facets, runtime] = await Promise.all([get('/products'), get('/issues/facets'), get('/analysis-runtime/status')]);
    optionList(form.querySelector('[data-products]'), obj(products).items || []);
    optionList(form.querySelector('[data-months]'), obj(facets).months || []);
    const runtimeElement = root.querySelector('[data-analysis-runtime]');
    runtimeElement.textContent = runtime.ready ? 'AI 服务已就绪' : 'AI 服务未就绪：' + arr(runtime.errors).join('；');
    state.runtimeReady = Boolean(runtime.ready);
    updateSelection();
  }
  async function load() {
    root.querySelector('[data-status]').textContent = '正在加载…';
    try {
      const data = await get('/issues', Object.assign(filters(), {page:state.page, page_size:state.pageSize}));
      const items = arr(data.items), total = Number(data.total || 0), pages = Math.max(1, Math.ceil(total / state.pageSize));
      setState('error', false); setState('empty', !total);
      root.querySelector('[data-total]').textContent = '共 ' + total + ' 条问题';
      root.querySelector('[data-page-scope]').textContent = '当前第 ' + state.page + ' / ' + pages + ' 页';
      root.querySelector('[data-issue-rows]').innerHTML = items.length ? items.map(item => {
        const issue = obj(item), id = String(issue.knowledge_id || ''), qs = queryString();
        return '<tr data-issue-id="' + esc(id) + '"><td class="p0-check-column"><input type="checkbox" data-select-issue value="' + esc(id) + '" aria-label="选择问题 ' + esc(issue.business_issue_id || id) + '"></td><td><a class="p0-issue-id" href="/p0/issues/' + encodeURIComponent(id) + '?' + qs + '">' + esc(issue.business_issue_id || id) + '</a><span class="p0-summary">' + esc(issue.title || issue.description || issue.business_issue_id || '暂无摘要') + '</span></td><td><b>' + esc(issue.business_type || '-') + '</b><small class="p0-muted">' + esc(issue.product_name || issue.product || issue.product_code || '-') + '</small></td><td>' + esc(issue.month || '-') + '</td><td><span class="p0-badge">' + esc(issue.severity || '-') + '</span></td><td><span class="p0-badge">' + esc(issue.analysis_status || 'NOT_ANALYZED') + '</span></td><td>' + esc(issue.updated_at || '-') + '</td></tr>';
      }).join('') : '<tr><td colspan="7" class="p0-loading">暂无符合条件的问题。</td></tr>';
      const nav = root.querySelector('[data-pagination]'); nav.hidden = !total;
      nav.querySelector('[data-page-label]').textContent = state.page + ' / ' + pages;
      nav.querySelector('[data-prev]').disabled = state.page <= 1; nav.querySelector('[data-next]').disabled = state.page >= pages;
      root.querySelector('[data-status]').textContent = '已更新'; updateSelection();
    } catch (error) {
      setState('error', true); root.querySelector('[data-error-message]').textContent = '读取失败：' + error.message; root.querySelector('[data-status]').textContent = '';
    }
  }
  async function runBatch() {
    if (!state.selected.size || state.running) return;
    state.running = true; updateSelection();
    const result = root.querySelector('[data-batch-result]');
    result.textContent = '正在分析 ' + state.selected.size + ' 个问题，请勿重复提交…';
    try {
      const data = await post('/issues/batch-analysis', {knowledge_ids:[...state.selected], concurrency:Number(root.querySelector('[data-concurrency]').value || 2), force:false});
      result.textContent = '分析完成：成功 ' + data.succeeded + '，部分完成 ' + (data.partial || 0) + '，失败 ' + data.failed + '。';
      state.selected.clear(); await load();
    } catch (error) { result.textContent = '批量分析失败：' + error.message; }
    finally { state.running = false; updateSelection(); }
  }

  form.addEventListener('submit', event => { event.preventDefault(); state.page = 1; state.selected.clear(); load(); });
  root.querySelector('[data-reset]').addEventListener('click', () => { form.reset(); state.page = 1; state.selected.clear(); load(); });
  root.querySelector('[data-retry]').addEventListener('click', load);
  root.querySelector('[data-prev]').addEventListener('click', () => { state.page--; load(); });
  root.querySelector('[data-next]').addEventListener('click', () => { state.page++; load(); });
  root.querySelector('[data-select-all]').addEventListener('change', event => {
    root.querySelectorAll('[data-select-issue]').forEach(check => event.target.checked ? state.selected.add(check.value) : state.selected.delete(check.value)); updateSelection();
  });
  root.querySelector('[data-issue-rows]').addEventListener('change', event => {
    if (!event.target.matches('[data-select-issue]')) return;
    event.target.checked ? state.selected.add(event.target.value) : state.selected.delete(event.target.value); updateSelection();
  });
  root.querySelector('[data-batch-analyze]').addEventListener('click', runBatch);
  meta().catch(() => {}).finally(load);
})();
