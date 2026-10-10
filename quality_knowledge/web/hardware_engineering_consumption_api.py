"""Feature-gated internal engineering analysis; no public Provider endpoint."""
from __future__ import annotations
import hmac
from typing import Any
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from services.hardware_engineering_consumption import EngineeringAnalysisError, HardwareEngineeringConsumption


class EngineeringRequest(BaseModel):
    knowledge_id: str = Field(min_length=1, max_length=128)
    task_intent: str = Field(min_length=1, max_length=40)


def create_hardware_engineering_consumption_router(service: HardwareEngineeringConsumption, *, token: str | None = None) -> APIRouter:
    router = APIRouter(prefix='/api/hardware-query/v1', tags=['hardware-engineering-agent-v1'])

    @router.post('/analyze')
    def analyze(body: EngineeringRequest, x_hardware_analysis_token: str | None = Header(None, alias='X-Hardware-Analysis-Token')) -> dict[str, Any]:
        if not token:
            raise HTTPException(status_code=503, detail='TRUSTED_AGENT_GATE_NOT_CONFIGURED')
        if not x_hardware_analysis_token or not hmac.compare_digest(x_hardware_analysis_token, token):
            raise HTTPException(status_code=403, detail='TRUSTED_AGENT_AUTH_REQUIRED')
        try:
            return service.analyze(body.knowledge_id, body.task_intent)
        except EngineeringAnalysisError as error:
            status = 503 if error.code in {'CONSUMPTION_AGENT_DISABLED', 'MODEL_LOCAL_CONFIG_REQUIRED', 'ANALYSIS_AGENT_CONFIG_MISSING'} else 422
            raise HTTPException(status_code=status, detail=error.code) from error

    return router
