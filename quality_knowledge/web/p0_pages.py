"""P0 quality-insight page router.

The router deliberately contains no business aggregation logic.  It is a thin
HTML shell around the frozen /api/v2 contracts so it can be mounted by the
main application without changing the legacy insight page.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates


_HERE = Path(__file__).resolve().parent


def create_p0_insights_router(
    *,
    template_dir: str | Path | None = None,
    static_dir: str | Path | None = None,
    api_prefix: str = "/api/v2",
) -> APIRouter:
    """Return an isolated router for ``/p0/insights``.

    The host application owns router inclusion and API implementation.  The
    optional directories make the factory easy to exercise from a test app or
    a packaged release.
    """
    templates = Jinja2Templates(directory=str(template_dir or (_HERE / "templates")))
    assets = Path(static_dir or (_HERE / "static"))
    router = APIRouter()

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

    @router.get("/p0/issues/{knowledge_id}", response_class=HTMLResponse, include_in_schema=False)
    async def p0_issue_detail(request: Request, knowledge_id: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_issue_detail.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "knowledge_id": knowledge_id,
                "page_title": "问题详情 · 质量能力",
            },
        )

    @router.get("/p0/cases", response_class=HTMLResponse, include_in_schema=False)
    async def p0_cases(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_cases.html",
            {"api_prefix": api_prefix.rstrip("/"), "page_title": "重大问题案例库"},
        )

    @router.get("/p0/cases/{case_id}", response_class=HTMLResponse, include_in_schema=False)
    async def p0_case_detail(request: Request, case_id: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p0_case_detail.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "case_id": case_id,
                "page_title": "重大问题案例详情",
            },
        )

    @router.get("/p0/hardware-cases", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_home(request: Request) -> HTMLResponse:
        role = "MAINTAINER" if request.query_params.get("role") == "maintainer" else "CONSUMER"
        return templates.TemplateResponse(
            request,
            "hardware_case_home.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "硬件案例库",
                "hardware_role": role,
                "hardware_active": "home",
            },
        )

    @router.get("/p0/hardware-cases/tree", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_tree(request: Request) -> HTMLResponse:
        role = "MAINTAINER" if request.query_params.get("role") == "maintainer" else "CONSUMER"
        return templates.TemplateResponse(
            request,
            "hardware_case_tree.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "双树导航 · 硬件案例库",
                "hardware_role": role,
                "hardware_active": "tree",
            },
        )

    @router.get("/p0/hardware-cases/search", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_search(request: Request) -> HTMLResponse:
        role = "MAINTAINER" if request.query_params.get("role") == "maintainer" else "CONSUMER"
        return templates.TemplateResponse(
            request,
            "hardware_case_search.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "案例搜索 · 硬件案例库",
                "hardware_role": role,
                "hardware_active": "search",
            },
        )

    @router.get("/p0/hardware-cases/review", response_class=HTMLResponse, include_in_schema=False)
    async def hardware_case_review_queue(request: Request) -> HTMLResponse:
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
        role = "MAINTAINER" if request.query_params.get("role") == "maintainer" else "CONSUMER"
        return templates.TemplateResponse(
            request,
            "hardware_case_detail.html",
            {
                "hardware_api_prefix": "/api/v2/hardware-cases",
                "page_title": "案例详情 · 硬件案例库",
                "hardware_role": role,
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
        return FileResponse(target, media_type=media)

    return router


def create_hardware_case_pages_router(
    *,
    template_dir: str | Path | None = None,
    static_dir: str | Path | None = None,
) -> APIRouter:
    """Return only Hardware Case P01-P07 pages plus their shared static route.

    The implementation reuses the frozen page definitions above.  Filtering at
    composition time avoids duplicating P01-P07 behavior while allowing a
    Hardware Case-only host to omit unrelated product pages and APIs.
    """
    full = create_p0_insights_router(
        template_dir=template_dir,
        static_dir=static_dir,
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
