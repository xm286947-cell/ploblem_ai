"""P1 forward risk-assessment page shell."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


_HERE = Path(__file__).resolve().parent


def create_p1_router(*, api_prefix: str = "/api/v2") -> APIRouter:
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    router = APIRouter()

    @router.get("/p1/risk-assessment", response_class=HTMLResponse, include_in_schema=False)
    async def risk_assessment(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "p1_risk_assessment.html",
            {"page_title": "正向质量风险评估 · P1", "api_prefix": api_prefix.rstrip("/")},
        )

    @router.get("/p1/product-reports", response_class=HTMLResponse, include_in_schema=False)
    async def product_reports(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "p1_product_reports.html", {"page_title": "产品质量综合报告", "api_prefix": api_prefix.rstrip("/")})

    return router
