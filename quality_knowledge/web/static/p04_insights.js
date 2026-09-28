(function () {
  'use strict';

  const root = document.querySelector('[data-p04-insights]');
  if (!root) return;

  const api = (root.dataset.apiPrefix || '/api/v2/quality-scenario-insights/v1').replace(/\/$/, '');
  const STORAGE_KEY = 'p04-context';
  const DEFAULTS = {view: 'PRODUCT', selected_object: null, filters: {}, matrix_mode: '', page: 1, page_size: 20};
  const MATRIX_MODES = {
    PRODUCT: ['LIFECYCLE_X_BUSINESS_ACTIVITY'],
    CUSTOMER: ['PRODUCT_X_BUSINESS_ACTIVITY', 'PRODUCT_X_QUALITY_FOCUS'],
    INDUSTRY: ['CUSTOMER_X_PRODUCT_OR_FAMILY', 'BUSINESS_ACTIVITY_X_QUALITY_FOCUS']
  };
  let state = Object.assign({}, DEFAULTS);
  let requestSequence = 0;

  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const arr = value => Array.isArray(value) ? value : [];
  const obj = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};

  async function call(path, options) {
    const response = await fetch(api + path, Object.assign({
      headers: {Accept: 'application/json', 'Content-Type': 'application/json'}
    }, options || {}));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error_code || data.detail || 'HTTP_' + response.status);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function normalizeState(value, partial) {
    const input = obj(value);
    const next = partial ? {} : Object.assign({}, DEFAULTS);
    if (Object.hasOwn(input, 'view')) {
      const view = String(input.view || '').toUpperCase();
      if (['PRODUCT', 'CUSTOMER', 'INDUSTRY'].includes(view)) next.view = view;
    }
    if (Object.hasOwn(input, 'selected_object') || Object.hasOwn(input, 'selected_object_ref')) {
      const selected = input.selected_object;
      const ref = typeof selected === 'string' ? selected : (obj(selected).selector_ref || input.selected_object_ref);
      next.selected_object = ref && /^selector_[a-f0-9]{24}$/.test(String(ref))
        ? {selector_ref: String(ref)} : null;
    }
    if (Object.hasOwn(input, 'filters')) {
      const filters = obj(input.filters);
      next.filters = Object.fromEntries(['lifecycle', 'business_activity', 'quality_focus']
        .filter(key => typeof filters[key] === 'string' && filters[key].length <= 200)
        .map(key => [key, filters[key]]));
    }
    if (Object.hasOwn(input, 'matrix_mode')) {
      const mode = String(input.matrix_mode || '').toUpperCase();
      next.matrix_mode = mode;
    }
    if (Object.hasOwn(input, 'page')) {
      const page = Number(input.page);
      if (Number.isInteger(page) && page > 0 && page <= 100000) next.page = page;
    }
    if (Object.hasOwn(input, 'page_size')) {
      const size = Number(input.page_size);
      if (Number.isInteger(size) && size > 0 && size <= 100) next.page_size = size;
    }
    if (next.view && next.matrix_mode && !MATRIX_MODES[next.view].includes(next.matrix_mode)) next.matrix_mode = '';
    if (!partial) next.matrix_mode = next.matrix_mode || MATRIX_MODES[next.view][0];
    return next;
  }

  function parseObject(raw) {
    if (!raw || raw.length > 4096) return null;
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function readUrlContext() {
    const params = new URLSearchParams(window.location.search);
    const hasPersistentContext = ['view', 'selected_object_ref', 'matrix_mode', 'page']
      .some(key => params.has(key)) || [...params.keys()].some(key => key.startsWith('filter_'));
    const context = {};
    if (params.has('view')) context.view = params.get('view');
    if (params.has('selected_object_ref')) context.selected_object_ref = params.get('selected_object_ref');
    if (params.has('matrix_mode')) context.matrix_mode = params.get('matrix_mode');
    if (params.has('page')) context.page = params.get('page');
    const filters = {};
    ['lifecycle', 'business_activity', 'quality_focus'].forEach(key => {
      if (params.has('filter_' + key)) filters[key] = params.get('filter_' + key);
    });
    if (Object.keys(filters).length) context.filters = filters;
    const returnContext = parseObject(params.get('p04_context'));
    return {
      params,
      hasPersistentContext,
      persistent: hasPersistentContext ? normalizeState(context, true) : null,
      returnContext: returnContext ? normalizeState(returnContext, true) : null,
      reset: params.get('p04_reset') === '1'
    };
  }

  function readSessionState() {
    try {
      const saved = parseObject(sessionStorage.getItem(STORAGE_KEY));
      if (!saved || Number(saved.schema_version) !== 1 || !saved.state) return null;
      return normalizeState(saved.state, true);
    } catch (_) {
      return null;
    }
  }

  function persistSession() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        schema_version: 1,
        state: normalizeState(state, false),
        saved_at: Date.now()
      }));
    } catch (_) {}
  }

  function serializeState(params) {
    const result = new URLSearchParams();
    result.set('view', state.view);
    if (state.selected_object && state.selected_object.selector_ref) {
      result.set('selected_object_ref', state.selected_object.selector_ref);
    }
    Object.entries(state.filters || {}).forEach(([key, value]) => {
      if (value) result.set('filter_' + key, value);
    });
    if (state.matrix_mode) result.set('matrix_mode', state.matrix_mode);
    if (state.page > 1) result.set('page', String(state.page));
    if (params && params.get('return_to')) result.set('return_to', params.get('return_to'));
    if (params && params.get('qs_context')) result.set('qs_context', params.get('qs_context'));
    return result;
  }

  function writeUrl(mode, originalParams) {
    const params = serializeState(originalParams);
    const url = window.location.pathname + (params.toString() ? '?' + params.toString() : '') + window.location.hash;
    if (mode === 'push') window.history.pushState({p04: true}, '', url);
    else window.history.replaceState({p04: true}, '', url);
  }

  function consumeNavigationParams(keys) {
    const target = new URL(window.location.href);
    keys.forEach(k => target.searchParams.delete(k));
    const query=target.searchParams.toString();
    const next=target.pathname+(query?'?'+query:'')+target.hash;
    window.history.replaceState(window.history.state,'',next);
  }

  function restoreContext(options) {
    const settings = Object.assign({consumeOneShot: true, persistUrl: true}, options || {});
    const url = readUrlContext();
    if (url.reset) {
      state = Object.assign({}, DEFAULTS, {matrix_mode: MATRIX_MODES.PRODUCT[0]});
    } else {
      const session = readSessionState();
      // Explicit precedence: defaults < session compatibility < one-shot return < persistent URL.
      state = Object.assign({}, DEFAULTS, session || {}, url.returnContext || {}, url.persistent || {});
      state.filters = Object.assign(
        {},
        obj(session && session.filters),
        obj(url.returnContext && url.returnContext.filters),
        obj(url.persistent && url.persistent.filters)
      );
      state = normalizeState(state, false);
    }
    if (settings.consumeOneShot && url.reset) consumeNavigationParams(['p04_reset','p04_context']);
    else if (settings.consumeOneShot && url.returnContext) consumeNavigationParams(['p04_context']);
    applyStateToDom();
    if (settings.persistUrl) writeUrl('replace', new URLSearchParams(window.location.search));
    persistSession();
    return state;
  }

  function applyStateToDom() {
    root.querySelectorAll('[data-view]').forEach(button => {
      const active = button.dataset.view === state.view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    const modeSelect = root.querySelector('[data-matrix-mode]');
    modeSelect.innerHTML = MATRIX_MODES[state.view].map(mode =>
      '<option value="' + esc(mode) + '">' + esc(mode) + '</option>').join('');
    if (!MATRIX_MODES[state.view].includes(state.matrix_mode)) state.matrix_mode = MATRIX_MODES[state.view][0];
    modeSelect.value = state.matrix_mode;
    root.querySelectorAll('[data-filter]').forEach(select => {
      const key = select.dataset.filter;
      const value = state.filters[key] || '';
      if (value && ![...select.options].some(option => option.value === value)) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }
      select.value = value;
    });
  }

  function currentFilters() {
    return Object.fromEntries([...root.querySelectorAll('[data-filter]')]
      .map(select => [select.dataset.filter, select.value]).filter(([, value]) => value));
  }

  function setOptions(select, items) {
    select.innerHTML = '<option value="">全部对象</option>';
    arr(items).forEach(item => {
      const option = document.createElement('option');
      option.value = item.selector_ref;
      option.textContent = (item.level ? item.level + ' · ' : '') + (item.label || item.selector_ref);
      select.appendChild(option);
    });
    select.value = state.selected_object ? state.selected_object.selector_ref : '';
    if (state.selected_object && select.value !== state.selected_object.selector_ref) state.selected_object = null;
  }

  async function loadSelectors(requestedView) {
    const view = requestedView || state.view;
    const data = await call('/selectors?view=' + encodeURIComponent(view));
    if (view !== state.view || (data.view && data.view !== view)) return false;
    setOptions(root.querySelector('[data-selector]'), data.items);
    if (data.state !== 'NORMAL') root.querySelector('[data-status]').textContent = data.state;
    return true;
  }

  function populateFilters(items) {
    const fields = ['lifecycle', 'business_activity', 'quality_focus'];
    fields.forEach(key => {
      const select = root.querySelector('[data-filter="' + key + '"]');
      const current = state.filters[key] || '';
      const values = [...new Set(arr(items).map(item => item[key]).filter(Boolean))];
      if (current && !values.includes(current)) values.push(current);
      select.innerHTML = '<option value="">全部' + ({
        lifecycle: '生命周期', business_activity: '业务活动', quality_focus: '质量关注'
      }[key]) + '</option>' + values.sort().map(value =>
        '<option value="' + esc(value) + '">' + esc(value) + '</option>').join('');
      select.value = current;
    });
  }

  function showState(name, visible, message) {
    const element = root.querySelector('[data-state="' + name + '"]');
    if (!element) return;
    element.hidden = !visible;
    if (message) {
      const target = element.querySelector('[data-state-message]');
      if (target) target.textContent = message;
    }
  }

  function renderCards(items) {
    root.querySelector('[data-stat-cards]').innerHTML = arr(items).map(item =>
      '<article class="p04-card p04-stat"><span>' + esc(item.metric_key) + '</span><strong>' +
      esc(item.value) + '</strong><small>' + esc(item.state) + '</small></article>').join('');
  }

  function renderMatrix(matrix) {
    const mount = root.querySelector('[data-matrix]');
    if (!matrix || !arr(matrix.cells).length) {
      mount.innerHTML = '<p>当前矩阵暂无可用数据。</p>';
      return;
    }
    const map = new Map(arr(matrix.cells).map(cell => [cell.x_key + '\u0000' + cell.y_key, cell]));
    mount.innerHTML = '<table class="p04-table"><thead><tr><th>' + esc(matrix.x_dimension) + '</th>' +
      arr(matrix.columns).map(column => '<th>' + esc(column) + '</th>').join('') +
      '</tr></thead><tbody>' + arr(matrix.rows).map(row =>
        '<tr><th>' + esc(row) + '</th>' + arr(matrix.columns).map(column => {
          const cell = map.get(row + '\u0000' + column);
          return '<td>' + (cell ? '<button data-drill=\'' + esc(JSON.stringify(cell.drilldown_query)) +
            '\'>' + esc(cell.count) + '</button>' : '—') + '</td>';
        }).join('') + '</tr>').join('') + '</tbody></table>';
  }

  function renderDistributions(data) {
    const mount = root.querySelector('[data-distributions]');
    mount.innerHTML = Object.entries(obj(data)).map(([name, items]) => {
      const max = Math.max(1, ...arr(items).map(item => Number(item.count) || 0));
      return '<section class="p04-dist-group"><h3>' + esc(name) + '</h3>' +
        arr(items).slice(0, 8).map(item =>
          '<div class="p04-dist"><div class="p04-dist-head"><button class="p04-link" data-drill=\'' +
          esc(JSON.stringify(item.drilldown_query)) + '\'>' + esc(item.label) + '</button><b>' +
          esc(item.count) + '</b></div><div class="p04-bar"><i style="width:' +
          Math.max(3, Math.round((Number(item.count) || 0) * 100 / max)) + '%"></i></div></div>'
        ).join('') + '</section>';
    }).join('');
  }

  function detailPath(item) {
    const params = serializeState();
    params.set('return_to', '/p0/quality-scenario-insights');
    params.set('qs_context', 'quality-scenario-workspace');
    params.set('p04_context', JSON.stringify(normalizeState(state, false)));
    return '/p0/quality-scenarios/' + encodeURIComponent(item.scenario_id) + '?' + params.toString();
  }

  function renderList(items) {
    const mount = root.querySelector('[data-scenario-list]');
    root.querySelector('[data-total]').textContent = (state.total || 0) + ' 个场景';
    if (!arr(items).length) {
      mount.innerHTML = '<p>当前条件暂无 PUBLISHED QualityScenario。</p>';
      return;
    }
    mount.innerHTML = '<table class="p04-table"><thead><tr><th>场景</th><th>生命周期</th><th>业务活动</th><th>质量关注</th><th>产品</th><th>客户</th><th></th></tr></thead><tbody>' +
      arr(items).map(item => {
        const href = detailPath(item);
        return '<tr><td><button data-scenario=\'' + esc(JSON.stringify(item)) + '\'>' +
          esc(item.scenario_name) + '</button><small>' + esc(item.scenario_id) +
          '</small></td><td>' + esc(item.lifecycle) + '</td><td>' + esc(item.business_activity) +
          '</td><td>' + esc(item.quality_focus) + '</td><td>' + esc(item.product_context || '—') +
          '</td><td>' + esc(item.customer_context || '—') + '</td><td><a class="p04-link" href="' +
          esc(href) + '" data-p03="' + esc(item.scenario_id) + '">查看 Scenario Detail</a></td></tr>';
      }).join('') + '</tbody></table>';
  }

  function render(data, requestState, sequence) {
    if (sequence !== requestSequence) return;
    if (data.view !== requestState.view) {
      throw new Error('STALE_QUERY_RESPONSE');
    }
    state.result_revision = data.result_revision;
    state.query_context_id = data.query_context_id;
    state.total = data.total;
    showState('ERROR', false);
    showState('PARTIAL_DATA', data.state === 'PARTIAL_DATA', '部分关系未映射，已展示可用场景。');
    showState('NO_RELATION_MAPPING', data.state === 'NO_RELATION_MAPPING');
    showState('EMPTY', data.state === 'EMPTY');
    root.querySelector('[data-context]').textContent = state.view + ' · ' + data.state + ' · revision ' + data.result_revision;
    populateFilters(data.scenario_list);
    renderCards(data.stat_cards);
    renderMatrix(data.matrix);
    renderDistributions(data.distributions);
    renderList(data.scenario_list);
    persistSession();
    if (window.OverallNavigation) window.OverallNavigation.restoreNow({ready: true});
  }

  function readControls() {
    state.filters = currentFilters();
    const selector = root.querySelector('[data-selector]');
    state.selected_object = selector.value ? {selector_ref: selector.value} : null;
    state.matrix_mode = root.querySelector('[data-matrix-mode]').value;
  }

  async function query(options) {
    const settings = Object.assign({history: 'none'}, options || {});
    readControls();
    state.matrix_mode = state.matrix_mode || MATRIX_MODES[state.view][0];
    if (settings.history !== 'none') writeUrl(settings.history, new URLSearchParams(window.location.search));
    persistSession();
    const sequence = ++requestSequence;
    const requestState = normalizeState(state, false);
    root.querySelector('[data-status]').textContent = '正在加载…';
    try {
      const data = await call('/query', {method: 'POST', body: JSON.stringify(requestState)});
      if (sequence !== requestSequence) return;
      render(data, requestState, sequence);
      root.querySelector('[data-status]').textContent = '已更新';
    } catch (error) {
      if (sequence !== requestSequence) return;
      showState('ERROR', true, error.message);
      root.querySelector('[data-status]').textContent = '读取失败';
    }
  }

  function openScenario(item) {
    const drawer = document.querySelector('[data-drawer]');
    document.querySelector('[data-drawer-title]').textContent = item.scenario_name || item.scenario_id;
    drawer.querySelector('[data-drawer-body]').innerHTML =
      '<dl><dt>当前视角</dt><dd>' + esc(state.view) + '</dd><dt>生命周期</dt><dd>' +
      esc(item.lifecycle) + '</dd><dt>业务活动</dt><dd>' + esc(item.business_activity) +
      '</dd><dt>质量关注</dt><dd>' + esc(item.quality_focus) + '</dd><dt>触发条件摘要</dt><dd>' +
      esc(item.trigger_summary) + '</dd><dt>失效模式摘要</dt><dd>' +
      esc(item.failure_mode_summary) + '</dd><dt>来源问题数</dt><dd>' +
      esc(item.source_problem_count) + '</dd><dt>产品 / 客户 / 行业</dt><dd>' +
      esc(item.product_context || '—') + ' / ' + esc(item.customer_context || '—') + ' / ' +
      esc(item.industry_context || '—') + '</dd><dt>正式链路</dt><dd><a class="p04-link" href="' +
      esc(detailPath(item)) + '">进入 Scenario Detail → Evidence → Source Trace</a></dd></dl>';
    drawer.hidden = false;
    document.querySelector('[data-backdrop]').hidden = false;
  }

  function closeDrawer() {
    document.querySelector('[data-drawer]').hidden = true;
    document.querySelector('[data-backdrop]').hidden = true;
  }

  root.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', async () => {
    requestSequence += 1;
    state.view = button.dataset.view;
    state.selected_object = null;
    state.filters = {};
    state.page = 1;
    state.matrix_mode = MATRIX_MODES[state.view][0];
    applyStateToDom();
    try {
      if (await loadSelectors(state.view)) await query({history: 'push'});
    } catch (error) {
      showState('ERROR', true, error.message);
    }
  }));

  root.querySelector('[data-apply]').addEventListener('click', () => query({history: 'push'}));
  root.querySelector('[data-reset]').addEventListener('click', () => {
    requestSequence += 1;
    state = Object.assign({}, DEFAULTS, {matrix_mode: MATRIX_MODES.PRODUCT[0]});
    applyStateToDom();
    const params = new URLSearchParams(window.location.search);
    params.set('p04_reset', '1');
    params.delete('p04_context');
    window.history.replaceState({p04: true}, '', window.location.pathname + '?' + params.toString() + window.location.hash);
    restoreContext({consumeOneShot: true, persistUrl: true});
    loadSelectors(state.view).then(current => current && query({history: 'none'})).catch(error => showState('ERROR', true, error.message));
  });
  root.querySelector('[data-retry]').addEventListener('click', () => query());
  root.querySelector('[data-matrix-mode]').addEventListener('change', () => query({history: 'push'}));
  document.querySelector('[data-close]').addEventListener('click', closeDrawer);
  document.querySelector('[data-backdrop]').addEventListener('click', closeDrawer);

  root.addEventListener('click', event => {
    const drill = event.target.closest('[data-drill]');
    if (drill) {
      try {
        const payload = JSON.parse(drill.dataset.drill);
        call('/drilldown', {method: 'POST', body: JSON.stringify(payload)}).then(result => {
          if (result.status === 'REVISION_CHANGED') {
            showState('ERROR', true, '数据版本已变化，请刷新后重试。');
            return;
          }
          if (result.scenario_list && result.scenario_list[0]) openScenario(result.scenario_list[0]);
        }).catch(error => showState('ERROR', true, error.message));
      } catch (_) {}
      return;
    }
    const scenario = event.target.closest('[data-scenario]');
    if (scenario) {
      try { openScenario(JSON.parse(scenario.dataset.scenario)); } catch (_) {}
    }
  });

  window.addEventListener('popstate', () => {
    requestSequence += 1;
    restoreContext({consumeOneShot: true, persistUrl: false});
    loadSelectors(state.view).then(current => current && query({history: 'none'})).catch(error => showState('ERROR', true, error.message));
  });

  restoreContext();
  loadSelectors().then(() => query({history: 'none'})).catch(error => showState('ERROR', true, error.message));
})();
