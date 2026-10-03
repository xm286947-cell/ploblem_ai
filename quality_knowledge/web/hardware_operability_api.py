"""Hardware system endpoints for release binding and operability."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.hardware_data_reliability import HardwareDataReliabilityManager
from services.hardware_operability import (
    contract_descriptor,
    health_status,
    readiness_status,
)


def create_hardware_operability_router(
    *,
    project_root: str | Path,
    hardware_db_path: str | Path,
    startup_status: Mapping[str, Any] | None = None,
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
            startup_status=startup_status,
        )
        return JSONResponse(payload, status_code=status)

    if startup_status is not None:
        @router.get("/api/system/hardware/startup")
        def startup() -> JSONResponse:
            status = dict(startup_status)
            return JSONResponse(status, status_code=200 if status.get("ready") else 503)

    @router.get("/api/public/hardware/v1/contract")
    def contract() -> dict[str, Any]:
        return contract_descriptor(root)

    @router.get("/api/system/hardware/schema")
    def schema_status() -> JSONResponse:
        if startup_status is not None and not startup_status.get("ready"):
            payload = {
                "status": "UNREADY",
                "error_code": startup_status.get("error_code") or "HARDWARE_STARTUP_NOT_READY",
            }
            return JSONResponse(payload, status_code=503)
        payload = HardwareDataReliabilityManager(hardware_db).inspect_status()
        status_code = 200 if payload.get("status") == "READY" else 503
        return JSONResponse(payload, status_code=status_code)

    return router


__all__ = ["create_hardware_operability_router"]
