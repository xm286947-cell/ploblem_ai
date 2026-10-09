(() => {
  const root = document.querySelector('[data-hardware-r1-e2e]');
  if (!root) return;
  const target = root.querySelector('[data-e2e-readiness]');
  const button = root.querySelector('[data-e2e-refresh]');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function refresh() {
    target.textContent = '正在读取隔离目录、Agent/模型配置与非生产环境状态…';
    try {
      const response = await fetch('/api/e2e/hardware-r1/readiness', {headers:{Accept:'application/json'}});
      const state = await response.json();
      if (!response.ok) throw new Error(state.detail || response.statusText);
      const rows = [
        ['独立验证数据目录', state.data_root_isolated ? 'READY' : 'BLOCKED'],
        ['Agent 配置', state.agent_config_code ? state.agent_config + ' · ' + state.agent_config_code : state.agent_config],
        ['模型/Provider 配置', state.model_provider_config_code ? state.model_provider_config + ' · ' + state.model_provider_config_code : state.model_provider_config],
        ['Unified Knowledge 环境', state.knowledge_code ? state.knowledge_environment + ' · ' + state.knowledge_code : state.knowledge_environment],
        ['Knowledge 模式', state.knowledge_mode || '—'],
        ['Knowledge Release', state.knowledge_release_version || '—'],
        ['Promotion 服务', state.promotion_code ? state.promotion_service + ' · ' + state.promotion_code : state.promotion_service],
        ['自动 Publish', state.auto_publish ? '禁止配置错误' : '关闭'],
        ['Provider 请求', state.provider_call_performed ? '异常' : '未调用']
      ];
      target.innerHTML = `<div class="hc-knowledge-detail-grid">${rows.map(([k,v])=>`<div class="hc-knowledge-detail-item"><strong>${esc(k)}</strong><span>${esc(v)}</span></div>`).join('')}</div>`;
    } catch (error) { target.textContent = `环境状态读取失败：${error.message}`; }
  }
  button.addEventListener('click', refresh);
  refresh();
})();
