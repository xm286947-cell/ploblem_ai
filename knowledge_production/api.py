from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from repositories import JsonArtifactRepository

from .query import KnowledgeQueryService
from .storage_compat import (
    StorageKnowledgeCompatibilityError,
    StorageKnowledgeCompatibilityService,
)


def create_knowledge_api_app(
    repository_root: str,
    *,
    knowledge_release_version: str,
    service_id: str = "storage_knowledge_service",
) -> FastAPI:
    app = FastAPI(title="Unified Knowledge API", version="V1.0")
    compatibility = StorageKnowledgeCompatibilityService(
        KnowledgeQueryService(JsonArtifactRepository(repository_root)),
        knowledge_release_version=knowledge_release_version,
        service_id=service_id,
    )
    app.state.storage_knowledge_compatibility = compatibility

    @app.post("/v1/knowledge/query")
    async def query_knowledge(request: Request):
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

    return app
