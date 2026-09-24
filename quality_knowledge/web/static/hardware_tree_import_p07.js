(() => {
  "use strict";

  const root = document.getElementById("hc-tree-p07");
  if (!root) return;

  const api = root.dataset.apiPrefix;
  const caseApi = api.replace(/\/tree-imports$/, "");
  const MAINTAINER_HEADERS = () => ({
    "X-Hardware-Case-Role": "MAINTAINER",
    "X-Hardware-Case-Operator": state.operator || localStorage.getItem("hc-tree-operator") || "web-maintainer"
  });

  const state = {
    treeType: "CIRCUIT_FEATURE",
    operator: localStorage.getItem("hc-tree-operator") || "",
    jobId: null,
    workbook: null,
    job: null,
    analysis: null,
    columns: [],
    currentStep: 1
  };

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const pathText = (node) => {
    if (!node) return "—";
    const path = Array.isArray(node.path) ? node.path : [];
    return path.length ? path.join(" / ") : (node.name || node.node_id || "—");
  };
  const statusLabel = (status) => ({
    APPLIED: "SUCCESS",
    APPLIED_WITH_EXCLUSIONS: "APPLIED_WITH_EXCLUSIONS",
    APPLY_FAILED: "APPLY_FAILED"
  }[status] || status || "—");
  const statusClass = (status) => {
    if (status === "APPLIED") return "hc-status-success";
    if (status === "APPLIED_WITH_EXCLUSIONS") return "hc-status-exclusion";
    if (status === "APPLY_FAILED" || status === "PARSE_FAILED" || status === "VALIDATION_FAILED") return "hc-status-failed";
    if (status === "READY_TO_APPLY" || status === "REVIEW_REQUIRED") return "hc-status-review";
    return "hc-status-neutral";
  };

  function alertBox(message, kind = "error") {
    const el = $("hc-alert");
    el.hidden = false;
    el.className = "hc-alert " + (kind === "success" ? "success" : kind === "warning" ? "warning" : "error");
    el.textContent = message;
    el.scrollIntoView({block: "nearest"});
  }
  function clearAlert() {
    const el = $("hc-alert");
    el.hidden = true;
    el.textContent = "";
  }

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    let body = null;
    try { body = await response.json(); } catch (_) { body = null; }
    if (!response.ok) {
      const code = body?.detail || body?.error || ("HTTP_" + response.status);
      const error = new Error(String(code));
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  }

  function showHome() {
    $("hc-home").hidden = false;
    $("hc-workflow").hidden = true;
    clearAlert();
  }

  function showWorkflow(treeType, step = 1) {
    state.treeType = treeType || state.treeType;
    $("hc-home").hidden = true;
    $("hc-workflow").hidden = false;
    $("hc-tree-type").value = state.treeType;
    $("hc-operator").value = state.operator;
    setStep(step);
  }

  function setStep(step) {
    state.currentStep = step;
    document.querySelectorAll(".hc-step-panel").forEach(el => el.hidden = true);
    const panel = $("hc-step-" + step);
    if (panel) panel.hidden = false;
    document.querySelectorAll(".hc-steps li").forEach(el => {
      const n = Number(el.dataset.step);
      el.classList.toggle("active", n === step);
      el.classList.toggle("done", n < step);
    });
    if (panel) panel.scrollIntoView({behavior: "smooth", block: "start"});
  }

  async function loadHome() {
    clearAlert();
    const allJobs = await request(api, {headers: MAINTAINER_HEADERS()});
    renderRecentJobs(allJobs.items || []);
    await Promise.all(["CIRCUIT_FEATURE", "MATERIAL_DEVICE"].map(loadTreeCard));
  }

  async function loadTreeCard(treeType) {
    const card = document.querySelector('[data-tree-card="' + treeType + '"]');
    if (!card) return;
    let version = null, tree = null, history = null;
    try { version = await request(api + "/active-version/" + treeType, {headers: MAINTAINER_HEADERS()}); } catch (_) {}
    try { tree = await request(caseApi + "/trees/" + treeType); } catch (_) {}
    try { history = await request(api + "?tree_type=" + encodeURIComponent(treeType), {headers: MAINTAINER_HEADERS()}); } catch (_) {}
    const jobs = history?.items || [];
    const last = jobs.length ? jobs[jobs.length - 1] : null;
    const issueCount = Number(last?.counts?.validation_issue_count || 0);
    card.querySelector('[data-field="version"]').textContent = version?.active_version?.version_id || "未建立";
    card.querySelector('[data-field="nodes"]').textContent = Array.isArray(tree?.nodes) ? tree.nodes.length : "0";
    card.querySelector('[data-field="last-job"]').textContent = last ? statusLabel(last.status) : "无";
    card.querySelector('[data-field="issues"]').textContent = String(issueCount);
  }

  function renderRecentJobs(items) {
    const tbody = $("hc-recent-imports");
    const sorted = [...items].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))).slice(0, 12);
    if (!sorted.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">暂无导入任务</td></tr>';
      return;
    }
    tbody.innerHTML = sorted.map(job => {
      const issues = Number(job.counts?.validation_issue_count || 0);
      return '<tr>' +
        '<td class="mono">' + escapeHtml(job.job_id) + '</td>' +
        '<td>' + escapeHtml(job.tree_type) + '</td>' +
        '<td>' + escapeHtml(job.source_filename) + '</td>' +
        '<td>' + escapeHtml(job.created_at || "—") + '</td>' +
        '<td><span class="hc-status ' + statusClass(job.status) + '">' + escapeHtml(statusLabel(job.status)) + '</span></td>' +
        '<td>' + escapeHtml(job.applied_version_id || "—") + '</td>' +
        '<td>' + issues + '</td>' +
        '<td>' + escapeHtml(job.operator || "—") + '</td>' +
      '</tr>';
    }).join("");
  }

  function resetWorkflow(treeType) {
    state.treeType = treeType;
    state.jobId = null;
    state.workbook = null;
    state.job = null;
    state.analysis = null;
    state.columns = [];
    $("hc-file").value = "";
    $("hc-sheet").innerHTML = "";
    $("hc-header-row").value = "1";
    $("hc-mapping-builder").innerHTML = "";
    $("hc-job-meta").textContent = "";
    $("hc-apply-result").innerHTML = "";
    showWorkflow(treeType, 1);
  }

  async function uploadWorkbook(event) {
    event.preventDefault();
    clearAlert();
    const treeType = $("hc-tree-type").value;
    const operator = $("hc-operator").value.trim();
    const file = $("hc-file").files[0];
    if (!operator) return alertBox("请输入操作者，导入任务必须可审计。");
    if (!file) return alertBox("请选择 .xlsx 文件。");
    if (!file.name.toLowerCase().endsWith(".xlsx")) return alertBox("仅支持 .xlsx 文件。");

    state.operator = operator;
    state.treeType = treeType;
    localStorage.setItem("hc-tree-operator", operator);

    const form = new FormData();
    form.append("tree_type", treeType);
    form.append("file", file);
    try {
      const result = await request(api, {method: "POST", headers: MAINTAINER_HEADERS(), body: form});
      state.job = result.job;
      state.jobId = result.job.job_id;
      state.workbook = result.workbook;
      $("hc-job-meta").innerHTML = '<b>' + escapeHtml(result.job.job_id) + '</b><span>' +
        escapeHtml(result.job.source_filename) + ' · ' + escapeHtml(result.job.import_type) + '</span>';
      const sheet = $("hc-sheet");
      sheet.innerHTML = (result.workbook.sheets || []).map(item =>
        '<option value="' + escapeHtml(item.sheet_name) + '">' +
          escapeHtml(item.sheet_name) + ' · ' + item.max_row + ' 行 / ' + item.max_column + ' 列</option>'
      ).join("");
      setStep(2);
      alertBox("Workbook 检查完成。请配置 Sheet、Header 与动态 Mapping。", "success");
    } catch (error) {
      alertBox("上传失败：" + error.message);
    }
  }

  function addColumn(name = "", role = "LEVEL") {
    const clean = String(name || "").trim();
    if (clean && state.columns.some(item => item.name === clean)) {
      return alertBox("原始列已存在：" + clean, "warning");
    }
    state.columns.push({name: clean, role});
    renderMapping();
  }

  function renderMapping() {
    const target = $("hc-mapping-builder");
    if (!state.columns.length) {
      target.innerHTML = '<div class="hc-empty">还没有 Mapping 行。按 Excel 表头加入原始列，不限制层级数量。</div>';
      return;
    }
    target.innerHTML = state.columns.map((item, index) =>
      '<div class="hc-mapping-row" data-index="' + index + '">' +
        '<input class="hc-map-name" value="' + escapeHtml(item.name) + '" placeholder="原始列名">' +
        '<select class="hc-map-role">' +
          '<option value="LEVEL"' + (item.role === "LEVEL" ? " selected" : "") + '>层级 LEVEL</option>' +
          '<option value="METADATA"' + (item.role === "METADATA" ? " selected" : "") + '>Metadata</option>' +
          '<option value="BUSINESS_KEY"' + (item.role === "BUSINESS_KEY" ? " selected" : "") + '>Business Key</option>' +
          '<option value="IGNORE"' + (item.role === "IGNORE" ? " selected" : "") + '>忽略</option>' +
        '</select>' +
        '<div class="hc-map-actions">' +
          '<button type="button" class="btn hc-up" title="上移">↑</button>' +
          '<button type="button" class="btn hc-down" title="下移">↓</button>' +
          '<button type="button" class="btn hc-remove">移除</button>' +
        '</div>' +
      '</div>'
    ).join("");

    target.querySelectorAll(".hc-mapping-row").forEach(row => {
      const index = Number(row.dataset.index);
      row.querySelector(".hc-map-name").addEventListener("input", e => { state.columns[index].name = e.target.value; });
      row.querySelector(".hc-map-role").addEventListener("change", e => {
        const role = e.target.value;
        if (role === "BUSINESS_KEY") {
          state.columns.forEach((col, i) => { if (i !== index && col.role === "BUSINESS_KEY") col.role = "IGNORE"; });
        }
        state.columns[index].role = role;
        renderMapping();
      });
      row.querySelector(".hc-up").addEventListener("click", () => moveColumn(index, -1));
      row.querySelector(".hc-down").addEventListener("click", () => moveColumn(index, 1));
      row.querySelector(".hc-remove").addEventListener("click", () => { state.columns.splice(index, 1); renderMapping(); });
    });
  }

  function moveColumn(index, offset) {
    const next = index + offset;
    if (next < 0 || next >= state.columns.length) return;
    [state.columns[index], state.columns[next]] = [state.columns[next], state.columns[index]];
    renderMapping();
  }

  function mappingProfile() {
    state.columns.forEach(item => { item.name = String(item.name || "").trim(); });
    const pathColumns = state.columns.filter(item => item.role === "LEVEL" && item.name).map(item => item.name);
    const metadataColumns = state.columns.filter(item => item.role === "METADATA" && item.name).map(item => item.name);
    const businessKeys = state.columns.filter(item => item.role === "BUSINESS_KEY" && item.name).map(item => item.name);
    if (!pathColumns.length) throw new Error("至少需要一个层级列。");
    if (businessKeys.length > 1) throw new Error("Business Key 只能选择一个。");
    return {
      sheet_name: $("hc-sheet").value,
      header_row: Number($("hc-header-row").value || 0),
      path_columns: pathColumns,
      metadata_columns: metadataColumns,
      business_key_column: businessKeys[0] || null
    };
  }

  async function analyze() {
    clearAlert();
    if (!state.jobId) return alertBox("当前没有导入任务。");
    let profile;
    try { profile = mappingProfile(); } catch (error) { return alertBox(error.message); }
    if (!profile.sheet_name || profile.header_row < 1) return alertBox("请选择 Sheet 并填写有效表头行。");
    try {
      const result = await request(api + "/" + state.jobId + "/analyze", {
        method: "POST",
        headers: {...MAINTAINER_HEADERS(), "Content-Type": "application/json"},
        body: JSON.stringify(profile)
      });
      state.analysis = result;
      state.job = result.job;
      renderPreview(result, profile);
      renderChanges(result.changes || []);
      setStep(3);
      alertBox("Preview / Validation 已生成。当前 ACTIVE Tree 尚未改变。", "success");
    } catch (error) {
      alertBox("分析失败：" + error.message + "。请核对 Sheet、表头行和原始列名。");
    }
  }

  function renderPreview(result, profile) {
    const nodes = result.preview?.nodes || [];
    const stats = result.preview?.stats || {};
    $("hc-tree-preview").innerHTML = nodes.length ? nodes.slice(0, 120).map(node =>
      '<div class="hc-node-row"><span>' + escapeHtml(pathText(node)) + '</span><small>' +
      escapeHtml(node.business_key || node.node_id || "") + '</small></div>'
    ).join("") : '<div class="hc-empty">没有 Candidate 节点</div>';

    $("hc-source-preview").innerHTML =
      '<dl class="hc-definition">' +
      '<div><dt>Sheet</dt><dd>' + escapeHtml(profile.sheet_name) + '</dd></div>' +
      '<div><dt>Header</dt><dd>第 ' + profile.header_row + ' 行</dd></div>' +
      '<div><dt>层级列</dt><dd>' + escapeHtml(profile.path_columns.join(" → ")) + '</dd></div>' +
      '<div><dt>Metadata</dt><dd>' + escapeHtml(profile.metadata_columns.join("、") || "无") + '</dd></div>' +
      '<div><dt>Business Key</dt><dd>' + escapeHtml(profile.business_key_column || "未配置") + '</dd></div>' +
      '<div><dt>数据行</dt><dd>' + escapeHtml(stats.data_row_count ?? "—") + '</dd></div>' +
      '<div><dt>最大深度</dt><dd>' + escapeHtml(stats.max_depth ?? "—") + '</dd></div>' +
      '</dl>';

    renderIssues(result.issues || []);
  }

  function renderIssues(issues) {
    $("hc-validation-count").textContent = String(issues.filter(item => !item.resolved).length);
    const list = $("hc-validation-list");
    if (!issues.length) {
      list.innerHTML = '<div class="hc-empty hc-ok">Validation 无问题</div>';
      return;
    }
    list.innerHTML = issues.map(issue =>
      '<article class="hc-validation-item ' + (issue.resolved ? "resolved" : "") + '">' +
        '<div><b>' + escapeHtml(issue.issue_type) + '</b><span>' +
        escapeHtml(issue.sheet_name || "—") + ' / Row ' + escapeHtml(issue.row_number || "—") +
        ' / Column ' + escapeHtml(issue.column_name || "—") + '</span></div>' +
        '<p>' + escapeHtml(issue.suggested_action || issue.original_value || "") + '</p>' +
        (issue.resolved ? '<span class="hc-status hc-status-success">已处理</span>' :
          '<button class="btn hc-resolve-issue" data-issue="' + escapeHtml(issue.issue_id) + '">标记已处理</button>') +
      '</article>'
    ).join("");
    list.querySelectorAll(".hc-resolve-issue").forEach(btn => btn.addEventListener("click", () => resolveIssue(btn.dataset.issue)));
  }

  async function resolveIssue(issueId) {
    try {
      await request(api + "/" + state.jobId + "/issues/" + encodeURIComponent(issueId) + "/resolve", {
        method: "POST", headers: MAINTAINER_HEADERS()
      });
      await reloadJob();
      alertBox("Validation 项已标记为已处理。", "success");
    } catch (error) {
      alertBox("处理 Validation 项失败：" + error.message);
    }
  }

  function renderChanges(changes) {
    state.analysis = state.analysis || {};
    state.analysis.changes = changes;
    const types = ["ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE", "NO_CHANGE", "CONFLICT"];
    const summary = Object.fromEntries(types.map(type => [type, changes.filter(c => c.change_type === type).length]));
    const excluded = changes.filter(c => c.decision === "EXCLUDED").length;
    $("hc-change-summary").innerHTML = types.map(type =>
      '<div><span>' + type + '</span><strong>' + summary[type] + '</strong></div>'
    ).join("") + '<div class="hc-excluded"><span>排除项</span><strong>' + excluded + '</strong></div>';

    const tbody = $("hc-change-table");
    if (!changes.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted">没有变更</td></tr>';
      updateConflictGate();
      return;
    }
    tbody.innerHTML = changes.map(change => {
      const before = change.before || null;
      const after = change.after || null;
      const decision = change.decision || "PENDING";
      let actions = "";
      if (change.change_type === "NO_CHANGE") {
        actions = '<span class="muted">无需处理</span>';
      } else if (change.change_type === "CONFLICT") {
        actions = '<div class="hc-conflict-editor">' +
          '<textarea class="hc-conflict-json" data-change="' + escapeHtml(change.change_id) + '">' +
            escapeHtml(JSON.stringify(after || before || {}, null, 2)) + '</textarea>' +
          '<div><button class="btn btn-primary hc-resolve-conflict" data-change="' + escapeHtml(change.change_id) + '">接受 / 修正</button>' +
          '<button class="btn hc-exclude-change" data-change="' + escapeHtml(change.change_id) + '">排除</button></div>' +
        '</div>';
      } else {
        actions = '<div class="hc-inline-actions"><button class="btn btn-primary hc-confirm-change" data-change="' +
          escapeHtml(change.change_id) + '">确认</button><button class="btn hc-exclude-change" data-change="' +
          escapeHtml(change.change_id) + '">排除</button></div>';
      }
      return '<tr>' +
        '<td><span class="hc-change-type type-' + escapeHtml(change.change_type.toLowerCase()) + '">' + escapeHtml(change.change_type) + '</span></td>' +
        '<td><b>' + escapeHtml(pathText(after || before)) + '</b><small class="mono">' + escapeHtml(change.node_id || change.business_key || "") + '</small></td>' +
        '<td><pre>' + escapeHtml(before ? JSON.stringify(before, null, 2) : "—") + '</pre></td>' +
        '<td><pre>' + escapeHtml(after ? JSON.stringify(after, null, 2) : "—") + '</pre></td>' +
        '<td><span class="hc-status ' + (decision === "PENDING" ? "hc-status-review" : decision === "EXCLUDED" ? "hc-status-exclusion" : "hc-status-success") + '">' + escapeHtml(decision) + '</span></td>' +
        '<td>' + actions + '</td>' +
      '</tr>';
    }).join("");

    tbody.querySelectorAll(".hc-confirm-change").forEach(btn => btn.addEventListener("click", () => decideChange(btn.dataset.change, "CONFIRMED")));
    tbody.querySelectorAll(".hc-exclude-change").forEach(btn => btn.addEventListener("click", () => decideChange(btn.dataset.change, "EXCLUDED")));
    tbody.querySelectorAll(".hc-resolve-conflict").forEach(btn => btn.addEventListener("click", () => {
      const area = tbody.querySelector('.hc-conflict-json[data-change="' + CSS.escape(btn.dataset.change) + '"]');
      let resolved;
      try { resolved = JSON.parse(area.value); } catch (_) { return alertBox("手工修正内容必须是有效 JSON。"); }
      decideChange(btn.dataset.change, "RESOLVED", resolved);
    }));
    updateConflictGate();
  }

  async function decideChange(changeId, decision, resolvedAfter = null) {
    try {
      const body = {decision};
      if (decision === "RESOLVED") body.resolved_after = resolvedAfter;
      await request(api + "/" + state.jobId + "/changes/" + encodeURIComponent(changeId) + "/decision", {
        method: "POST",
        headers: {...MAINTAINER_HEADERS(), "Content-Type": "application/json"},
        body: JSON.stringify(body)
      });
      await reloadJob();
      alertBox(decision === "EXCLUDED" ? "Candidate 已排除；EXCLUDE 不属于 Tree Change Type。" : "变更处理状态已更新。", "success");
    } catch (error) {
      alertBox("变更处理失败：" + error.message);
    }
  }

  function updateConflictGate() {
    const changes = state.analysis?.changes || [];
    const issues = state.analysis?.issues || [];
    const unresolvedIssues = issues.filter(item => !item.resolved).length;
    const pendingConflicts = changes.filter(item => item.change_type === "CONFLICT" && !["RESOLVED", "EXCLUDED"].includes(item.decision)).length;
    const pendingFormal = changes.filter(item => !["NO_CHANGE", "CONFLICT"].includes(item.change_type) && !["CONFIRMED", "EXCLUDED"].includes(item.decision)).length;
    const blocked = unresolvedIssues + pendingConflicts + pendingFormal > 0;
    $("hc-ready-apply").disabled = blocked;
    $("hc-conflict-gate").className = "hc-gate-note " + (blocked ? "blocked" : "ready");
    $("hc-conflict-gate").textContent = blocked
      ? "Apply Gate 阻断：Validation 未处理 " + unresolvedIssues + "，Conflict 未处理 " + pendingConflicts + "，正式变更未确认 " + pendingFormal + "。"
      : "Apply Gate 已满足：Validation 已处理，Conflict=0，正式变更均已确认或事前排除。";
  }

  async function reloadJob() {
    const detail = await request(api + "/" + state.jobId, {headers: MAINTAINER_HEADERS()});
    state.job = detail.job;
    state.analysis = state.analysis || {};
    state.analysis.changes = detail.changes || [];
    state.analysis.issues = detail.issues || [];
    renderIssues(state.analysis.issues);
    renderChanges(state.analysis.changes);
    return detail;
  }

  async function readyToApply() {
    try {
      const job = await request(api + "/" + state.jobId + "/ready", {method: "POST", headers: MAINTAINER_HEADERS()});
      state.job = job;
      renderApplySummary();
      setStep(5);
    } catch (error) {
      alertBox("尚不能进入 Apply：" + error.message);
    }
  }

  function renderApplySummary() {
    const changes = state.analysis?.changes || [];
    const types = ["ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE", "NO_CHANGE", "CONFLICT"];
    const excluded = changes.filter(item => item.decision === "EXCLUDED").length;
    const formal = types.map(type => '<div><span>' + type + '</span><strong>' + changes.filter(item => item.change_type === type).length + '</strong></div>').join("");
    $("hc-apply-summary").innerHTML =
      '<div class="hc-apply-meta"><div><span>目标树</span><strong>' + escapeHtml(state.treeType) + '</strong></div>' +
      '<div><span>当前 ACTIVE Version</span><strong>' + escapeHtml(state.job?.current_version_id || "未建立") + '</strong></div>' +
      '<div><span>排除项</span><strong>' + excluded + '</strong></div><div><span>未处理冲突</span><strong>0</strong></div></div>' +
      '<div class="hc-formal-summary">' + formal + '</div>';
  }

  async function applyJob() {
    $("hc-confirm-apply").disabled = true;
    clearAlert();
    let beforeActive = null;
    try {
      beforeActive = await request(api + "/active-version/" + state.treeType, {headers: MAINTAINER_HEADERS()});
    } catch (_) {}
    try {
      const result = await request(api + "/" + state.jobId + "/apply", {method: "POST", headers: MAINTAINER_HEADERS()});
      state.job = result.job;
      const excluded = Number(result.job.counts?.excluded_count || 0);
      const changed = Math.max(0, Number(result.job.counts?.change_count || 0) - excluded);
      if (result.job.status === "APPLIED_WITH_EXCLUSIONS") {
        $("hc-apply-result").innerHTML =
          '<article class="hc-result hc-result-exclusion"><h3>已生效，存在排除项</h3>' +
          '<p>用户在 Apply 前已明确排除部分 Candidate，其余 Change Set 已 Atomic Apply 成功。</p>' +
          '<div><b>生效：' + changed + '</b><b>排除：' + excluded + '</b><b>失败：0</b></div>' +
          '<p>新 ACTIVE Version：<strong>' + escapeHtml(result.active_version?.version_id || result.job.applied_version_id) + '</strong></p></article>';
      } else {
        $("hc-apply-result").innerHTML =
          '<article class="hc-result hc-result-success"><h3>导入成功</h3>' +
          '<p>Change Set 已全部生效。</p><div><b>生效：' + changed + '</b><b>排除：0</b><b>失败：0</b></div>' +
          '<p>新 ACTIVE Version：<strong>' + escapeHtml(result.active_version?.version_id || result.job.applied_version_id) + '</strong></p></article>';
      }
      alertBox("Atomic Apply 完成。", "success");
      await renderHistory(state.treeType);
      setTimeout(() => setStep(6), 50);
    } catch (error) {
      let detail = null, afterActive = null;
      try { detail = await request(api + "/" + state.jobId, {headers: MAINTAINER_HEADERS()}); } catch (_) {}
      try { afterActive = await request(api + "/active-version/" + state.treeType, {headers: MAINTAINER_HEADERS()}); } catch (_) {}
      const beforeId = beforeActive?.active_version?.version_id || "未建立";
      const afterId = afterActive?.active_version?.version_id || "未建立";
      $("hc-apply-result").innerHTML =
        '<article class="hc-result hc-result-failed"><h3>APPLY_FAILED</h3>' +
        '<p>Atomic Apply 失败，未生成新的 ACTIVE Version。上一 ACTIVE Version 保持不变。</p>' +
        '<div><b>失败：1</b><b>错误：' + escapeHtml(detail?.job?.error_code || error.message) + '</b></div>' +
        '<p>Apply 前：<strong>' + escapeHtml(beforeId) + '</strong> · 当前：<strong>' + escapeHtml(afterId) + '</strong></p></article>';
      alertBox("Apply 失败：" + error.message);
    } finally {
      $("hc-confirm-apply").disabled = false;
    }
  }

  async function renderHistory(treeType = state.treeType) {
    state.treeType = treeType;
    const [history, active] = await Promise.all([
      request(api + "?tree_type=" + encodeURIComponent(treeType), {headers: MAINTAINER_HEADERS()}),
      request(api + "/active-version/" + encodeURIComponent(treeType), {headers: MAINTAINER_HEADERS()}).catch(() => ({active_version: null}))
    ]);
    const activeId = active?.active_version?.version_id || null;
    const rows = [...(history.items || [])].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    const tbody = $("hc-version-history");
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">暂无版本 / 导入历史</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(job => {
      const version = job.applied_version_id || "—";
      const vStatus = version !== "—" ? (version === activeId ? "ACTIVE" : "HISTORICAL") : "—";
      return '<tr>' +
        '<td><b>' + escapeHtml(version) + '</b></td>' +
        '<td>' + escapeHtml(vStatus) + '</td>' +
        '<td class="mono">' + escapeHtml(job.job_id) + '</td>' +
        '<td>' + escapeHtml(job.tree_type) + '</td>' +
        '<td>' + escapeHtml(job.source_filename) + '</td>' +
        '<td><span class="hc-status ' + statusClass(job.status) + '">' + escapeHtml(statusLabel(job.status)) + '</span></td>' +
        '<td>' + escapeHtml(job.operator || "—") + '</td>' +
        '<td>' + escapeHtml(job.updated_at || job.created_at || "—") + '</td>' +
      '</tr>';
    }).join("");
  }

  $("hc-refresh-home").addEventListener("click", () => loadHome().catch(err => alertBox(err.message)));
  $("hc-back-home").addEventListener("click", () => { showHome(); loadHome().catch(err => alertBox(err.message)); });
  $("hc-upload-form").addEventListener("submit", uploadWorkbook);
  $("hc-cancel-upload").addEventListener("click", showHome);
  $("hc-add-column").addEventListener("click", () => {
    const input = $("hc-column-name");
    const value = input.value.trim();
    if (!value) return alertBox("请输入原始列名。", "warning");
    addColumn(value);
    input.value = "";
  });
  $("hc-column-name").addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); $("hc-add-column").click(); }
  });
  $("hc-run-analysis").addEventListener("click", analyze);
  $("hc-to-diff").addEventListener("click", () => { renderChanges(state.analysis?.changes || []); setStep(4); });
  $("hc-ready-apply").addEventListener("click", readyToApply);
  $("hc-confirm-apply").addEventListener("click", applyJob);
  $("hc-back-diff").addEventListener("click", () => setStep(4));
  $("hc-refresh-history").addEventListener("click", () => renderHistory().catch(err => alertBox(err.message)));

  document.querySelectorAll("[data-start-import]").forEach(btn => btn.addEventListener("click", () => resetWorkflow(btn.dataset.startImport)));
  document.querySelectorAll("[data-show-history]").forEach(btn => btn.addEventListener("click", async () => {
    showWorkflow(btn.dataset.showHistory, 6);
    try { await renderHistory(btn.dataset.showHistory); } catch (error) { alertBox("历史加载失败：" + error.message); }
  }));

  renderMapping();
  loadHome().catch(error => alertBox("基础数据加载失败：" + error.message));
})();