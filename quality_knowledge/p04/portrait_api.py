"""HTTP projections for customer portrait and portrait archive contracts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .portrait import ArchiveListProjection, PortraitService


def create_portrait_router(service: PortraitService) -> APIRouter:
    router = APIRouter(prefix="/api/v2/quality-scenario-insights/v1", tags=["P04 Portrait"])

    @router.post("/customer-quality-portrait/v1/query")
    def portrait_query(payload: dict[str, Any]) -> dict[str, Any]:
        return service.query(payload or {}).model_dump(mode="json")

    @router.post("/customer-quality-portrait-archive/v1/jobs", status_code=201)
    def create_portrait_job(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            job_id = service.create_job(
                payload.get("filters") if isinstance(payload.get("filters"), dict) else payload,
                model=str(payload.get("model") or "P04_PORTRAIT_AGGREGATOR"),
            )
        except ValueError as error:
            raise HTTPException(status_code=503 if str(error) == "DATA_UNAVAILABLE" else 400, detail=str(error)) from error
        return {"contract_version": "customer-quality-portrait-archive/v1", "job_id": job_id}

    @router.post("/customer-quality-portrait-archive/v1/jobs/{job_id}/archive")
    def archive_portrait_job(job_id: str) -> dict[str, Any]:
        try:
            return service.archive_job(job_id)
        except ValueError as error:
            status = 409 if str(error) == "ALREADY_ARCHIVED" else 404
            raise HTTPException(status_code=status, detail=str(error)) from error

    @router.get("/customer-quality-portrait-archive/v1")
    def archive_list() -> dict[str, Any]:
        items = service.repository.list_archives()
        return ArchiveListProjection(items=items, total=len(items)).model_dump(mode="json")

    @router.get("/customer-quality-portrait-archive/v1/{archive_id}")
    def archive_detail(archive_id: str) -> dict[str, Any]:
        try:
            return service.repository.get_archive(archive_id).model_dump(mode="json")
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
