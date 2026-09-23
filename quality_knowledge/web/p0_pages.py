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


# Convenience for hosts that prefer a module-level router while still keeping
# inclusion explicit in their main.py.
p0_insights_router = create_p0_insights_router()
