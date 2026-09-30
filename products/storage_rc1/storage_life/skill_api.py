from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from skills.real_knowledge import RealKnowledgeAssessmentService


def create_storage_skill_router(service: RealKnowledgeAssessmentService | None = None) -> APIRouter:
    service = service or RealKnowledgeAssessmentService.current()
    router = APIRouter(tags=["Storage Domain Skills"])

    @router.get("/api/product/skills/readiness")
    def skill_readiness():
        return service.readiness()

    @router.get("/api/product/skills/real-golden")
    def real_golden():
        return service.real_golden()

    @router.post("/api/product/skills/{skill_id}/execute")
    def execute_skill(skill_id: str, payload: dict[str, Any]):
        try:
            return service.execute_skill(skill_id, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
