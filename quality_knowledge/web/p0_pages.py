"""P0 quality-insight page router.

The router deliberately contains no business aggregation logic.  It is a thin
HTML shell around the frozen /api/v2 contracts so it can be mounted by the
main application without changing the legacy insight page.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .missed_test_adapter import build_missed_test_rows
from .itr_recovery_adapter import build_itr_recovery_rows
from .itr_resolution_adapter import build_itr_resolution_rows
from .software_assessment_adapter import build_software_assessment_rows
from .current_problem_associations import build_current_problem_associations
from .overall_navigation import (
    append_overall_return_state,
    normalize_overall_return_state,
    overall_navigation_asset_version,
)


_HERE = Path(__file__).resolve().parent


def _p04_return_url(raw_context: str | None) -> str:
    base = "/p0/quality-scenario-insights"
    if raw_context is None:
        return base + "?p04_reset=1"
    if len(raw_context) > 4096:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    try:
        context = json.loads(raw_context)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    if not isinstance(context, dict) or context.get("contract") != "p04-query-context/v1":
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    view = context.get("view")
    modes = {
        "PRODUCT": {"LIFECYCLE_X_BUSINESS_ACTIVITY"},
        "CUSTOMER": {"PRODUCT_X_BUSINESS_ACTIVITY", "PRODUCT_X_QUALITY_FOCUS"},
        "INDUSTRY": {"CUSTOMER_X_PRODUCT_OR_FAMILY", "BUSINESS_ACTIVITY_X_QUALITY_FOCUS"},
    }
    selected = context.get("selected_object")
    filters = context.get("filters")
    page = context.get("page")
    if view not in modes or context.get("matrix_mode") not in modes[view]:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    if selected is not None and (
        not isinstance(selected, dict)
        or not isinstance(selected.get("selector_ref"), str)
        or not selected["selector_ref"].strip()
        or len(selected["selector_ref"]) > 300
        or set(selected) != {"selector_ref"}
    ):
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    allowed_filters = {"lifecycle", "business_activity", "quality_focus"}
    if not isinstance(filters, dict) or set(filters) - allowed_filters or any(
        not isinstance(value, str) or len(value) > 200 for value in filters.values()
    ):
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 100000:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    if set(context) != {"contract", "view", "selected_object", "filters", "matrix_mode", "page"}:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    return base + "?" + urlencode({"p04_context": json.dumps(context, ensure_ascii=False, separators=(",", ":"))})


def _normalize_p04_url_context(raw_context: str) -> str:
    """Validate a browser URL context and fill its documented default fields."""
    if len(raw_context) > 4096:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    try:
        context = json.loads(raw_context)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    allowed = {
        "contract", "view", "selected_object", "selected_object_ref", "filters",
        "matrix_mode", "page", "page_size",
    }
    if not isinstance(context, dict) or set(context) - allowed:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    if context.get("contract") not in (None, "p04-query-context/v1"):
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    view = context.get("view", "PRODUCT")
    modes = {
        "PRODUCT": {"LIFECYCLE_X_BUSINESS_ACTIVITY"},
        "CUSTOMER": {"PRODUCT_X_BUSINESS_ACTIVITY", "PRODUCT_X_QUALITY_FOCUS"},
        "INDUSTRY": {"CUSTOMER_X_PRODUCT_OR_FAMILY", "BUSINESS_ACTIVITY_X_QUALITY_FOCUS"},
    }
    if view not in modes:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    selected = context.get("selected_object", context.get("selected_object_ref"))
    if isinstance(selected, str):
        selected = {"selector_ref": selected} if selected else None
    if selected is not None and (
        not isinstance(selected, dict)
        or set(selected) != {"selector_ref"}
        or not isinstance(selected.get("selector_ref"), str)
        or not selected["selector_ref"].strip()
        or len(selected["selector_ref"]) > 300
    ):
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    filters = context.get("filters", {})
    allowed_filters = {"lifecycle", "business_activity", "quality_focus"}
    if not isinstance(filters, dict) or set(filters) - allowed_filters or any(
        not isinstance(value, str) or len(value) > 200 for value in filters.values()
    ):
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    matrix_mode = context.get("matrix_mode") or next(iter(modes[view]))
    if matrix_mode not in modes[view]:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    page = context.get("page", 1)
    page_size = context.get("page_size", 20)
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 100000:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise HTTPException(status_code=400, detail="INVALID_RETURN_CONTEXT")
    normalized = {
        "view": view,
        "selected_object": selected,
        "filters": filters,
        "matrix_mode": matrix_mode,
        "page": page,
        "page_size": page_size,
    }
    if context.get("contract") is not None:
        normalized["contract"] = context["contract"]
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _safe_current_problem_return_url(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if len(value) > 4096 or "\r" in value or "\n" in value:
        raise HTTPException(status_code=400, detail="INVALID_ISSUE_RETURN_CONTEXT")
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or parsed.path not in {
            "/p0/issues",
            "/p0/missed-test-analysis",
            "/p0/itr-recovery",
            "/p0/itr-resolution",
            "/p0/software-assessment",
        }
    ):
        raise HTTPException(status_code=400, detail="INVALID_ISSUE_RETURN_CONTEXT")
    return parsed.path + (("?" + parsed.query) if parsed.query else "")


def _safe_issue_return_url(raw: str | None) -> str:
    """Accept only an in-product P0 issue-detail return target."""
    value = str(raw or "").strip()
    if not value:
        return ""
    if len(value) > 4096 or "\r" in value or "\n" in value:
        raise HTTPException(status_code=400, detail="INVALID_CASE_RETURN_CONTEXT")
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment not in {"", "repeat-risk"}
        or not parsed.path.startswith("/p0/issues/")
        or parsed.path == "/p0/issues/"
    ):
        raise HTTPException(status_code=400, detail="INVALID_CASE_RETURN_CONTEXT")
    return (
        parsed.path
        + (("?" + parsed.query) if parsed.query else "")
        + (("#" + parsed.fragment) if parsed.fragment else "")
    )


def create_p0_insights_router(
    *,
    template_dir: str | Path | None = None,
    static_dir: str | Path | None = None,
    api_prefix: str = "/api/v2",
    scenario_detail_service: Any | None = None,
    hardware_case_host_role: str | None = None,
) -> APIRouter:
    """Return an isolated router for ``/p0/insights``.

    The host application owns router inclusion and API implementation.  The
    optional directories make the factory easy to exercise from a test app or
    a packaged release.
    """
    templates = Jinja2Templates(directory=str(template_dir or (_HERE / "templates")))
    assets = Path(static_dir or (_HERE / "static"))
    templates.env.globals["overall_navigation_asset_version"] = overall_navigation_asset_version(assets)
    hardware_host_role = str(
        hardware_case_host_role or "CONSUMER"
    ).strip().upper()
    if hardware_host_role not in {"CONSUMER", "MAINTAINER"}:
        raise ValueError("HARDWARE_CASE_HOST_ROLE_INVALID")

    def resolve_hardware_role(request: Request) -> str:
        # Reuse the existing host-role model. Query/browser state is never
        # authority; an unbound host fails closed to CONSUMER.
        return hardware_host_role

    def require_hardware_maintainer() -> None:
        if hardware_host_role != "MAINTAINER":
            raise HTTPException(
                status_code=403,
                detail="HARDWARE_CASE_MAINTAINER_REQUIRED",
            )

    def p04_js_asset_version() -> str:
        asset = assets / "p04_insights.js"
        try:
            content = asset.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="P04_STATIC_ASSET_UNAVAILABLE",
            ) from exc
        return hashlib.sha256(content).hexdigest()

    router = APIRouter()

    def resolve_p04_return_context(request: Request) -> tuple[str, str, str]:
        requested_return = request.query_params.get("return_to", "")
        return_to = requested_return if requested_return in {
            "/p0/quality-scenario-insights",
            "/p0/insights/p04",
        } else "/p0/quality-scenario-insights"
        raw_url_context = request.query_params.get("p04_context")
        raw_return_context = request.query_params.get("return_context")
        overall_state = normalize_overall_return_state(
            request.query_params.get("overall_return_state")
        )
        if raw_url_context is None and raw_return_context is None:
            return_url = return_to + "?p04_reset=1"
            return return_to, "", append_overall_return_state(return_url, overall_state)

        if raw_url_context is not None:
            _normalize_p04_url_context(raw_url_context)
            safe_context = raw_url_context
        else:
            # Legacy P03 callers use the stricter frozen return-context contract.
            _p04_return_url(raw_return_context)
            safe_context = json.dumps(json.loads(raw_return_context), ensure_ascii=False, separators=(",", ":"))
        return_url = return_to + "?" + urlencode({"p04_context": safe_context})
        return_url = append_overall_return_state(return_url, overall_state)
        return return_to, safe_context, return_url

    @router.get("/p0/insights", response_class=HTMLResponse, include_in_schema=False)
    async def p0_insights(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_insights.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "质量洞察 · 质量能力"},
        )

    @router.get("/p0/quality-scenario-insights", response_class=HTMLResponse, include_in_schema=False)
    async def p04_quality_scenario_insights(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p04_insights.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "p04_api_prefix": "/api/v2/quality-scenario-insights/v1",
                "p04_js_asset_version": p04_js_asset_version(),
                "page_title": "质量画像与洞察 · QualityScenario",
            },
        )

    @router.get("/p0/insights/p04", response_class=HTMLResponse, include_in_schema=False)
    async def p04_quality_scenario_insights_alias(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p04_insights.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "p04_api_prefix": "/api/v2/quality-scenario-insights/v1",
                "p04_js_asset_version": p04_js_asset_version(),
                "page_title": "质量画像与洞察 · QualityScenario",
            },
        )

    @router.get("/p0/issues", response_class=HTMLResponse, include_in_schema=False)
    async def p0_issues(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_issues.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "问题工作台 · 质量能力"},
        )

    @router.get("/p0/batch-analysis", response_class=HTMLResponse, include_in_schema=False)
    async def p0_batch_analysis(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_batch_analysis.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "批量 AI 分析 · 质量能力"},
        )

    @router.get("/p0/missed-test-analysis", response_class=HTMLResponse, include_in_schema=False)
    async def p0_missed_test_analysis(request: Request) -> HTMLResponse:
        legacy = getattr(request.app.state, "legacy_quality_issue_services", None)
        service = getattr(legacy, "knowledge_issue_service", None) if legacy is not None else None
        if service is None:
            status = getattr(request.app.state, "legacy_quality_issue_status", {}) or {}
            raise HTTPException(
                status_code=503,
                detail=str(status.get("code") or "LEGACY_DB_UNAVAILABLE"),
            )
        q = (request.query_params.get("q") or "").strip()
        analysis_status = (request.query_params.get("analysis_status") or "").strip().upper()
        rows = build_missed_test_rows(
            service,
            q=q,
            analysis_status=analysis_status,
            detail_prefix="/p0/issues",
            return_path="/p0/missed-test-analysis",
            detail_anchor="analysis",
        )
        return templates.TemplateResponse(
            request,
            "p0_missed_test_analysis.html",
            {
                "items": rows,
                "total": len(rows),
                "q": q,
                "analysis_status": analysis_status,
                "page_title": "软件问题漏测分析 · 质量能力",
            },
        )

    @router.get("/p0/itr-recovery", response_class=HTMLResponse, include_in_schema=False)
    async def p0_itr_recovery(request: Request) -> HTMLResponse:
        legacy = getattr(request.app.state, "legacy_quality_issue_services", None)
        service = getattr(legacy, "knowledge_issue_service", None) if legacy is not None else None
        if service is None:
            status = getattr(request.app.state, "legacy_quality_issue_status", {}) or {}
            raise HTTPException(
                status_code=503,
                detail=str(status.get("code") or "LEGACY_DB_UNAVAILABLE"),
            )
        q = (request.query_params.get("q") or "").strip()
        rows = build_itr_recovery_rows(
            service,
            q=q,
            detail_prefix="/p0/issues",
            return_path="/p0/itr-recovery",
        )
        return templates.TemplateResponse(
            request,
            "p0_itr_recovery_workbench.html",
            {
                "items": rows,
                "total": len(rows),
                "with_recovery": sum(
                    1 for row in rows
                    if row.get("source_fact_status") == "SOURCE_FACT_PRESENT"
                ),
                "q": q,
                "page_title": "ITR / 现场恢复 · 质量能力",
            },
        )

    @router.get("/p0/itr-resolution", response_class=HTMLResponse, include_in_schema=False)
    async def p0_itr_resolution(request: Request) -> HTMLResponse:
        legacy = getattr(request.app.state, "legacy_quality_issue_services", None)
        material_repository = getattr(legacy, "material_repository", None) if legacy is not None else None
        if material_repository is None:
            status = getattr(request.app.state, "legacy_quality_issue_status", {}) or {}
            raise HTTPException(
                status_code=503,
                detail=str(status.get("code") or "LEGACY_DB_UNAVAILABLE"),
            )
        q = (request.query_params.get("q") or "").strip()
        rows = build_itr_resolution_rows(
            material_repository,
            q=q,
            detail_prefix="/p0/issues",
            return_path="/p0/itr-resolution",
        )
        linked = sum(1 for row in rows if row.get("knowledge_id"))
        return templates.TemplateResponse(
            request,
            "p0_itr_resolution_workbench.html",
            {
                "items": rows,
                "total": len(rows),
                "linked": linked,
                "unlinked": len(rows) - linked,
                "source_state_count": sum(
                    1 for row in rows if row.get("business_status")
                ),
                "q": q,
                "page_title": "ITR 彻底解决 · 质量能力",
            },
        )

    @router.get("/p0/software-assessment", response_class=HTMLResponse, include_in_schema=False)
    async def p0_software_assessment(request: Request) -> HTMLResponse:
        legacy = getattr(request.app.state, "legacy_quality_issue_services", None)
        material_repository = getattr(legacy, "material_repository", None) if legacy is not None else None
        if material_repository is None:
            status = getattr(request.app.state, "legacy_quality_issue_status", {}) or {}
            raise HTTPException(
                status_code=503,
                detail=str(status.get("code") or "LEGACY_DB_UNAVAILABLE"),
            )
        q = (request.query_params.get("q") or "").strip()
        rows = build_software_assessment_rows(
            material_repository,
            q=q,
            detail_prefix="/p0/issues",
            return_path="/p0/software-assessment",
        )
        linked = sum(1 for row in rows if row.get("knowledge_id"))
        return templates.TemplateResponse(
            request,
            "p0_software_assessment_workbench.html",
            {
                "items": rows,
                "total": len(rows),
                "linked": linked,
                "unlinked": len(rows) - linked,
                "source_state": sum(
                    1 for row in rows
                    if row.get("assessment_status") or row.get("assessment_result")
                ),
                "q": q,
                "page_title": "软件问题考核 · 质量能力",
            },
        )

    @router.get("/api/v2/issues/{knowledge_id}/workbench-relations")
    async def current_problem_workbench_relations(
        request: Request,
        knowledge_id: str,
    ) -> dict[str, Any]:
        """Read-only public projection for Canonical Problem workbench relations."""

        legacy = getattr(request.app.state, "legacy_quality_issue_services", None)
        service = (
            getattr(legacy, "knowledge_issue_service", None)
            if legacy is not None
            else None
        )
        material_repository = (
            getattr(legacy, "material_repository", None)
            if legacy is not None
            else None
        )
        if service is None or material_repository is None:
            status = getattr(request.app.state, "legacy_quality_issue_status", {}) or {}
            raise HTTPException(
                status_code=503,
                detail=str(status.get("code") or "LEGACY_DB_UNAVAILABLE"),
            )
        projection = build_current_problem_associations(
            service,
            material_repository,
            knowledge_id,
            p0=True,
        )
        if projection.get("identity") is None and service.get_issue(knowledge_id) is None:
            raise HTTPException(status_code=404, detail="CURRENT_PROBLEM_NOT_FOUND")
        return projection

    @router.get("/p0/issues/{knowledge_id}", response_class=HTMLResponse, include_in_schema=False)
    async def p0_issue_detail(request: Request, knowledge_id: str) -> HTMLResponse:
        if scenario_detail_service is not None and str(knowledge_id).startswith("QS-"):
            return_to, p04_context, return_url = resolve_p04_return_context(request)
            scenario = scenario_detail_service.scenario_detail(knowledge_id)
            if scenario is None:
                raise HTTPException(status_code=404, detail="QUALITY_SCENARIO_NOT_FOUND")
            return templates.TemplateResponse(
                request,
                "p0_quality_scenario_detail.html",
                {
                    "api_prefix": api_prefix.rstrip("/"),
                    "scenario_id": knowledge_id,
                    "scenario": scenario,
                    "return_to": return_to,
                    "return_url": return_url,
                    "return_context": p04_context,
                    "overall_return_state": request.query_params.get("overall_return_state", ""),
                    "page_title": "场景详情 · 质量能力",
                },
            )
        requested_return = request.query_params.get("return_to", "")
        if requested_return in {
            "/p0/quality-scenario-insights",
            "/p0/insights/p04",
        }:
            return_to = requested_return
        elif requested_return:
            return_to = _safe_current_problem_return_url(requested_return)
        else:
            return_to = ""
        overall_state = normalize_overall_return_state(
            request.query_params.get("overall_return_state")
        )
        if return_to:
            return_to = append_overall_return_state(return_to, overall_state)
        legacy = getattr(request.app.state, "legacy_quality_issue_services", None)
        legacy_service = getattr(legacy, "knowledge_issue_service", None) if legacy is not None else None
        material_repository = getattr(legacy, "material_repository", None) if legacy is not None else None
        associations = build_current_problem_associations(
            legacy_service,
            material_repository,
            knowledge_id,
            p0=True,
        )
        return templates.TemplateResponse(
            request,
            "p0_issue_detail.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "knowledge_id": knowledge_id,
                "page_title": "问题详情 · 质量能力",
                "return_to": return_to,
                "current_problem_associations": associations,
            },
        )

    @router.get("/p0/quality-scenarios/{scenario_id}", response_class=HTMLResponse, include_in_schema=False)
    async def p0_quality_scenario_detail(request: Request, scenario_id: str) -> HTMLResponse:
        if scenario_detail_service is None:
            raise HTTPException(status_code=404, detail="QUALITY_SCENARIO_NOT_FOUND")
        scenario = scenario_detail_service.scenario_detail(scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail="QUALITY_SCENARIO_NOT_FOUND")
        return_to, p04_context, return_url = resolve_p04_return_context(request)
        return templates.TemplateResponse(
            request,
            "p0_quality_scenario_detail.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "scenario_id": scenario_id,
                "scenario": scenario,
                "return_to": return_to,
                "return_url": return_url,
                "return_context": p04_context,
                "overall_return_state": request.query_params.get("overall_return_state", ""),
                "page_title": "场景详情 · 质量能力",
            },
        )

    @router.get("/p0/quality-scenario-sources/{source_ref:path}", response_class=HTMLResponse, include_in_schema=False)
    async def p0_quality_scenario_source(request: Request, source_ref: str) -> HTMLResponse:
        if scenario_detail_service is None:
            raise HTTPException(status_code=404, detail="SOURCE_REFERENCE_NOT_FOUND")
        trace = scenario_detail_service.source_trace(source_ref)
        if trace is None:
            raise HTTPException(status_code=404, detail="SOURCE_REFERENCE_NOT_FOUND")
        return_to, p04_context, return_url = resolve_p04_return_context(request)
        return templates.TemplateResponse(
            request,
            "p0_quality_scenario_source.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "source_ref": source_ref,
                "trace": trace,
                "return_to": return_to,
                "return_url": return_url,
                "return_context": p04_context,
                "overall_return_state": request.query_params.get("overall_return_state", ""),
                "page_title": "来源追溯 · 质量能力",
            },
        )

    @router.get("/p0/cases", response_class=HTMLResponse, include_in_schema=False)
    async def p0_cases(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_cases.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "重大问题案例库"},
        )

    @router.get("/p0/major-production", response_class=HTMLResponse, include_in_schema=False)
    async def major_production(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "major_production.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "重大问题知识生产"},
        )

    @router.get("/p0/cases/{case_id}", response_class=HTMLResponse, include_in_schema=False)
    async def p0_case_detail(request: Request, case_id: str) -> HTMLResponse:
        return_url = _safe_issue_return_url(request.query_params.get("return_to"))
        return templates.TemplateResponse(
            request,
            "p0_case_detail.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "case_id": case_id,
                "return_url": return_url,
                "page_title": "重大问题案例详情",
            },
        )

    @router.get("/p0/hardware-cases", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "hardware_case_home.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "硬件案例库",
                "hardware_role": resolve_hardware_role(request),
                "hardware_active": "home",
            },
        )

    @router.get("/p0/hardware-cases/tree", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_tree(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "hardware_case_tree.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "双树导航 · 硬件案例库",
                "hardware_role": resolve_hardware_role(request),
                "hardware_active": "tree",
            },
        )

    @router.get("/p0/hardware-cases/search", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_search(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "hardware_case_search.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "案例搜索 · 硬件案例库",
                "hardware_role": resolve_hardware_role(request),
                "hardware_active": "search",
            },
        )

    @router.get("/p0/hardware-cases/review", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_review_queue(request: Request) -> HTMLResponse:
        require_hardware_maintainer()
        return templates.TemplateResponse(
            request,
            "hardware_case_review.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "案例确认 · 硬件案例库",
                "hardware_role": "MAINTAINER",
                "hardware_active": "review",
                "case_id": "",
            },
        )

    @router.get("/p0/hardware-cases/{case_id}/review", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_review(request: Request, case_id: str) -> HTMLResponse:
        require_hardware_maintainer()
        return templates.TemplateResponse(
            request,
            "hardware_case_review.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "案例确认 · 硬件案例库",
                "hardware_role": "MAINTAINER",
                "hardware_active": "review",
                "case_id": case_id,
            },
        )

    @router.get("/p0/hardware-cases/base-data", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_base_data(request: Request) -> HTMLResponse:
        require_hardware_maintainer()
        return templates.TemplateResponse(
            request,
            "hardware_tree_import.html",
            {
                "import_api_prefix": "/api/v2/hardware-cases/tree-imports",
                "tree_api_prefix": "/api/v2/hardware-cases",
                "page_title": "基础数据管理 · 硬件案例库",
                "hardware_role": "MAINTAINER",
                "hardware_active": "base-data",
            },
        )

    @router.get("/p0/hardware-cases/intake", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_intake(request: Request) -> HTMLResponse:
        require_hardware_maintainer()
        return templates.TemplateResponse(
            request,
            "hardware_case_intake.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "知识导入 · 硬件案例库",
                "hardware_role": "MAINTAINER",
                "hardware_active": "intake",
            },
        )

    @router.get("/p0/hardware-cases/{case_id}", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_detail(request: Request, case_id: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "hardware_case_detail.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "案例详情 · 硬件案例库",
                "hardware_role": resolve_hardware_role(request),
                "hardware_active": "",
                "case_id": case_id,
            },
        )

    @router.get("/p0/settings", response_class=HTMLResponse, include_in_schema=False)
    async def p0_settings(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_console.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "产品与字段映射", "mode": "settings"},
        )

    @router.get("/p0/data-intake", response_class=HTMLResponse, include_in_schema=False)
    async def p0_data_intake(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_console.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "数据接入", "mode": "intake"},
        )

    @router.get("/p0/static/{asset_name}", include_in_schema=False)
    async def p0_static(asset_name: str) -> FileResponse:
        # Asset names are intentionally one path component: no traversal and
        # no arbitrary file serving from the release directory.
        if Path(asset_name).name != asset_name or Path(asset_name).suffix not in {".css", ".js"}:
            raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
        target = assets / asset_name
        if not target.is_file():
            raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
        media = "text/css" if target.suffix == ".css" else "text/javascript"
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return FileResponse(
            target,
            media_type=media,
            headers={
                "X-Content-SHA256": digest,
                "ETag": f'"{digest}"',
                "Cache-Control": "no-store",
            },
        )

    return router


def create_hardware_case_pages_router(
    *,
    template_dir: str | Path | None = None,
    static_dir: str | Path | None = None,
    hardware_case_host_role: str | None = None,
) -> APIRouter:
    """Return only Hardware Case P01-P07 pages plus their shared static route.

    The implementation reuses the frozen page definitions above.  Filtering at
    composition time avoids duplicating P01-P07 behavior while allowing a
    Hardware Case-only host to omit unrelated product pages and APIs.
    """
    full = create_p0_insights_router(
        template_dir=template_dir,
        static_dir=static_dir,
        hardware_case_host_role=hardware_case_host_role,
    )
    router = APIRouter()
    router.routes.extend(
        route
        for route in full.routes
        if str(getattr(route, "path", "")).startswith("/p0/hardware-cases")
        or str(getattr(route, "path", "")) == "/p0/static/{asset_name}"
    )
    return router


# Convenience for hosts that prefer a module-level router while still keeping
# inclusion explicit in their main.py.
p0_insights_router = create_p0_insights_router()
