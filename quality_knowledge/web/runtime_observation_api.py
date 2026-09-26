"""HTTP boundary for the canonical Storage Runtime Observation contract."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from runtime.contracts import RuntimeObservation
from runtime.observation import RuntimeObservationError, RuntimeObservationService


def _error(error: RuntimeObservationError) -> HTTPException:
    return HTTPException(
        status_code=409 if error.code == "RUNTIME_OBSERVATION_ALREADY_EXISTS" else 400,
        detail={"code": error.code, "details": error.details},
    )


def _payload(observation: RuntimeObservation) -> dict[str, Any]:
    result = observation.model_dump(mode="json")
    result["formal_consumption"] = RuntimeObservationService.formal_consumption(observation)
    return result


def create_runtime_observation_router(service: RuntimeObservationService) -> APIRouter:
    router = APIRouter()

    @router.post("/storage/runtime-observations", status_code=201)
    def create_runtime_observation(payload: RuntimeObservation) -> dict[str, Any]:
        try:
            return _payload(service.create(payload))
        except RuntimeObservationError as error:
            raise _error(error) from error

    @router.get("/storage/devices/{device_id}/runtime-observations")
    def list_runtime_observations(
        device_id: str,
        metric_name: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        items = service.list(device_id=device_id, metric_name=metric_name, limit=limit)
        return {"items": [_payload(item) for item in items], "total": len(items)}

    @router.get("/storage/runtime-observations/{observation_id}")
    def get_runtime_observation(observation_id: str) -> dict[str, Any]:
        observation = service.get(observation_id)
        if observation is None:
            raise HTTPException(404, "RUNTIME_OBSERVATION_NOT_FOUND")
        return _payload(observation)

    return router
