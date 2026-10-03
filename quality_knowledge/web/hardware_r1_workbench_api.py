"""API binding for Hardware R1 Knowledge Production Workbench V1.

The router is intentionally thin and delegates Batch orchestration to
HardwareR1WorkbenchService. It does not implement Stage A/B itself and does not
publish Formal Knowledge.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile

from services.hardware_case_r1_workbench import (
    HardwareR1WorkbenchError,
    HardwareR1WorkbenchService,
)


def _require_maintainer(value: str | None) -> None:
    role = str(value or "CONSUMER").strip().upper()
    if role != "MAINTAINER":
        raise HTTPException(
            status_code=403,
            detail="HARDWARE_CASE_MAINTAINER_REQUIRED",
        )


def _workbench_error(error: HardwareR1WorkbenchError) -> HTTPException:
    if error.code in {"BATCH_NOT_FOUND", "BATCH_ITEM_NOT_FOUND"}:
        return HTTPException(status_code=404, detail=error.code)
    if error.code in {
        "RETRY_NOT_ALLOWED",
        "RETRY_FAILED_STAGE_NOT_AVAILABLE",
    }:
        return HTTPException(status_code=409, detail=error.code)
    return HTTPException(status_code=400, detail=error.code)


def create_hardware_r1_workbench_router(
    service: HardwareR1WorkbenchService,
    *,
    prefix: str = "/api/v2/hardware-cases/r1/workbench",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["hardware-r1-workbench"])

    @router.post("/batches", status_code=201)
    async def upload_batch(
        files: list[UploadFile] = File(...),
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if not files:
            raise HTTPException(status_code=400, detail="BATCH_FILES_REQUIRED")
        payloads: list[tuple[str, bytes, str | None]] = []
        for file in files:
            payloads.append(
                (
                    str(file.filename or ""),
                    await file.read(),
                    file.content_type,
                )
            )
        return service.upload_batch(payloads)

    @router.get("/batches")
    def list_batches(
        limit: int = Query(default=50, ge=1, le=200),
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        return service.list_batches(limit=limit)

    @router.get("/batches/{batch_id}")
    def get_batch(
        batch_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.get_batch(batch_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    @router.post("/batches/{batch_id}/run-resume")
    def run_batch(
        batch_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.run_batch(batch_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    @router.post("/batches/{batch_id}/retry-failed-only")
    def retry_failed_only(
        batch_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.retry_failed_only(batch_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    @router.get("/items/{item_id}")
    def get_item(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.get_item(item_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    @router.post("/items/{item_id}/run-resume")
    def run_resume_item(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.run_resume_item(item_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    @router.post("/items/{item_id}/retry-failed-stage")
    def retry_failed_stage_item(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.retry_failed_stage_item(item_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    @router.post("/items/{item_id}/force-full-run")
    def force_full_run_item(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.force_full_run_item(item_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    @router.get("/items/{item_id}/advanced-debug")
    def advanced_debug(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.advanced_debug(item_id)
        except HardwareR1WorkbenchError as error:
            raise _workbench_error(error) from error

    return router


__all__ = ["create_hardware_r1_workbench_router"]
