"""Hardware request correlation and structured observability."""
from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


_CORRELATION = ContextVar("hardware_correlation_id", default="")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
logger = logging.getLogger("hardware.production")


def current_correlation_id() -> str:
    return _CORRELATION.get() or "hw-" + uuid4().hex


def _correlation(value: str | None) -> str:
    candidate = str(value or "").strip()
    if candidate and _SAFE_ID.fullmatch(candidate):
        return candidate
    return "hw-" + uuid4().hex


def emit_event(event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "correlation_id": current_correlation_id(),
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


class HardwareCorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = _correlation(request.headers.get("X-Correlation-ID"))
        token = _CORRELATION.set(correlation_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            response.headers["X-Correlation-ID"] = correlation_id
            if request.url.path.startswith(("/api/v2/hardware", "/api/public/hardware", "/health", "/ready")):
                emit_event(
                    "hardware_http_request",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
            return response
        except Exception as error:
            emit_event(
                "hardware_http_failure",
                method=request.method,
                path=request.url.path,
                error_type=type(error).__name__,
            )
            raise
        finally:
            _CORRELATION.reset(token)


__all__ = [
    "HardwareCorrelationMiddleware",
    "current_correlation_id",
    "emit_event",
]
