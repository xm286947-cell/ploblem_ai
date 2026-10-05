(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const observer = new MutationObserver(() => {
    const detail = document.querySelector('[data-knowledge-detail]:not([hidden])');
    if (!detail) return;
    const section = Array.from(detail.querySelectorAll('.hc-knowledge-detail-section')).find(node => node.querySelector('h3')?.textContent.trim() === 'Evidence refs');
    if (!section || section.dataset.e2eBound === '1') return;
    section.dataset.e2eBound = '1';
    const caseId = document.querySelector('[data-detail-subtitle]')?.textContent.split('·').pop()?.trim();
    const refs = Array.from(section.querySelectorAll('code')).map(code => code.textContent.trim()).filter(Boolean);
    const controls = document.createElement('div');
    controls.className = 'hc-knowledge-detail-item';
    controls.innerHTML = refs.length ? refs.map(id => `<div><code>${esc(id)}</code> <button type="button" class="hc-button" data-e2e-preview="${esc(id)}">定位 Evidence</button> <button type="button" class="hc-button" data-e2e-source="${esc(id)}">打开原始 Word</button></div>`).join('') : '—';
    const output = document.createElement('div');
    output.dataset.e2eEvidenceOutput = '';
    section.append(controls, output);
    controls.addEventListener('click', async event => {
      const button = event.target.closest('[data-e2e-preview], [data-e2e-source]');
      if (!button) return;
      const id = button.dataset.e2ePreview || button.dataset.e2eSource;
      const file = Boolean(button.dataset.e2eSource);
      const params = new URLSearchParams({business_case_id: caseId || ''});
      output.textContent = file ? '正在读取来源 Word…' : '正在定位原文…';
      try {
        const response = await fetch(`/api/public/hardware-knowledge/v1/evidence/${encodeURIComponent(id)}/${file ? 'source-file' : 'source-preview'}?${params}`, {headers:{'X-Hardware-Case-Role':'MAINTAINER', Accept:file?'application/octet-stream':'application/json'}});
        if (!response.ok) {
          let reason = response.statusText;
          try { reason = (await response.json()).detail || reason; } catch (_) {}
          throw new Error(reason);
        }
        if (file) {
          const url = URL.createObjectURL(await response.blob());
          window.open(url, '_blank', 'noopener');
          output.textContent = '原始 Word 已打开。';
        } else {
          output.innerHTML = `<pre>${esc(JSON.stringify(await response.json(), null, 2))}</pre>`;
        }
      } catch (error) { output.textContent = `Evidence 回看失败：${error.message}`; }
    });
  });
  observer.observe(document.body, {childList:true, subtree:true});
})();
