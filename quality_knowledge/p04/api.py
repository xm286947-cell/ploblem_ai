"""FastAPI routes for the P04 consumer contract."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .contracts import P04State, P04View
from .service import P04InsightService


def _json(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def create_p04_router(service: P04InsightService) -> APIRouter:
    router = APIRouter(prefix="/api/v2/quality-scenario-insights/v1", tags=["P04 QualityScenario Insight"])

    @router.get("/selectors")
    def selectors(view: str = "PRODUCT") -> Any:
        try:
            result = service.selectors(P04View(view.upper()))
        except ValueError as error:
            raise HTTPException(status_code=400, detail="INVALID_VIEW") from error
        if result["state"] == P04State.PERMISSION_UNAVAILABLE:
            return JSONResponse(status_code=403, content=_json(result))
        if result["state"] in {P04State.DATA_UNAVAILABLE, P04State.ERROR}:
            return JSONResponse(status_code=503, content=_json(result))
        return _json(result)

    @router.post("/query")
    def query(payload: dict[str, Any]) -> Any:
        try:
            result = service.query(payload or {})
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if result.state == P04State.PERMISSION_UNAVAILABLE:
            return JSONResponse(status_code=403, content=_json(result))
        if result.state == P04State.DATA_UNAVAILABLE:
            return JSONResponse(status_code=503, content=_json(result))
        if result.state == P04State.ERROR:
            return JSONResponse(status_code=400, content=_json(result))
        return _json(result)

    @router.post("/drilldown")
    def drilldown(payload: dict[str, Any]) -> Any:
        try:
            result = service.drilldown(payload or {})
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if result.status == "REVISION_CHANGED":
            return JSONResponse(status_code=409, content=_json(result))
        if result.state == P04State.PERMISSION_UNAVAILABLE:
            return JSONResponse(status_code=403, content=_json(result))
        if result.state == P04State.DATA_UNAVAILABLE:
            return JSONResponse(status_code=503, content=_json(result))
        return _json(result)

    return router
