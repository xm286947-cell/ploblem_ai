"""API binding for Hardware R1 Knowledge Production Workbench V1.

The router is intentionally thin and delegates Batch orchestration to
HardwareR1WorkbenchService. It does not implement Stage A/B itself and does not
publish Formal Knowledge.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from services.hardware_case_r1_workbench import (
    HardwareR1WorkbenchError,
    HardwareR1WorkbenchService,
)
from services.hardware_r1_knowledge_promotion import (
    HardwareR1KnowledgePromotionService,
    HardwareR1PromotionError,
)


class ReviewConflictDecisionRequest(BaseModel):
    decision_source: str = Field(min_length=1)
    reviewer: str = Field(default="MAINTAINER", min_length=1)


class PromotionReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1)
    confirmed_content: dict[str, Any]
    review_comment: str | None = None


class PromotionPublishRequest(BaseModel):
    publisher: str = Field(min_length=1)


def _require_maintainer(value: str | None) -> None:
    role = str(value or "CONSUMER").strip().upper()
    if role != "MAINTAINER":
        raise HTTPException(
            status_code=403,
            detail="HARDWARE_CASE_MAINTAINER_REQUIRED",
        )


def _workbench_error(error: HardwareR1WorkbenchError) -> HTTPException:
    if error.code in {
        "BATCH_NOT_FOUND",
        "BATCH_ITEM_NOT_FOUND",
        "REVIEW_CONFLICT_NOT_FOUND",
        "CANDIDATE_NOT_FOUND",
    }:
        return HTTPException(status_code=404, detail=error.code)
    if error.code in {
        "RETRY_NOT_ALLOWED",
        "RETRY_FAILED_STAGE_NOT_AVAILABLE",
        "REVIEW_NOT_REQUIRED",
        "REVIEW_CONFLICT_ALREADY_RESOLVED",
        "CANDIDATE_CONCURRENT_UPDATE",
        "CANDIDATE_ASSET_INVALIDATED",
        "CANDIDATE_LOCKED_BY_PROMOTION",
        "CANDIDATE_REVIEW_TRANSITION_INVALID",
    }:
        return HTTPException(status_code=409, detail=error.code)
    return HTTPException(status_code=400, detail=error.code)


def _promotion_error(error: HardwareR1PromotionError) -> HTTPException:
    if error.code in {
        "BATCH_NOT_FOUND", "BATCH_ITEM_NOT_FOUND", "PROMOTION_NOT_FOUND",
        "CANDIDATE_NOT_FOUND",
    }:
        return HTTPException(status_code=404, detail=error.code)
    if error.code in {
        "GOLDEN_CANDIDATE_NOT_READY",
        "HUMAN_REVIEW_REQUIRED",
        "CANDIDATE_INTAKE_REQUIRED",
        "PUBLISH_REQUIRED_BEFORE_QUERY_BACK",
        "PROMOTION_RETRY_NOT_ALLOWED",
        "PROMOTION_IDEMPOTENCY_CONFLICT",
        "PROMOTION_STATE_INVALID",
        "CANDIDATE_ASSET_INVALIDATED",
        "CANDIDATE_LOCKED_BY_REVIEW",
        "CANDIDATE_LOCKED_BY_PROMOTION",
        "CANDIDATE_CONCURRENT_UPDATE",
        "CANDIDATE_PROMOTION_TRANSITION_INVALID",
        "CANDIDATE_DATA_INTEGRITY_ERROR",
    }:
        return HTTPException(status_code=409, detail=error.code)
    return HTTPException(status_code=422, detail=error.code)


def create_hardware_r1_workbench_router(
    service: HardwareR1WorkbenchService,
    *,
    promotion_service: HardwareR1KnowledgePromotionService | None = None,
    prefix: str = "/api/v2/hardware-cases/r1/workbench",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["hardware-r1-workbench"])

    def require_promotion_service() -> HardwareR1KnowledgePromotionService:
        if promotion_service is None:
            raise HTTPException(status_code=503, detail="KNOWLEDGE_PROMOTION_UNAVAILABLE")
        return promotion_service

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

    @router.post(
        "/items/{item_id}/review-conflicts/{conflict_id}/resolve"
    )
    def resolve_review_conflict(
        item_id: str,
        conflict_id: str,
        request: ReviewConflictDecisionRequest,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.resolve_review_conflict(
                item_id,
                conflict_id=conflict_id,
                decision_source=request.decision_source,
                reviewer=request.reviewer,
            )
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

    @router.post("/items/{item_id}/promotion/precheck")
    def promotion_precheck(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.precheck_item(item_id)
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.post("/items/{item_id}/promotion/intake")
    def promotion_intake(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.intake_item(item_id)
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.post("/items/{item_id}/promotion/review")
    def promotion_review(
        item_id: str,
        request: PromotionReviewRequest,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.review_item(
                item_id,
                reviewer=request.reviewer,
                confirmed_content=request.confirmed_content,
                review_time=datetime.now(timezone.utc),
                review_comment=request.review_comment,
            )
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.post("/items/{item_id}/promotion/publish")
    def promotion_publish(
        item_id: str,
        request: PromotionPublishRequest,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.publish_item(
                item_id,
                publisher=request.publisher,
                published_at=datetime.now(timezone.utc),
            )
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.post("/items/{item_id}/promotion/verify")
    def promotion_verify(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.verify_item(item_id)
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.post("/items/{item_id}/promotion/retry-failed")
    def promotion_retry_failed(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.retry_failed_item(item_id)
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.get("/items/{item_id}/promotion")
    def promotion_get_item(
        item_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.get_item(item_id)
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.post("/batches/{batch_id}/promotion/intake")
    def promotion_intake_batch(
        batch_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.intake_batch(batch_id)
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.post("/batches/{batch_id}/promotion/retry-failed")
    def promotion_retry_failed_batch(
        batch_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        try:
            return promotion.retry_failed_batch(batch_id)
        except HardwareR1PromotionError as error:
            raise _promotion_error(error) from error

    @router.get("/batches/{batch_id}/promotion")
    def promotion_get_batch(
        batch_id: str,
        x_hardware_case_role: str | None = Header(
            default=None,
            alias="X-Hardware-Case-Role",
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        promotion = require_promotion_service()
        return promotion.get_batch(batch_id)


    return router


__all__ = ["create_hardware_r1_workbench_router"]
