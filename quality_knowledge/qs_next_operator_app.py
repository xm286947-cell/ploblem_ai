"""Opt-in human-review operator workbench, layered on the frozen original Web.

No legacy page or source DB mutation. Security must be bound by an existing
trusted identity/permission/CSRF adapter before this can be mounted anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from quality_knowledge.qs_next_gateway import (
    EntryGateError, create_router as create_gateway_router,
)
from quality_knowledge.qs_next_candidate_preview import (
    FIELD_ALIASES, preview_one_issue, create_candidate_preview_router,
)
from quality_knowledge.qs_next_review import (
    REQUIRED_FIELDS, ReviewError, ReviewStore, create_review_router,
)
from quality_knowledge.qs_next_aggregation import create_suggestion_router
from quality_knowledge.qs_next_taxonomy_gate import OriginalTaxonomyGate


@dataclass(frozen=True)
class TrustedReviewSecurity:
    authenticated_actor: Callable[[Request], str | None]
    authorize: Callable[[str, str, str], bool]
    verify_csrf: Callable[[Request], bool]
    csrf_token_for: Callable[[Request], str]


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _safe_js(value: Any) -> str:
    # Embedded JSON in HTML must not contain a literal closing script tag.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _page(title: str, body: str, *, js: str = "") -> HTMLResponse:
    return HTMLResponse("""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + _escape(title) + """ · 质量场景</title>
<style>
:root{font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#243347;background:#f5f7fb}
body{margin:0}header{background:#152f4c;color:white;padding:18px 28px}
header a{color:#cee5ff;text-decoration:none}main{max-width:1120px;margin:24px auto;padding:0 16px}
h1{font-size:25px}h2{font-size:19px;margin:4px 0 14px}
.card{background:white;border:1px solid #dce3ed;border-radius:12px;padding:20px;margin:15px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px}
.small{font-size:13px;color:#65748a}.fact{font-size:14px;line-height:1.65}
label{display:block;font-size:13px;color:#536579;margin:8px 0 5px}
input,select,textarea{box-sizing:border-box;width:100%;border-radius:7px;border:1px solid #c1ccda;padding:9px;background:white;color:#21334a}
textarea{min-height:64px}button{padding:10px 16px;border:0;border-radius:7px;background:#285b9c;color:white;cursor:pointer}
button.danger{background:#a93946}button.secondary{background:#506879}button:disabled{opacity:.45}
nav a{margin-right:16px}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #e5eaf0;text-align:left;vertical-align:top;word-break:break-word}
code{white-space:normal;font-size:12px}aside{font-size:12px;color:#6b7790}
.note{color:#79510c;background:#fff7e6;padding:12px;border-radius:7px}
#error{white-space:pre-wrap;color:#a02332}#message{white-space:pre-wrap;color:#145b33}
</style></head><body><header><nav><a href="/qs-next/">质量场景 · 新工作台</a></nav></header><main>"""
    + body + """</main><script>""" + js + """</script></body></html>""")


def create_stage6_app(
    source_db: str | Path,
    review_db: str | Path,
    *,
    security: TrustedReviewSecurity,
    approved_domain_scopes: Mapping[str, set[str] | frozenset[str]],
    legacy_app: FastAPI | None = None,
) -> FastAPI:
    """Sandbox and integration *factory*; no implicit production mount.

    This is a sidecar entry surface: it does not inject links into the protected
    original workbench templates. For users it provides three entry forms
    referencing the existing material IDs. Product navigation integration is
    a separate change requiring full authorization/UED regression.
    """
    if not isinstance(security, TrustedReviewSecurity) or not all(
        callable(getattr(security, field, None))
        for field in ("authenticated_actor", "authorize", "verify_csrf", "csrf_token_for")
    ):
        raise ValueError("TRUSTED_SECURITY_BINDING_REQUIRED")
    source_db = Path(source_db).resolve()
    review_db = Path(review_db).resolve()
    gate = OriginalTaxonomyGate(
        source_db, approved_domain_scopes=approved_domain_scopes
    )
    store = ReviewStore(source_db, review_db)
    app = legacy_app or FastAPI(title="Quality Scenario Next — Isolated Operator")

    # Protect *every* new route, including the inherited stage3/4 GET APIs.
    # Never trust actor names supplied by form fields, query strings or headers.
    @app.middleware("http")
    async def protect_qs_next(request: Request, call_next):
        path = request.url.path
        if path == "/qs-next" or path.startswith(("/qs-next/", "/api/v2/qs-next/")):
            try:
                actor = security.authenticated_actor(request)
                if not actor or not security.authorize(actor, "VIEW", path):
                    return JSONResponse({"detail": "QS_OPERATOR_ACCESS_DENIED"}, status_code=403)
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    if not security.verify_csrf(request):
                        return JSONResponse({"detail": "CSRF_VALIDATION_REQUIRED"}, status_code=403)
            except Exception:
                return JSONResponse({"detail": "QS_SECURITY_BINDING_FAILED"}, status_code=403)
        return await call_next(request)

    app.include_router(create_gateway_router(source_db))
    app.include_router(create_candidate_preview_router(source_db))
    app.include_router(create_review_router(
        source_db, review_db,
        trusted_actor=security.authenticated_actor,
        authorize=security.authorize,
        taxonomy_verified=gate,
    ))
    app.include_router(create_suggestion_router(
        store, trusted_actor=security.authenticated_actor,
        authorize=security.authorize,
    ))

    @app.get("/qs-next/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        cards = (
            ("cs", "彻底解决问题", "软件 / 硬件 / 机械，可跨领域"),
            ("missed-test", "漏测分析问题", "仅软件"),
            ("software-assessment", "软件问题考核", "仅软件；必须关联正式来源"),
        )
        body = '<h1>质量场景 · 三入口</h1><p class="small">原有工作台保持不变。这里通过原始材料 ID 查看五维候选并进入人工审核。</p><div class="grid">'
        for wb, title, note in cards:
            body += ('<section class="card"><h2>' + _escape(title) + '</h2>'
                     '<p class="small">' + _escape(note) + '</p>'
                     '<label>原材料 ID</label><input id="mat-' + wb + '" placeholder="例如 MAT-123">'
                     '<p><button onclick="openEntry(' + _safe_js(wb) + ')">查看候选</button></p></section>')
        body += '</div><p class="note">本入口仅用于独立测试和受控演进；必须使用经过认证与授权的操作会话。未配置生产安全绑定时不可正式部署。</p>'
        return _page("三入口", body, js="""
function openEntry(wb){const id=document.getElementById('mat-'+wb).value.trim();
if(id)location.href='/qs-next/entry/'+encodeURIComponent(wb)+'/'+encodeURIComponent(id);}
""")

    @app.get("/qs-next/entry/{workbench}/{material_id}", response_class=HTMLResponse, include_in_schema=False)
    def entry_page(request: Request, workbench: str, material_id: str):
        try:
            result = preview_one_issue(str(source_db), workbench, material_id)
        except EntryGateError as exc:
            return _page("入口验证失败", '<h1>无法查看</h1><div class="note">'
                         + _escape(exc.code) + '</div>')
        candidate = result.get("candidate")
        body = ('<h1>单问题场景候选</h1><p class="small">入口 ' + _escape(workbench)
                + '　|　材料 ' + _escape(material_id) + '</p>')
        if candidate is None:
            body += '<div class="note">尚不能生产候选：' + _escape(", ".join(result["blockers"])) + '</div>'
            return _page("待补充来源", body)
        body += ('<section class="card"><h2>候选范围</h2><p class="fact">'
                 '问题追溯：' + _escape(candidate.get("problem_ref_context"))
                 + '　领域：' + _escape(" / ".join(candidate["problem_domains"]))
                 + '　来源覆盖：' + _escape(candidate["source_coverage"])
                 + '</p><p class="note">目前只是待确认预览，不是正式质量场景。</p></section>')
        for dimension, items in candidate["dimensions"].items():
            body += '<section class="card"><h2>' + _escape(dimension) + '</h2><table>'
            for field, value in items.items():
                ev = candidate["field_evidence"].get(field) or {}
                body += ('<tr><td><b>' + _escape(field) + '</b></td><td>'
                         + _escape(value) + '<aside>原字段：' + _escape(ev.get("raw_field",""))
                         + '　材料：' + _escape(ev.get("material_id",""))
                         + '　版本：' + _escape(ev.get("source_revision",""))
                         + '</aside></td></tr>')
            body += '</table></section>'
        conflicts = candidate["conflicts"]
        if conflicts:
            body += ('<section class="card"><h2>冲突待确认</h2><p class="note">'
                     + _escape("，".join(x["field"] for x in conflicts))
                     + '</p></section>')
        body += ('<div class="card"><button id="start">进入人工审核</button>'
                 '<p id="error"></p></div>')
        start_url = f"/api/v2/qs-next/review/v1/start/{quote(workbench, safe='')}/{quote(material_id, safe='')}"
        js = ('const csrf=' + _safe_js(security.csrf_token_for(request))
              + ';const url=' + _safe_js(start_url) + """;
document.getElementById('start').onclick=async ()=>{
const response=await fetch(url,{method:'POST',headers:{'X-CSRF-Token':csrf}});
const data=await response.json();
if(!response.ok){document.getElementById('error').textContent=data.detail||'无法进入审核';return;}
location.href='/qs-next/review/'+encodeURIComponent(data.review_id);
};
""")
        return _page("五维候选", body, js=js)

    @app.get("/qs-next/review/{review_id}", response_class=HTMLResponse, include_in_schema=False)
    def review_page(request: Request, review_id: str):
        try:
            review = store.get(review_id)
            audit = store.audit(review_id)
        except ReviewError as exc:
            return _page("审核记录未找到", '<h1>' + _escape(exc.code) + '</h1>')
        fields = review["effective_fields"]
        names = [key for key in FIELD_ALIASES if key in fields or key in REQUIRED_FIELDS]
        body = ('<h1>场景人工审核</h1><div class="card"><p class="fact"><b>'
                + _escape(review_id) + '</b>　状态：'
                + _escape(review["status"]) + '　版本：'
                + str(review["revision"]) + '</p>'
                '<p>问题：' + _escape(review["problem_ref_context"])
                + '　领域：' + _escape(" / ".join(review["problem_domains"])) + '</p>'
                '<p class="note">确认仅代表审核结论。当前不支持自动发布到原版场景资产。</p></div>')
        body += '<div class="card"><h2>五维字段 · 人工修订</h2><table><tr><th>字段</th><th>有效值 / 可修订值</th><th>来源说明</th></tr>'
        for name in names:
            evidence = review["original_field_evidence"].get(name, [])
            raw = "；".join(x.get("raw_field","") + " / " + x.get("material_id","") for x in evidence)
            body += ('<tr><td>' + _escape(name) + '</td><td><input data-field="'
                     + _escape(name) + '" data-original="' + _escape(fields.get(name, ""))
                     + '" value="' + _escape(fields.get(name, "")) + '"></td><td><aside>'
                     + _escape(raw) + '</aside></td></tr>')
        body += '</table>'
        body += ('<label>人工修改原因（必填）</label><textarea id="reason" placeholder="填写证据核实和修订原因"></textarea>')
        if review["unresolved_conflicts"]:
            body += '<h2>来源冲突 · 必须逐项解决</h2>'
            for key in review["unresolved_conflicts"]:
                body += ('<label><input type="checkbox" class="resolve" value="'
                         + _escape(key) + '" style="width:auto"> 已人工核对 '
                         + _escape(key) + '</label>')
        if review["required_missing"]:
            body += ('<p class="note">仍缺必填：'
                     + _escape("、".join(review["required_missing"])) + '</p>')
        if review["status"]=="IN_REVIEW":
            body += ('<p><button id="save">保存修订</button> '
                     '<button id="confirm">确认候选</button> '
                     '<button id="reject" class="danger">驳回候选</button></p>')
        else:
            body += '<p class="small">该审核已结束，只允许查看。</p>'
        body += '<p id="error"></p><p id="message"></p></div>'
        body += '<div class="card"><h2>审核历史</h2><table><tr><th>版本</th><th>动作</th><th>人员</th><th>原因</th></tr>'
        for event in audit:
            body += ('<tr><td>' + str(event["revision"]) + '</td><td>'
                     + _escape(event["action"]) + '</td><td>'
                     + _escape(event["actor"]) + '</td><td>'
                     + _escape(event["reason"]) + '</td></tr>')
        body += '</table></div>'
        prefix = f"/api/v2/qs-next/review/v1/{quote(review_id, safe='')}"
        js = ("const url=" + _safe_js(prefix)
              + ";const csrf=" + _safe_js(security.csrf_token_for(request))
              + ";const rev=" + str(review["revision"]) + """;
async function submit(action){
const reason=document.getElementById('reason').value.trim();
if(!reason){document.getElementById('error').textContent='必须填写修改原因';return;}
let body={expected_revision:rev,reason};
if(action==='revise'){
 const changes={};document.querySelectorAll('input[data-field]').forEach(el=>{
 if(el.value!==el.dataset.original)changes[el.dataset.field]=el.value;});
 const resolved_conflicts={};document.querySelectorAll('.resolve:checked').forEach(el=>{
 resolved_conflicts[el.value]=reason;const field=document.querySelector('input[data-field="'+el.value+'"]');
 if(field)changes[el.value]=field.value;});
 body.changes=changes;body.resolved_conflicts=resolved_conflicts;
}else{body.decision=action==='confirm'?'CONFIRM':'REJECT';}
const resp=await fetch(url+'/'+(action==='revise'?'revise':'decide'),{
 method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
 body:JSON.stringify(body)});
const data=await resp.json();if(!resp.ok){
 document.getElementById('error').textContent=data.detail||'操作失败';return;}
location.reload();
}
for(const id of ['save','confirm','reject']){const btn=document.getElementById(id);
if(btn)btn.onclick=()=>submit(id==='save'?'revise':id);}
""")
        return _page("人工审核", body, js=js)

    return app


__all__ = ["TrustedReviewSecurity", "create_stage6_app"]
