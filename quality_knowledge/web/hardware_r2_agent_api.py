"""Read-only Hardware R2 smart query API, separate from public consumption/v1."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.hardware_r2_agent_runtime import HardwareR2Agent, validate_advice


class EngineeringAnalysisRequest(BaseModel):
    knowledge_id: str = Field(min_length=1, max_length=128)
    task: Literal["DESIGN_REUSE", "COMPONENT_CIRCUIT_RISK",
                  "FIELD_PROBLEM", "TEST_VALIDATION"]


def create_hardware_r2_agent_router(
    case_search_service: Any, consumption_service: Any,
    consumption_agent: HardwareR2Agent,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/hardware-r2", tags=["hardware-r2-agent"])

    @router.get("/search")
    def smart_search(
        text: str = "",
        interface: str | None = None,
        signal: str | None = None,
        device: str | None = None,
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        # Existing public v1 remains deterministic and backward compatible.
        filters = {"interface": interface, "signal": signal, "device": device}
        if not text.strip():
            payload = consumption_service.search("", limit=limit, **filters)
            return {
                **payload,
                "agent": {"status": "SKIPPED_EMPTY_QUERY", "trace": None},
                "retrieval_mode": "SQLITE_FORMAL",
            }
        cases = case_search_service.search_cases(text, role="CONSUMER")
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for case in cases.get("results", []):
            case_id = str(case.get("business_case_id") or case.get("case_id") or "")
            if not case_id or case_id in seen:
                continue
            source = consumption_service.search(
                "", business_case_id=case_id, limit=2, **filters
            ).get("results", [])
            if not source:
                continue
            # Do not synthesize a Formal row from an unreviewed local case.
            row = dict(source[0])
            retrieval = case.get("retrieval") or {}
            row["match_score"] = retrieval.get("score") or 0
            explanation = retrieval.get("why_hit") or {}
            row["match_reasons"] = list(explanation.get("reasons") or [])
            row["retrieval_reason"] = explanation
            seen.add(case_id)
            rows.append(row)
            if len(rows) >= limit:
                break
        retrieval = cases.get("retrieval") or {}
        return {
            "contract_version": "hardware-r2-read-only/v1",
            "results": rows,
            "agent": retrieval.get("online_agent") or {
                "status": "SKIPPED_FAST_PATH", "trace": None
            },
            "retrieval_mode": retrieval.get("mode", "UNKNOWN"),
        }

    @router.post("/analyze")
    def analyze(request: EngineeringAnalysisRequest) -> dict[str, Any]:
        row = consumption_service.get(request.knowledge_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FORMAL_KNOWLEDGE_NOT_FOUND")
        result = consumption_agent.invoke({
            "task": request.task,
            "knowledge": dict(row),
        })
        if result.get("status") != "COMPLETED":
            return {
                "status": result.get("status", "BLOCKED"),
                "reason": result.get("reason"),
                "trace": result.get("trace"),
                "analysis": None,
            }
        try:
            analysis = validate_advice(result.get("data") or {}, row)
        except ValueError:
            # Invalid citations must not be rendered as engineering advice.
            return {
                "status": "EVIDENCE_REJECTED",
                "reason": "ADVICE_SOURCE_REF_INVALID",
                "trace": result.get("trace"),
                "analysis": None,
            }
        return {
            "status": "COMPLETED", "analysis": analysis,
            "knowledge_id": request.knowledge_id,
            "trace": result.get("trace"),
        }

    return router
