"""Read-only query API. Paid Agent execution requires server-side trusted authentication."""
from __future__ import annotations

import hmac
import os
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from services.hardware_assisted_search import HardwareAssistedSearch
from services.hardware_case_ai_retrieval import HardwareCaseAIRetrievalService


class AssistedQueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1024)
    interface: str | None = None
    signal: str | None = None
    device: str | None = None
    limit: int = Field(default=100, ge=1, le=100)


def create_hardware_assisted_query_router(
    service: HardwareAssistedSearch,
    *,
    trusted_agent_token: str | None = None,
    trusted_agent_factory: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/hardware-query/v1", tags=["hardware-query-v1"])

    @router.get("/search")
    def search(
        text: str = "",
        interface: str | None = None,
        signal: str | None = None,
        device: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        # This publicly accessible GET is strictly deterministic. It never
        # instantiates the Provider or reuses a privileged per-request Agent.
        try:
            return service.search(text, interface=interface, signal=signal, device=device, limit=limit)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/search-assisted")
    def search_assisted(
        body: AssistedQueryRequest,
        x_hardware_query_token: str | None = Header(default=None, alias="X-Hardware-Query-Token"),
    ) -> dict[str, Any]:
        # Never expose the token to the browser or accept it in query parameters.
        # Both a trusted server-side credential and explicit NON_PROD gates
        # are required before even constructing a Runtime/Provider instance.
        if (
            os.getenv("HARDWARE_QUERY_AGENT_ENABLED") != "1"
            or os.getenv("HARDWARE_R2_DEPLOYMENT_MODE") != "NON_PROD"
            or os.getenv("HARDWARE_QUERY_AGENT_NONPROD") != "1"
        ):
            raise HTTPException(status_code=503, detail="QUERY_AGENT_NONPROD_GATE_REQUIRED")
        if not trusted_agent_token or trusted_agent_factory is None:
            raise HTTPException(status_code=503, detail="QUERY_AGENT_TRUSTED_GATE_NOT_CONFIGURED")
        if not x_hardware_query_token or not hmac.compare_digest(x_hardware_query_token, trusted_agent_token):
            raise HTTPException(status_code=403, detail="QUERY_AGENT_TRUSTED_AUTH_REQUIRED")
        try:
            agent = trusted_agent_factory()
        except Exception as error:
            code = str(getattr(error, "code", None) or type(error).__name__)
            raise HTTPException(status_code=503, detail=code) from error
        base = service.case_search
        trusted_case_search = HardwareCaseAIRetrievalService(
            base.case_service,
            retrieval_query_service=base.retrieval_query_service,
            consumption_service=base.consumption_service,
            query_agent=agent,
        )
        isolated = HardwareAssistedSearch(trusted_case_search, service.consumption)
        try:
            return isolated.search(
                body.text,
                interface=body.interface,
                signal=body.signal,
                device=body.device,
                limit=body.limit,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return router
