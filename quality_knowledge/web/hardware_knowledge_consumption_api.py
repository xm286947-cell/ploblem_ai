"""Read-only HTTP surface for hardware-knowledge-consumption/v1."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse

from services.hardware_knowledge_consumption import (
    HardwareKnowledgeConsumptionError,
    HardwareKnowledgeConsumptionService,
)


PUBLIC_PREFIX = "/api/public/hardware-knowledge/v1"


def _http_error(error: HardwareKnowledgeConsumptionError) -> HTTPException:
    code = error.code
    if code == "KNOWLEDGE_NOT_FOUND":
        status = 404
    elif code == "SEARCH_LIMIT_INVALID":
        status = 400
    elif code.startswith("CONSUMPTION_PROJECTION_"):
        status = 503
    else:
        status = 503
    return HTTPException(status_code=status, detail=code)


def create_hardware_knowledge_consumption_router(
    service: HardwareKnowledgeConsumptionService,
    *,
    source_store: Any | None = None,
    knowledge_adapter: Any | None = None,
    assisted_query_service: Any | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix=PUBLIC_PREFIX,
        tags=["hardware-knowledge-consumption-v1"],
    )

    @router.get("/search")
    def search(
        text: str = "",
        knowledge_id: str | None = None,
        business_case_id: str | None = None,
        source_domain: str | None = None,
        source_object_type: str | None = None,
        interface: str | None = None,
        signal: str | None = None,
        device: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        try:
            return service.search(
                text,
                knowledge_id=knowledge_id,
                business_case_id=business_case_id,
                source_domain=source_domain,
                source_object_type=source_object_type,
                interface=interface,
                signal=signal,
                device=device,
                limit=limit,
            )
        except HardwareKnowledgeConsumptionError as error:
            raise _http_error(error) from error

    @router.get("/assisted-search")
    def assisted_search(text: str = "", limit: int = Query(default=100, ge=1, le=100)) -> dict[str, Any]:
        if assisted_query_service is None:
            raise HTTPException(status_code=503, detail="ASSISTED_QUERY_UNAVAILABLE")
        return assisted_query_service.search(text, limit=limit)

    @router.get("/objects/{knowledge_id}")
    def get_object(knowledge_id: str) -> dict[str, Any]:
        try:
            result = service.get(knowledge_id)
        except HardwareKnowledgeConsumptionError as error:
            raise _http_error(error) from error
        if result is None:
            raise HTTPException(status_code=404, detail="KNOWLEDGE_NOT_FOUND")
        return result

    def resolve_local_evidence(business_case_id: str, evidence_id: str) -> tuple[str, dict[str, Any]]:
        if source_store is None or knowledge_adapter is None:
            raise HTTPException(status_code=503, detail="EVIDENCE_SOURCE_UNAVAILABLE")
        try:
            source = source_store.get_active_source(business_case_id)
            evidence = knowledge_adapter.resolve_evidence(evidence_id)
        except Exception as error:
            code = str(getattr(error, "code", None) or "EVIDENCE_SOURCE_UNAVAILABLE")
            raise HTTPException(status_code=503, detail=code) from error
        source_info = evidence.get("source") if isinstance(evidence, dict) else None
        metadata = source_info.get("metadata") if isinstance(source_info, dict) else None
        locator = metadata.get("hardware_locator") if isinstance(metadata, dict) else None
        if (
            not isinstance(source_info, dict)
            or str(source_info.get("source_id") or "") != str(source.get("source_id") or "")
            or str(source_info.get("uri") or "") != str(source.get("source_ref") or "")
            or not isinstance(locator, dict)
        ):
            raise HTTPException(status_code=409, detail="EVIDENCE_SOURCE_IDENTITY_MISMATCH")
        return str(source["source_ref"]), locator

    @router.get("/evidence/{evidence_id}/source-preview")
    def evidence_source_preview(
        evidence_id: str,
        business_case_id: str,
        x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role"),
    ) -> dict[str, Any]:
        if str(x_hardware_case_role or "").strip().upper() != "MAINTAINER":
            raise HTTPException(status_code=403, detail="HARDWARE_CASE_MAINTAINER_REQUIRED")
        source_ref, locator = resolve_local_evidence(business_case_id, evidence_id)
        try:
            preview = source_store.preview(source_ref, locator, context_blocks=1)
        except Exception as error:
            code = str(getattr(error, "code", None) or "SOURCE_PREVIEW_FAILED")
            raise HTTPException(status_code=409, detail=code) from error
        return {"business_case_id": business_case_id, "evidence_id": evidence_id, **preview}

    @router.get("/evidence/{evidence_id}/source-file")
    def evidence_source_file(
        evidence_id: str,
        business_case_id: str,
        x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role"),
    ) -> FileResponse:
        if str(x_hardware_case_role or "").strip().upper() != "MAINTAINER":
            raise HTTPException(status_code=403, detail="HARDWARE_CASE_MAINTAINER_REQUIRED")
        source_ref, _ = resolve_local_evidence(business_case_id, evidence_id)
        try:
            meta = source_store.get_metadata(source_ref)
            path = source_store.resolve_path(source_ref)
        except Exception as error:
            code = str(getattr(error, "code", None) or "SOURCE_UNAVAILABLE")
            raise HTTPException(status_code=404, detail=code) from error
        return FileResponse(path, media_type=meta.get("mime_type") or "application/octet-stream", filename=meta.get("display_name") or path.name)

    return router


__all__ = ["PUBLIC_PREFIX", "create_hardware_knowledge_consumption_router"]
