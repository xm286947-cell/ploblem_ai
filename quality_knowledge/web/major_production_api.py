"""Unified Web API for Major Source production; no test-only data paths."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from services.major_case_production import MajorCaseProductionService, MajorProductionError


def _error(error: MajorProductionError) -> HTTPException:
    status = 404 if error.code in {"MAJOR_CASE_NOT_FOUND", "MAJOR_ENTRY_NOT_FOUND"} else 409 if error.code in {"NO_PUBLISHABLE_CONFIRMED_FACT", "MAJOR_CONFIRMATION_REQUIRES_PENDING_AI_CANDIDATE"} else 503 if error.code == "MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED" else 400
    return HTTPException(status, error.code)


def create_major_production_router(service: MajorCaseProductionService) -> APIRouter:
    router = APIRouter(prefix="/api/v2/major-production", tags=["major-production"])

    @router.post("/sources", status_code=201)
    async def intake_source(
        title: str = Form(...),
        group_code: str = Form(...),
        standard_itr: str = Form(...),
        domain: str = Form(""),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        try:
            return service.intake_source(
                title=title,
                group_code=group_code,
                domain=domain,
                standard_itr=standard_itr,
                source_name=file.filename or "major-source.pdf",
                source_bytes=await file.read(),
            )
        except MajorProductionError as error:
            raise _error(error) from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get("/cases/{case_id}")
    def case_detail(case_id: str) -> dict[str, Any]:
        try:
            return service.detail(case_id)
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/cases/{case_id}/analysis")
    def analyze(case_id: str) -> dict[str, Any]:
        try:
            return service.analyze(case_id)
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/entries/{entry_id}/confirm")
    def confirm(entry_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return service.confirm_entry(
                entry_id,
                reviewer=str(payload.get("reviewer") or ""),
                content=str(payload.get("content") or ""),
                reason=str(payload.get("reason") or ""),
            )
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/events/{event_id}/publish")
    def publish(event_id: str) -> dict[str, Any]:
        try:
            return service.publish(event_id)
        except MajorProductionError as error:
            raise _error(error) from error

    return router
