"""Additional read-only API; public deterministic knowledge v1 remains intact."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from services.hardware_assisted_search import HardwareAssistedSearch


def create_hardware_assisted_query_router(service: HardwareAssistedSearch) -> APIRouter:
    router = APIRouter(prefix="/api/hardware-query/v1", tags=["hardware-query-v1"])

    @router.get("/search")
    def search(text: str = "", interface: str | None = None,
               signal: str | None = None, device: str | None = None,
               limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        try:
            return service.search(text, interface=interface, signal=signal, device=device, limit=limit)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return router
