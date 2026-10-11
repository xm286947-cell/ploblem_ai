/* #575 UED: retain query when switching existing read-only search screens. */
(() => {
  const active = document.querySelector('[data-hc-ued-primary]');
  if (!active) return;
  const source = document.querySelector('[data-search-query]');
  const formalInput = document.querySelector('[data-knowledge-text]');
  const casesLink = document.querySelector('[data-hc-ued-back-query]');
  if (formalInput && casesLink) {
    const refreshCaseLink = () => {
      const query = formalInput.value.trim();
      casesLink.href = '/p0/hardware-cases/search' + (query ? '?q=' + encodeURIComponent(query) : '');
    };
    formalInput.addEventListener('input', refreshCaseLink);
    refreshCaseLink();
  }
  const formalLink = document.querySelector('[data-hc-ued-forward-query]');
  if (source && formalLink) {
    const update = () => {
      const q = source.value.trim();
      formalLink.href = '/p0/hardware-cases/knowledge' + (q ? '?q=' + encodeURIComponent(q) : '');
    };
    source.addEventListener('input', update);
    update();
  }
})();
