from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, getproxies, proxy_bypass, urlopen

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from pydantic import BaseModel, TypeAdapter, ValidationError

from runtime.reliability.errors import RuntimeStepError
from runtime.contracts import ErrorCategory
from runtime.providers.endpoint import ProviderEndpointError, ProviderEndpointResolver


_RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}
_DIAGNOSTIC_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
    "bearer_token",
    "credential",
    "credentials",
}
_REQUEST_ID_HEADERS = {
    "request-id",
    "x-request-id",
    "x-requestid",
    "trace-id",
    "x-trace-id",
    "x-dashscope-request-id",
}


def _env_enabled(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _provider_diagnostics_enabled() -> bool:
    return _env_enabled("RUNTIME_PROVIDER_DIAGNOSTICS")


def _provider_trace_enabled() -> bool:
    return _env_enabled("RUNTIME_PROVIDER_TRACE") or _provider_diagnostics_enabled()


def _redact_diagnostic_value(value: Any, *, known_secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _DIAGNOSTIC_SECRET_KEYS or normalized.endswith("_secret"):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_diagnostic_value(
                    item,
                    known_secrets=known_secrets,
                )
        return redacted
    if isinstance(value, list):
        return [
            _redact_diagnostic_value(item, known_secrets=known_secrets)
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            _redact_diagnostic_value(item, known_secrets=known_secrets)
            for item in value
        ]
    if isinstance(value, str):
        text = value
        for secret in known_secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text
    return value


def _safe_request_body(
    body: dict[str, Any],
    *,
    known_secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    safe = _redact_diagnostic_value(body, known_secrets=known_secrets)
    messages = safe.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            message["content"] = json.dumps(
                _redact_diagnostic_value(parsed, known_secrets=known_secrets),
                ensure_ascii=False,
                default=str,
            )
    return safe


def _safe_error_body(
    raw: bytes,
    *,
    known_secrets: tuple[str, ...] = (),
) -> Any:
    text = raw.decode("utf-8", errors="replace")
    for secret in known_secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:32768]
    return _redact_diagnostic_value(parsed, known_secrets=known_secrets)


def _safe_response_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    result: dict[str, str] = {}
    try:
        items = headers.items()
    except AttributeError:
        return result
    for name, value in items:
        normalized = str(name).lower()
        if normalized == "content-type" or normalized in _REQUEST_ID_HEADERS:
            result[str(name)] = str(value)
    return result


def _provider_request_id(headers: Any) -> str | None:
    safe_headers = _safe_response_headers(headers)
    for name, value in safe_headers.items():
        if str(name).lower() in _REQUEST_ID_HEADERS:
            return value
    return None


def _safe_proxy(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    if not parsed.scheme or not parsed.hostname:
        return "<set>"
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def _write_provider_trace(event: dict[str, Any]) -> None:
    line = "[runtime-provider] " + json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
    )
    print(line, flush=True)
    path = os.environ.get("RUNTIME_PROVIDER_TRACE_FILE", "").strip()
    if not path:
        return
    try:
        trace_path = Path(path)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(line + "\n")
            handle.flush()
    except Exception:
        pass


def _trace_provider_request(
    *,
    endpoint: str,
    model: str,
    api_key: str | None,
    timeout_seconds: int,
    runtime_context: dict[str, Any],
    body: dict[str, Any],
    body_bytes: bytes,
) -> None:
    if not _provider_trace_enabled():
        return
    parsed = urlsplit(endpoint)
    proxies = getproxies()
    diagnostic = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "phase": "request",
        "provider": "openai_compatible",
        "provider_call_seq": runtime_context.get("provider_call_seq"),
        "method": "POST",
        "model": model,
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port
        or (443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None),
        "path": parsed.path,
        "endpoint": endpoint,
        "auth": "bearer_present" if api_key else "none",
        "content_type": "application/json",
        "body_bytes": len(body_bytes),
        "body_sha256": hashlib.sha256(body_bytes).hexdigest()[:16],
        "timeout_seconds": timeout_seconds,
        "sdk_retry": 0,
        "proxy_bypass": proxy_bypass(parsed.hostname) if parsed.hostname else False,
        "proxy_http": _safe_proxy(proxies.get("http", "")) if proxies.get("http") else None,
        "proxy_https": _safe_proxy(proxies.get("https", "")) if proxies.get("https") else None,
    }
    if _provider_diagnostics_enabled():
        diagnostic["headers"] = {
            "Content-Type": "application/json",
            **({"Authorization": "Bearer [REDACTED]"} if api_key else {}),
        }
        diagnostic["body"] = _safe_request_body(
            body,
            known_secrets=(api_key,) if api_key else (),
        )
    _write_provider_trace(diagnostic)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


class OpenAICompatibleProviderAdapter:
    """Runtime-owned single-request OpenAI-compatible provider adapter.

    One invocation performs exactly one HTTP provider request. Retry, provider
    call budget, checkpoint and resume remain owned by the Runtime execution
    engine. The adapter never retries internally and never returns credentials
    or raw provider envelopes to business code.
    """

    def __init__(
        self,
        *,
        system_prompt: str,
        output_schema: Any,
        timeout_seconds: int | None = None,
        response_shape: str | None = None,
    ) -> None:
        self.system_prompt = str(system_prompt)
        self.output_schema = output_schema
        self.timeout_seconds = int(timeout_seconds or 120)
        self.response_shape = str(response_shape or "").strip().lower() or None

    def _validate_shape(self, parsed: Any) -> None:
        if self.response_shape in {"json_array", "array", "list"} and not isinstance(parsed, list):
            raise RuntimeStepError(
                "provider JSON must be an array",
                code="PROVIDER_OUTPUT_SHAPE_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"expected_shape": "json_array"},
            )
        if self.response_shape in {"json_object", "object", "dict"} and not isinstance(parsed, dict):
            raise RuntimeStepError(
                "provider JSON must be an object",
                code="PROVIDER_OUTPUT_SHAPE_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"expected_shape": "json_object"},
            )

    def _validate_output(self, parsed: Any) -> Any:
        self._validate_shape(parsed)
        schema = self.output_schema
        try:
            if isinstance(schema, TypeAdapter):
                return _jsonable(schema.validate_python(parsed))

            if isinstance(schema, type) and issubclass(schema, BaseModel):
                if isinstance(parsed, list):
                    return [
                        schema.model_validate(item).model_dump(mode="json")
                        for item in parsed
                    ]
                return schema.model_validate(parsed).model_dump(mode="json")

            if isinstance(schema, dict):
                validate_json_schema(parsed, schema)
                return parsed

            validate_python = getattr(schema, "validate_python", None)
            if callable(validate_python):
                return _jsonable(validate_python(parsed))

            if callable(schema):
                return _jsonable(schema(parsed))

            return parsed
        except RuntimeStepError:
            raise
        except (ValidationError, JsonSchemaValidationError, TypeError, ValueError) as exc:
            details: dict[str, Any] = {"schema_type": type(schema).__name__}
            if isinstance(exc, ValidationError):
                details["errors"] = exc.errors(include_input=False)
            elif isinstance(exc, JsonSchemaValidationError):
                details["json_schema_path"] = [str(item) for item in exc.absolute_path]
            raise RuntimeStepError(
                "provider JSON failed configured output schema",
                code="PROVIDER_SCHEMA_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details=details,
            ) from exc

    def __call__(self, payload: Any, context: dict[str, Any]) -> Any:
        runtime_context = context.get("runtime") or {}
        provider = runtime_context.get("provider_config") or {}

        base_url = str(provider.get("base_url") or "").strip()
        model = str(provider.get("model") or "").strip()
        api_key = provider.get("api_key")
        if not base_url or not model:
            raise RuntimeStepError(
                "resolved provider configuration is incomplete",
                code="PROVIDER_CONFIG_INCOMPLETE",
                category=ErrorCategory.CONFIG,
                retryable=False,
                details={
                    "has_base_url": bool(base_url),
                    "has_model": bool(model),
                },
            )

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
        }
        if provider.get("temperature") is not None:
            body["temperature"] = provider["temperature"]
        if provider.get("max_tokens") is not None:
            body["max_tokens"] = provider["max_tokens"]

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            endpoint = ProviderEndpointResolver.chat_completions_url(base_url)
        except ProviderEndpointError as exc:
            raise RuntimeStepError(
                "provider base_url failed Runtime endpoint contract",
                code="PROVIDER_BASE_URL_INVALID",
                category=ErrorCategory.CONFIG,
                retryable=False,
                details=exc.details,
            ) from exc
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        _trace_provider_request(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=self.timeout_seconds,
            runtime_context=runtime_context,
            body=body,
            body_bytes=body_bytes,
        )
        request = Request(
            endpoint,
            data=body_bytes,
            method="POST",
            headers=headers,
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if _provider_trace_enabled():
                    _write_provider_trace(
                        {
                            "ts": datetime.now().isoformat(timespec="milliseconds"),
                            "phase": "response",
                            "provider_call_seq": runtime_context.get("provider_call_seq"),
                            "status": getattr(response, "status", None),
                            "endpoint": endpoint,
                            "content_type": (
                                response.headers.get("Content-Type")
                                if getattr(response, "headers", None)
                                else None
                            ),
                        }
                    )
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = b""
            try:
                error_body = exc.read()
            except Exception:
                error_body = b""
            request_id = _provider_request_id(getattr(exc, "headers", None))
            if _provider_trace_enabled():
                diagnostic = {
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "phase": "http_error",
                    "provider_call_seq": runtime_context.get("provider_call_seq"),
                    "status": int(exc.code),
                    "endpoint": endpoint,
                    "response_headers": _safe_response_headers(
                        getattr(exc, "headers", None)
                    ),
                    "provider_request_id": request_id,
                }
                if _provider_diagnostics_enabled():
                    diagnostic["response_body"] = _safe_error_body(
                        error_body,
                        known_secrets=(api_key,) if api_key else (),
                    )
                _write_provider_trace(diagnostic)
            raise RuntimeStepError(
                f"provider HTTP {exc.code}",
                code="PROVIDER_HTTP_ERROR",
                category=ErrorCategory.TRANSPORT,
                retryable=exc.code in _RETRYABLE_HTTP,
                details={
                    "http_status": int(exc.code),
                    **(
                        {"provider_request_id": request_id}
                        if request_id
                        else {}
                    ),
                },
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if _provider_trace_enabled():
                _write_provider_trace(
                    {
                        "ts": datetime.now().isoformat(timespec="milliseconds"),
                        "phase": "transport_error",
                        "provider_call_seq": runtime_context.get("provider_call_seq"),
                        "error_type": type(exc).__name__,
                        "error": str(exc).replace("\n", " ")[:500],
                        "endpoint": endpoint,
                    }
                )
            raise RuntimeStepError(
                f"provider transport failure: {type(exc).__name__}",
                code="PROVIDER_TRANSPORT",
                category=ErrorCategory.TRANSPORT,
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc

        try:
            envelope = json.loads(raw_body)
            choice = envelope["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeStepError(
                "provider returned an invalid OpenAI-compatible envelope",
                code="PROVIDER_ENVELOPE_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            ) from exc

        if str(finish_reason or "").lower() == "length":
            raise RuntimeStepError(
                "provider output was truncated",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"finish_reason": "length"},
            )

        if not isinstance(content, str) or not content.strip():
            raise RuntimeStepError(
                "provider returned empty content",
                code="EMPTY_PROVIDER_CONTENT",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeStepError(
                "provider content is not strict JSON",
                code="INVALID_JSON",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            ) from exc

        return self._validate_output(parsed)


__all__ = ["OpenAICompatibleProviderAdapter"]
