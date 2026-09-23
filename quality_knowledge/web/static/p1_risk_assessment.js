(function () {
  "use strict";
  const root = document.querySelector("[data-p1-risk]");
  if (!root) return;
  const api = (window.P1_RISK_API || root.dataset.apiPrefix || "/api/v2").replace(/\/$/, "");
  const state = { assessmentId: "", cases: [] };
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  const json = (method, body) => ({
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });

  async function request(path, options) {
    const response = await fetch(api + path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP_${response.status}`);
    return data;
  }
  function toast(message) {
    const node = root.querySelector("[data-toast]");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { node.hidden = true; }, 3500);
  }
  function productOptions(items, emptyLabel) {
    return `<option value="">${emptyLabel}</option>` + items.map(item =>
      `<option value="${esc(item.product_code)}">${esc(item.product_name)} · ${esc(item.product_code)}</option>`
    ).join("");
  }
  async function initialize() {
    const [products, cases] = await Promise.all([request("/products"), request("/risk-cases")]);
    root.querySelector("[data-products]").innerHTML = productOptions(products.items || [], "选择产品");
    root.querySelector("[data-case-products]").innerHTML = productOptions(products.items || [], "全部产品");
    renderCases(cases, true);
  }
  function renderCases(data, refreshMergeTargets) {
    state.cases = data.items || [];
    root.querySelector("[data-case-count]").textContent = `${data.total || 0} 个案例`;
    root.querySelector("[data-case-list]").innerHTML = state.cases.length ? state.cases.map(item =>
      `<article class="case"><b>${esc(item.title)}</b><small>${esc(item.publication_level)} · V${esc(item.version_no)}</small>` +
      `<p>${esc(item.case.failure_mechanism || "未提供失效机理")}</p><small>${(item.case.related_issues || []).length} 个关联问题</small></article>`
    ).join("") : "<p>当前口径下没有风险案例。</p>";
    if (refreshMergeTargets) {
      root.querySelector("[data-merge-target]").innerHTML = '<option value="">发布为新案例</option>' +
        state.cases.map(item => `<option value="${esc(item.risk_case_id)}">合并到：${esc(item.title)} · V${esc(item.version_no)}</option>`).join("");
    }
  }
  root.querySelector("[data-case-filter]").addEventListener("submit", async event => {
    event.preventDefault();
    const query = new URLSearchParams();
    for (const [key, value] of new FormData(event.currentTarget)) if (value) query.set(key, value);
    try { renderCases(await request(`/risk-cases?${query}`), false); }
    catch (error) { toast(`筛选失败：${error.message}`); }
  });
  root.querySelector("[data-publish-form]").addEventListener("submit", async event => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.currentTarget));
    body.published_by = "web";
    try {
      const saved = await request("/risk-cases/publish", json("POST", body));
      renderCases(await request("/risk-cases"), true);
      toast(saved.outcome === "MERGED" ? "问题已合并到风险模式" : "风险案例已发布");
    } catch (error) { toast(`发布失败：${error.message}`); }
  });
  root.querySelector("[data-assessment-form]").addEventListener("submit", async event => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.currentTarget));
    body.created_by = "web";
    const path = state.assessmentId
      ? `/forward-assessments/${encodeURIComponent(state.assessmentId)}/versions`
      : "/forward-assessments";
    try {
      root.querySelector("[data-report]").hidden = false;
      root.querySelector("[data-risk-list]").innerHTML = "<p>正在匹配历史案例并检查控制覆盖…</p>";
      const report = await request(path, json("POST", body));
      state.assessmentId = report.assessment_id;
      root.querySelector("[data-assessment-submit]").textContent = "更新材料并重新评估";
      root.querySelector("[data-new-assessment]").hidden = false;
      renderReport(report);
    } catch (error) { toast(`评估失败：${error.message}`); }
  });
  root.querySelector("[data-new-assessment]").addEventListener("click", () => {
    state.assessmentId = "";
    root.querySelector("[data-assessment-form]").reset();
    root.querySelector("[data-assessment-submit]").textContent = "开始风险评估";
    root.querySelector("[data-new-assessment]").hidden = true;
    root.querySelector("[data-report]").hidden = true;
  });
  function list(values) {
    return (values || []).length
      ? `<ul>${values.map(value => `<li>${esc(value)}</li>`).join("")}</ul>`
      : "<p>—</p>";
  }
  function boundary(value) {
    const boundaryValue = value || {};
    const parts = [];
    [["products", "产品"], ["domains", "领域"], ["lifecycle_phases", "生命周期"]].forEach(([key, label]) => {
      if ((boundaryValue[key] || []).length) parts.push(`${label}：${boundaryValue[key].join("、")}`);
    });
    return parts.length ? parts.join("；") : "未限定";
  }
  function gapMeasures(gaps) {
    return (gaps || []).map(gap => {
      const details = gap.details || {};
      const action = details.first_action || details.recommended_action || details.management_action ||
        details.engineering_action || "待质量专家补充";
      return `${gap.axis} / ${gap.code}：${action}`;
    });
  }
  function renderComparison(comparison) {
    const node = root.querySelector("[data-comparison]");
    if (!comparison) { node.hidden = true; return; }
    const changes = (comparison.changed_risks || []).map(item =>
      `${item.risk_name}：${item.coverage_before} → ${item.coverage_after}`
    );
    node.hidden = false;
    node.innerHTML = `<h3>与 V${esc(comparison.previous_version_no)} 比较</h3>` +
      `<p>新增风险 ${(comparison.new_risks || []).length} · 消除风险 ${(comparison.resolved_risks || []).length} · 状态变化 ${changes.length}</p>` +
      list([...(comparison.new_risks || []).map(x => `新增：${x}`),
        ...(comparison.resolved_risks || []).map(x => `消除：${x}`), ...changes]);
  }
  function renderReport(report) {
    root.querySelector("[data-report-scope]").textContent =
      `${report.project_name} · ${report.product_name} · ${report.assessment_stage} · ${report.material_name} · V${report.version_no || 1}`;
    const summary = report.coverage_summary || {};
    root.querySelector("[data-summary]").innerHTML = `<span class="chip">匹配风险 ${report.matched_risk_count}</span>` +
      ["COVERED", "PARTIAL", "NOT_FOUND", "INSUFFICIENT_INFO", "NOT_APPLICABLE"].map(status =>
        `<span class="chip">${status} ${summary[status] || 0}</span>`).join("");
    renderComparison(report.version_comparison);
    const risks = report.risks || [];
    root.querySelector("[data-no-risk]").hidden = Boolean(risks.length);
    root.querySelector("[data-risk-list]").innerHTML = risks.map(risk => {
      const caseValue = risk.case || {};
      return `<article class="risk ${String(risk.risk_level).toLowerCase()}"><div class="head"><div><h3>${esc(risk.risk_name)}</h3>` +
        `<p>相关性 ${Math.round(risk.relevance * 100)}% · 置信度 ${Math.round(risk.confidence * 100)}%</p></div><div>` +
        `<span class="chip">${esc(risk.risk_level)}</span> <span class="chip">${esc(risk.coverage_status)}</span></div></div>` +
        `<div class="risk-grid"><section><label>匹配依据 / 失效机理</label>${list([...(risk.match_basis.matched_dimensions || []), ...(risk.match_basis.matched_keywords || []).slice(0, 8)])}` +
        `<p>${esc(risk.match_basis.mechanism || "未提供机理")}</p></section><section><label>适用边界</label><p>${esc(boundary(risk.match_basis.applicability_boundary))}</p>` +
        `<p>触发：${esc(caseValue.trigger_conditions || "未提供")}</p><p>客户影响：${esc(caseValue.customer_impact || "未提供")}</p></section>` +
        `<section><label>已有控制</label>${list(risk.existing_controls)}</section><section><label>缺失控制 / 待确认</label>` +
        `${list([...(risk.missing_controls || []), ...(risk.open_questions || [])])}</section>` +
        `<section><label>工程 / 管理措施</label>${list(gapMeasures(caseValue.capability_gaps))}</section>` +
        `<section><label>建议验证场景</label>${list(caseValue.verification_scenarios)}</section></div>` +
        `<form class="review" data-review="${esc(risk.risk_result_id)}"><select name="status"><option value="CONFIRMED">确认风险</option>` +
        `<option value="CORRECTED">修正结论</option><option value="NOT_APPLICABLE">不适用</option></select>` +
        `<input name="note" placeholder="填写确认依据或修正说明"><button>保存人工复核</button></form></article>`;
    }).join("");
    root.querySelectorAll("[data-review]").forEach(form => form.addEventListener("submit", review));
  }
  async function review(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form));
    body.reviewed_by = "web";
    try {
      const saved = await request(`/forward-risk-results/${encodeURIComponent(form.dataset.review)}/review`, json("POST", body));
      const button = form.querySelector("button");
      button.textContent = `已保存 · ${saved.review_status}`;
      button.disabled = true;
      toast("人工复核已保存");
    } catch (error) { toast(`保存失败：${error.message}`); }
  }
  initialize().catch(error => toast(`初始化失败：${error.message}`));
})();
