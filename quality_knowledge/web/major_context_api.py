"""Unified-host composition for the major-problem public JSON contract."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException


def create_major_context_router(provider: Any) -> APIRouter:
    router = APIRouter(tags=["major-problem-context"])

    @router.get("/api/v2/major-problems/{problem_id}/context")
    def get_context(problem_id: str) -> dict[str, Any]:
        try:
            payload = provider.get_context(problem_id)
        except PermissionError as exc:
            raise HTTPException(403, "major context permission unavailable") from exc
        except Exception as exc:
            raise HTTPException(503, "major context provider unavailable") from exc
        if payload is None:
            raise HTTPException(404, "major problem not found")
        return payload

    return router
