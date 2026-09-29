"""Hardware system endpoints for release binding and operability."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.hardware_operability import (
    contract_descriptor,
    health_status,
    readiness_status,
)


def create_hardware_operability_router(
    *,
    project_root: str | Path,
    hardware_db_path: str | Path,
) -> APIRouter:
    router = APIRouter(tags=["hardware-operability"])
    root = Path(project_root)
    hardware_db = Path(hardware_db_path)

    @router.get("/health")
    def health() -> dict[str, Any]:
        return health_status()

    @router.get("/ready")
    def ready() -> JSONResponse:
        status, payload = readiness_status(
            root=root,
            hardware_db_path=hardware_db,
        )
        return JSONResponse(payload, status_code=status)

    @router.get("/api/public/hardware/v1/contract")
    def contract() -> dict[str, Any]:
        return contract_descriptor(root)

    return router


__all__ = ["create_hardware_operability_router"]
