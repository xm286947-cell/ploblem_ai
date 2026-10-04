"""Read-only HTTP surface for hardware-knowledge-consumption/v1."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

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

    @router.get("/objects/{knowledge_id}")
    def get_object(knowledge_id: str) -> dict[str, Any]:
        try:
            result = service.get(knowledge_id)
        except HardwareKnowledgeConsumptionError as error:
            raise _http_error(error) from error
        if result is None:
            raise HTTPException(status_code=404, detail="KNOWLEDGE_NOT_FOUND")
        return result

    return router


__all__ = ["PUBLIC_PREFIX", "create_hardware_knowledge_consumption_router"]
