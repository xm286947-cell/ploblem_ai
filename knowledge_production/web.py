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

_ENUM_ZH = {
    "FACT": "事实",
    "CONCEPT": "概念",
    "SOLUTION": "解决方案",
    "DIAGNOSTIC": "诊断",
    "REQUIREMENT": "要求",
    "EXTERNAL_SOURCE": "外部来源",
    "BUSINESS": "业务来源",
    "CONFIRM": "确认",
    "EDIT": "编辑确认",
    "REJECT": "拒绝",
    "CONFIRMED": "已确认",
    "REJECTED": "已拒绝",
    "CANDIDATE": "候选",
    "ACTIVE": "生效",
    "STALE": "失效",
    "DEPRECATED": "已弃用",
    "RECEIVED": "已接收",
    "PARSED": "已解析",
    "FAILED": "失败",
    "VALID": "有效",
    "PARTIAL": "部分有效",
    "INVALID": "无效",
    "NEW": "新知识",
    "POSSIBLE_DUPLICATE": "可能重复",
    "DUPLICATE": "重复",
    "NONE": "无冲突",
    "CONFLICT": "冲突",
    "PARAMETER_DEFINITION": "参数定义",
    "MECHANISM_CONCEPT": "机理概念",
    "CALCULATION_RULE": "计算规则",
    "DIAGNOSTIC_RULE": "诊断规则",
    "DESIGN_RULE": "设计规则",
    "TEST_RULE": "测试规则",
    "CHANGE_IMPACT_RULE": "变更影响规则",
    "APPLICABILITY_RULE": "适用性规则",
}


def _enum_zh(value) -> str:
    raw = getattr(value, "value", value)
    raw = str(raw)
    translated = _ENUM_ZH.get(raw)
    return f"{translated}（{raw}）" if translated else raw


TEMPLATES.env.globals["enum_zh"] = _enum_zh


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
        object_type: str | None = Form(None),
        title: str = Form(""),
        content: str = Form(...),
        scope: str | None = Form(None),
        tags: str | None = Form(None),
        storage_semantic_class: str | None = Form(None),
        review_note: str = Form(""),
    ):
        edits = {"content": content}
        if object_type is not None and object_type.strip():
            edits["object_type"] = object_type.strip()
        if scope is not None:
            parsed_scope = [
                value.strip()
                for value in scope.split(",")
                if value.strip()
            ]
            edits["scope"] = list(dict.fromkeys(parsed_scope))

        parsed_tags = None
        if tags is not None:
            parsed_tags = [
                value.strip()
                for value in tags.split(",")
                if value.strip()
            ]

        selected_semantic = str(storage_semantic_class or "").strip()
        if selected_semantic:
            if parsed_tags is None:
                detail = service.get_candidate_detail(candidate_id)
                parsed_tags = list(detail["candidate"].tags)
            parsed_tags = [
                value
                for value in parsed_tags
                if not value.startswith("storage-semantic:")
            ]
            parsed_tags.append(
                f"storage-semantic:{selected_semantic}"
            )

        if parsed_tags is not None:
            edits["tags"] = list(dict.fromkeys(parsed_tags))
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
