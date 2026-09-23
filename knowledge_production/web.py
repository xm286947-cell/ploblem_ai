from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from repositories import JsonArtifactRepository

from .processing import KnowledgeProcessingError, KnowledgeProcessingService


BASE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=BASE / "templates")


def create_processing_app(repository_root: str | Path) -> FastAPI:
    app = FastAPI(title="Knowledge Processing", version="0.1")
    service = KnowledgeProcessingService(
        JsonArtifactRepository(repository_root)
    )
    app.state.knowledge_processing_service = service

    def candidate_context(
        request: Request,
        candidate_id: str,
        *,
        error: str | None = None,
        message: str | None = None,
    ):
        detail = service.get_candidate_detail(candidate_id)
        return {
            "request": request,
            "detail": detail,
            "error": error,
            "message": message,
        }

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/knowledge-production/sources")

    @app.get("/knowledge-production", include_in_schema=False)
    def knowledge_root():
        return RedirectResponse("/knowledge-production/sources")

    @app.get(
        "/knowledge-production/sources",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def sources(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "kp_sources.html",
            {"sources": service.list_sources()},
        )

    @app.get(
        "/knowledge-production/candidates",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def candidates(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "kp_candidates.html",
            {"candidates": service.list_candidates()},
        )

    @app.get(
        "/knowledge-production/candidates/{candidate_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def candidate_detail(request: Request, candidate_id: str):
        try:
            context = candidate_context(request, candidate_id)
        except KnowledgeProcessingError as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "kp_error.html",
                {"error": exc.code},
                status_code=404,
            )
        return TEMPLATES.TemplateResponse(
            request,
            "kp_candidate_detail.html",
            context,
        )

    @app.post(
        "/knowledge-production/candidates/{candidate_id}/evaluate",
        include_in_schema=False,
    )
    def evaluate_candidate(candidate_id: str):
        service.evaluate(candidate_id)
        return RedirectResponse(
            f"/knowledge-production/candidates/{candidate_id}",
            status_code=303,
        )

    @app.post(
        "/knowledge-production/candidates/{candidate_id}/confirm",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def confirm_candidate(
        request: Request,
        candidate_id: str,
        evaluation_id: str = Form(...),
        reviewed_by: str = Form(...),
        review_note: str = Form(""),
    ):
        try:
            service.confirm(
                candidate_id,
                evaluation_id,
                reviewed_by=reviewed_by,
                reviewed_at=datetime.now(timezone.utc),
                review_note=review_note or None,
            )
        except KnowledgeProcessingError as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "kp_candidate_detail.html",
                candidate_context(request, candidate_id, error=exc.code),
                status_code=409,
            )
        return RedirectResponse(
            f"/knowledge-production/candidates/{candidate_id}",
            status_code=303,
        )

    @app.post(
        "/knowledge-production/candidates/{candidate_id}/edit",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def edit_candidate(
        request: Request,
        candidate_id: str,
        evaluation_id: str = Form(...),
        reviewed_by: str = Form(...),
        title: str = Form(""),
        content: str = Form(...),
        review_note: str = Form(""),
    ):
        edits = {"content": content}
        if title.strip():
            edits["title"] = title.strip()
        try:
            service.edit(
                candidate_id,
                evaluation_id,
                edits,
                reviewed_by=reviewed_by,
                reviewed_at=datetime.now(timezone.utc),
                review_note=review_note or None,
            )
        except KnowledgeProcessingError as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "kp_candidate_detail.html",
                candidate_context(request, candidate_id, error=exc.code),
                status_code=409,
            )
        return RedirectResponse(
            f"/knowledge-production/candidates/{candidate_id}",
            status_code=303,
        )

    @app.post(
        "/knowledge-production/candidates/{candidate_id}/reject",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def reject_candidate(
        request: Request,
        candidate_id: str,
        evaluation_id: str = Form(...),
        reviewed_by: str = Form(...),
        review_note: str = Form(""),
    ):
        try:
            service.reject(
                candidate_id,
                evaluation_id,
                reviewed_by=reviewed_by,
                reviewed_at=datetime.now(timezone.utc),
                review_note=review_note or None,
            )
        except KnowledgeProcessingError as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "kp_candidate_detail.html",
                candidate_context(request, candidate_id, error=exc.code),
                status_code=409,
            )
        return RedirectResponse(
            f"/knowledge-production/candidates/{candidate_id}",
            status_code=303,
        )

    @app.post(
        "/knowledge-production/candidates/{candidate_id}/publish",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def publish_candidate(
        request: Request,
        candidate_id: str,
        published_by: str = Form(...),
    ):
        try:
            service.publish(
                candidate_id,
                published_by=published_by,
                published_at=datetime.now(timezone.utc),
            )
        except KnowledgeProcessingError as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "kp_candidate_detail.html",
                candidate_context(request, candidate_id, error=exc.code),
                status_code=409,
            )
        return RedirectResponse(
            "/knowledge-production/published",
            status_code=303,
        )

    @app.get(
        "/knowledge-production/reviews",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def reviews(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "kp_reviews.html",
            {"reviews": service.list_reviews()},
        )

    @app.get(
        "/knowledge-production/published",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def published(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "kp_published.html",
            {"objects": service.list_published()},
        )

    return app
