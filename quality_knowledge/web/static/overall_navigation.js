(function () {
  'use strict';

  const STORAGE_KEY = 'overall-return-context-v1';
  const FIELD_KEY = /^[A-Za-z0-9_.:-]{1,80}$/;
  let pendingRestore = null;
  let drawer = null;
  let returnFocus = null;
  let restored = false;

  function safeJson(raw) {
    if (!raw || raw.length > 4096) return null;
    try {
      const value = JSON.parse(raw);
      if (!value || typeof value !== 'object' || Array.isArray(value) || value.contract !== 'overall-return-context/v1') return null;
      const scroll = Number(value.scroll_y || 0);
      if (!Number.isInteger(scroll) || scroll < 0 || scroll > 10000000) return null;
      const fields = value.fields && typeof value.fields === 'object' && !Array.isArray(value.fields) ? value.fields : {};
      const clean = {};
      Object.entries(fields).slice(0, 32).forEach(([key, item]) => {
        if (FIELD_KEY.test(key) && typeof item === 'string' && item.length <= 300) clean[key] = item;
      });
      return {contract: value.contract, scroll_y: scroll, fields: clean,
        focus_id: typeof value.focus_id === 'string' ? value.focus_id.slice(0, 300) : '',
        selected_object: typeof value.selected_object === 'string' ? value.selected_object.slice(0, 300) : '',
        tab: typeof value.tab === 'string' ? value.tab.slice(0, 300) : ''};
    } catch (_) { return null; }
  }

  function capture() {
    const fields = {};
    document.querySelectorAll('[data-overall-state-field]').forEach(element => {
      const key = element.dataset.overallStateField || '';
      if (!FIELD_KEY.test(key) || element.disabled || element.type === 'password' || element.type === 'file') return;
      const value = element.type === 'checkbox' ? (element.checked ? 'true' : 'false') : String(element.value || '');
      if (value.length <= 300) fields[key] = value;
    });
    const active = document.activeElement;
    const selected = document.querySelector('[data-overall-selected-object][aria-current="true"], [data-overall-selected-object][data-selected="true"]');
    const activeTab = document.querySelector('[data-overall-tab][aria-selected="true"], [data-overall-tab].active');
    return {
      contract: 'overall-return-context/v1',
      scroll_y: Math.max(0, Math.min(10000000, Math.round(window.scrollY || 0))),
      focus_id: active && active.id ? active.id.slice(0, 300) : '',
      selected_object: selected ? String(selected.dataset.overallSelectedObject || '').slice(0, 300) : '',
      tab: activeTab ? String(activeTab.dataset.overallTab || '').slice(0, 300) : '',
      fields
    };
  }

  function restoreNow() {
    if (!pendingRestore || restored) return;
    Object.entries(pendingRestore.fields || {}).forEach(([key, value]) => {
      const element = [...document.querySelectorAll('[data-overall-state-field]')]
        .find(candidate => candidate.dataset.overallStateField === key);
      if (!element || element.disabled) return;
      if (element.type === 'checkbox') element.checked = value === 'true';
      else if (element.tagName === 'SELECT' && ![...element.options].some(option => option.value === value)) return;
      else element.value = value;
      element.dispatchEvent(new Event('change', {bubbles: true}));
    });
    if (pendingRestore.selected_object) {
      const selected = [...document.querySelectorAll('[data-overall-selected-object]')]
        .find(candidate => candidate.dataset.overallSelectedObject === pendingRestore.selected_object);
      if (selected && selected.getAttribute('aria-current') !== 'true' && selected.dataset.selected !== 'true') selected.click();
    }
    if (pendingRestore.tab) {
      const tab = [...document.querySelectorAll('[data-overall-tab]')]
        .find(candidate => candidate.dataset.overallTab === pendingRestore.tab);
      if (tab && tab.getAttribute('aria-selected') !== 'true' && !tab.classList.contains('active')) tab.click();
    }
    restored = true;
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      window.scrollTo({top: pendingRestore.scroll_y || 0, left: 0, behavior: 'auto'});
      const focus = pendingRestore.focus_id && document.getElementById(pendingRestore.focus_id);
      if (focus && typeof focus.focus === 'function') focus.focus({preventScroll: true});
    }));
  }

  function ensureDrawer() {
    if (drawer) return drawer;
    const backdrop = document.createElement('div');
    backdrop.className = 'overall-navigation-backdrop';
    backdrop.hidden = true;
    backdrop.setAttribute('data-overall-evidence-backdrop', '');
    const panel = document.createElement('aside');
    panel.className = 'overall-evidence-drawer';
    panel.hidden = true;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', '统一 Evidence / Source Viewer');
    panel.innerHTML = '<header><div><h2>Evidence / Source Viewer</h2><p>生产方公共 Evidence 契约</p></div><button type="button" data-overall-evidence-close aria-label="关闭">×</button></header><iframe title="Evidence / Source Viewer"></iframe>';
    document.body.append(backdrop, panel);
    const close = () => closeDrawer();
    backdrop.addEventListener('click', close);
    panel.querySelector('[data-overall-evidence-close]').addEventListener('click', close);
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); }
    });
    drawer = {backdrop, panel, frame: panel.querySelector('iframe')};
    return drawer;
  }

  function openDrawer(anchor) {
    const parts = ensureDrawer();
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin) return false;
    url.searchParams.set('presentation', 'drawer');
    url.searchParams.set('return_to', window.location.pathname + window.location.search + window.location.hash);
    url.searchParams.set('return_state', JSON.stringify(capture()));
    returnFocus = anchor;
    parts.frame.src = url.pathname + url.search + url.hash;
    parts.backdrop.hidden = false;
    parts.panel.hidden = false;
    document.body.classList.add('overall-navigation-open');
    parts.panel.querySelector('[data-overall-evidence-close]').focus();
    return true;
  }

  function closeDrawer() {
    if (!drawer) return;
    drawer.frame.src = 'about:blank';
    drawer.panel.hidden = true;
    drawer.backdrop.hidden = true;
    document.body.classList.remove('overall-navigation-open');
    if (returnFocus && returnFocus.isConnected) returnFocus.focus();
    returnFocus = null;
  }

  function preserveReturnState(anchor) {
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    const preserveMode = anchor.dataset.overallPreserveContext || '';
    const isReturnFlow = url.searchParams.has('return_to') || preserveMode || url.pathname.startsWith('/p0/quality-scenario-sources/');
    if (!isReturnFlow) return;
    if (url.searchParams.has('overall_return_state')) return;
    const current = new URL(window.location.href).searchParams.get('overall_return_state');
    const state = safeJson(current) || capture();
    url.searchParams.set('overall_return_state', JSON.stringify(state));
    if (preserveMode !== 'state-only' && !url.searchParams.has('return_to')) {
      url.searchParams.set('return_to', window.location.pathname + window.location.search + window.location.hash);
    }
    anchor.href = url.pathname + url.search + url.hash;
  }

  document.addEventListener('click', event => {
    const anchor = event.target.closest && event.target.closest('a[href]');
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (url.pathname === '/p0/overall/evidence') {
      event.preventDefault();
      openDrawer(anchor);
      return;
    }
    preserveReturnState(anchor);
  });

  const current = new URL(window.location.href);
  const rawRestore = current.searchParams.get('overall_return_state');
  pendingRestore = safeJson(rawRestore);
  if (rawRestore) {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(pendingRestore)); } catch (_) {}
    current.searchParams.delete('overall_return_state');
    history.replaceState(history.state, '', current.pathname + (current.search ? current.search : '') + current.hash);
  }
  if (!pendingRestore) {
    try { pendingRestore = safeJson(sessionStorage.getItem(STORAGE_KEY)); sessionStorage.removeItem(STORAGE_KEY); } catch (_) {}
  }
  window.OverallNavigation = {capture, restoreNow, closeDrawer};
  window.addEventListener('load', () => window.setTimeout(restoreNow, 80), {once: true});
})();
