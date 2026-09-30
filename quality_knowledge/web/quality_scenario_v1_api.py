"""QualityScenario V1 production/workflow API for Overall R2.

Mounted alongside the current /api/v2 router.  This module owns only the
QualityScenario V1 namespace and does not replace Repeat/Major/P04 APIs.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from quality_knowledge.quality_scenario_candidate_v1_service import CandidateV1Service
from quality_knowledge.quality_scenario_traceability_service import QualityScenarioTraceabilityService
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.quality_scenario_v1_workflow_service import QualityScenarioV1WorkflowService


def _http_error(error: Exception) -> HTTPException:
    code = str(error)
    if code in {
        "QUALITY_SCENARIO_V1_NOT_FOUND",
        "QUALITY_SCENARIO_V1_CANDIDATE_NOT_FOUND",
    }:
        return HTTPException(404, code)
    if code in {
        "SCENARIO_VERSION_CONFLICT",
        "SCENARIO_CONFIRM_STATE_CONFLICT",
        "SCENARIO_REJECT_STATE_CONFLICT",
    }:
        return HTTPException(409, code)
    return HTTPException(400, code)


def _expected_version(payload: dict[str, Any]) -> int:
    try:
        value = int(payload.get("expected_scenario_version") or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise HTTPException(400, "EXPECTED_SCENARIO_VERSION_REQUIRED")
    return value


def create_quality_scenario_v1_router(db_path: str) -> APIRouter:
    router = APIRouter(prefix="/api/v2/quality-scenario-workflow/v1", tags=["QualityScenario V1 Workflow"])
    repository = SQLiteQualityScenarioV1Repository(db_path)
    candidates = CandidateV1Service(repository)
    workflow = QualityScenarioV1WorkflowService(repository)
    traceability = QualityScenarioTraceabilityService(repository)

    @router.post("/quality-scenarios/candidates/from-reverse")
    def create_candidate(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("reverse_quality_result")
        taxonomy = payload.get("taxonomy")
        if not isinstance(result, dict):
            raise HTTPException(400, "REVERSE_QUALITY_RESULT_REQUIRED")
        if not isinstance(taxonomy, dict):
            raise HTTPException(400, "SCENARIO_TAXONOMY_REQUIRED")
        try:
            produced = candidates.create_from_reverse(
                result,
                taxonomy,
                trigger_source=payload.get("trigger_source"),
                trigger_reason=str(payload.get("trigger_reason") or ""),
                created_by=str(payload.get("created_by") or ""),
            )
            return produced.to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios/candidates")
    def list_candidates(
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        items = candidates.list_candidates(
            product_code=product_code,
            lifecycle_stage_code=lifecycle_stage_code,
            business_activity_code=business_activity_code,
            quality_concern_code=quality_concern_code,
            q=q,
        )
        return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}

    @router.get("/quality-scenarios/candidates/{scenario_id}")
    def get_candidate(scenario_id: str) -> dict[str, Any]:
        item = candidates.get_candidate(scenario_id)
        if item is None:
            raise HTTPException(404, "QUALITY_SCENARIO_V1_CANDIDATE_NOT_FOUND")
        return item.model_dump(mode="json")

    @router.get("/quality-scenario-sources/scenarios")
    def scenarios_for_source(source_ref: str = "") -> dict[str, Any]:
        try:
            return traceability.scenarios_for_source(source_ref)
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios")
    def list_scenarios(
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        status: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        try:
            items = workflow.list_scenarios(
                product_code=product_code,
                lifecycle_stage_code=lifecycle_stage_code,
                business_activity_code=business_activity_code,
                quality_concern_code=quality_concern_code,
                status=status or None,
                q=q,
            )
        except ValueError as error:
            raise _http_error(error) from error
        return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}

    @router.get("/quality-scenarios/{scenario_id}")
    def get_scenario(scenario_id: str) -> dict[str, Any]:
        try:
            return workflow.get(scenario_id).model_dump(mode="json")
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios/{scenario_id}/traceability")
    def scenario_traceability(scenario_id: str) -> dict[str, Any]:
        try:
            return traceability.trace(scenario_id)
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios/{scenario_id}/history")
    def scenario_history(scenario_id: str) -> dict[str, Any]:
        try:
            return workflow.history(scenario_id)
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/review")
    def review_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return workflow.review_candidate(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                patch=payload.get("patch") if isinstance(payload.get("patch"), dict) else {},
                review_status=str(payload.get("review_status") or "PENDING"),
                reviewer=str(payload.get("reviewer") or ""),
                reviewed_at=str(payload.get("reviewed_at") or ""),
                comment=str(payload.get("comment") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/confirm")
    def confirm_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return workflow.confirm(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                quality_confirmed_by=str(payload.get("quality_confirmed_by") or ""),
                quality_confirmed_at=str(payload.get("quality_confirmed_at") or ""),
                technical_confirmed_by=str(payload.get("technical_confirmed_by") or ""),
                technical_confirmed_at=str(payload.get("technical_confirmed_at") or ""),
                confirmation_note=str(payload.get("confirmation_note") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/reject")
    def reject_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return workflow.reject(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                reviewer=str(payload.get("reviewer") or ""),
                reviewed_at=str(payload.get("reviewed_at") or ""),
                comment=str(payload.get("comment") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/publish")
    def publish_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return workflow.publish(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                published_by=str(payload.get("published_by") or ""),
                published_at=str(payload.get("published_at") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    return router
