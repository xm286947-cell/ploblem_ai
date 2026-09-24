from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from repositories import JsonArtifactRepository

from .public_service import PublicKnowledgeError, PublicKnowledgeService
from .query import KnowledgeQueryService
from .storage_compat import (
    StorageKnowledgeCompatibilityError,
    StorageKnowledgeCompatibilityService,
)


def _public_error(exc: PublicKnowledgeError) -> JSONResponse:
    not_found = {
        "KNOWLEDGE_OBJECT_NOT_FOUND",
        "KNOWLEDGE_RELEASE_NOT_FOUND",
        "EVIDENCE_NOT_FOUND",
    }
    conflicts = {
        "CANDIDATE_ID_CONFLICT",
        "EVIDENCE_ID_CONFLICT",
        "IDEMPOTENCY_KEY_CONFLICT",
        "OBJECT_VERSION_CONFLICT",
    }
    status_code = 404 if exc.code in not_found else 409 if exc.code in conflicts else 422
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.code,
                "retryable": False,
            },
        },
    )


def create_knowledge_api_app(
    repository_root: str,
    *,
    knowledge_release_version: str,
    service_id: str = "storage_knowledge_service",
) -> FastAPI:
    app = FastAPI(title="Unified Knowledge API", version="V1.1")
    repository = JsonArtifactRepository(repository_root)
    compatibility = StorageKnowledgeCompatibilityService(
        KnowledgeQueryService(repository),
        knowledge_release_version=knowledge_release_version,
        service_id=service_id,
    )
    public = PublicKnowledgeService(repository)
    app.state.storage_knowledge_compatibility = compatibility
    app.state.public_knowledge = public

    @app.post("/v1/knowledge/query")
    async def query_knowledge(request: Request):
        """Legacy Storage-compatible query endpoint."""
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            return compatibility.handle_query(payload)
        except StorageKnowledgeCompatibilityError as exc:
            request_id = (
                str(payload.get("request_id") or "")
                if isinstance(payload, dict)
                else ""
            )
            response_service_id = (
                str(payload.get("service_id") or service_id)
                if isinstance(payload, dict)
                else service_id
            )
            return JSONResponse(
                status_code=200,
                content={
                    "contract_version": "V1.0",
                    "request_id": request_id,
                    "service_id": response_service_id,
                    "success": False,
                    "result": {"results": [], "total": 0},
                    "evidence": [],
                    "warnings": [],
                    "error": {
                        "code": exc.code,
                        "message": exc.code,
                        "retryable": False,
                    },
                },
            )

    @app.post("/v1/knowledge/evidences")
    async def intake_evidence(request: Request):
        try:
            return public.intake_evidence(await request.json())
        except PublicKnowledgeError as exc:
            return _public_error(exc)

    @app.post("/v1/knowledge/candidates")
    async def intake_candidate(request: Request):
        try:
            return public.intake_candidate(await request.json())
        except PublicKnowledgeError as exc:
            return _public_error(exc)

    @app.post("/v1/knowledge/reviews")
    async def review_candidate(request: Request):
        try:
            return public.review(await request.json())
        except PublicKnowledgeError as exc:
            return _public_error(exc)

    @app.post("/v1/knowledge/publish")
    async def publish_candidate(request: Request):
        try:
            return public.publish(await request.json())
        except PublicKnowledgeError as exc:
            return _public_error(exc)

    @app.post("/v1/knowledge/search")
    async def search_knowledge(request: Request):
        try:
            return public.query(await request.json())
        except PublicKnowledgeError as exc:
            return _public_error(exc)

    @app.get("/v1/knowledge/objects/{knowledge_id}")
    async def get_knowledge_object(
        knowledge_id: str,
        knowledge_release_version: str,
    ):
        try:
            return public.get(knowledge_release_version, knowledge_id)
        except PublicKnowledgeError as exc:
            return _public_error(exc)

    @app.get("/v1/knowledge/evidences/{evidence_id}")
    async def resolve_evidence(
        evidence_id: str,
        knowledge_release_version: str,
    ):
        try:
            return public.resolve_evidence(
                knowledge_release_version,
                evidence_id,
            )
        except PublicKnowledgeError as exc:
            return _public_error(exc)

    return app
