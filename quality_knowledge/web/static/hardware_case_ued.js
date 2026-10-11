/* #575 UED: retain query when switching existing read-only search screens. */
(() => {
  const active = document.querySelector('[data-hc-ued-primary]');
  if (!active) return;
  const source = document.querySelector('[data-search-query]');
  const formalLink = document.querySelector('[data-hc-ued-forward-query]');
  if (source && formalLink) {
    const update = () => {
      const q = source.value.trim();
      formalLink.href = '/p0/hardware-cases/knowledge' + (q ? '?q=' + encodeURIComponent(q) : '');
    };
    source.addEventListener('input', update);
    update();
  }
  const formal = document.querySelector('[data-hc-page="knowledge-consumption"]');
  if (formal) {
    const q = new URLSearchParams(location.search).get('q');
    const input = formal.querySelector('[data-knowledge-text]');
    const form = formal.querySelector('[data-knowledge-form]');
    if (q && input && form) {
      input.value = q;
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    }
  }
})();
