"""P07 Hardware Case base-data management page.

This module is UI-only. All validation, diff, versioning and atomic apply
semantics remain owned by the frozen HC-TREE-M3A API.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


_HERE = Path(__file__).resolve().parent


def create_hardware_tree_import_pages_router(
    *,
    template_dir: str | Path | None = None,
    api_prefix: str = "/api/v2/hardware-cases/tree-imports",
) -> APIRouter:
    templates = Jinja2Templates(directory=str(template_dir or (_HERE / "templates")))
    router = APIRouter()

    @router.get(
        "/p0/hardware-cases/base-data",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def hardware_case_base_data(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "hardware_tree_import_p07.html",
            {
                "api_prefix": api_prefix.rstrip("/"),
                "page_title": "基础数据管理 · 硬件案例库",
            },
        )

    return router
