from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from pydantic import BaseModel, TypeAdapter, ValidationError

from runtime.reliability.errors import RuntimeStepError
from runtime.contracts import ErrorCategory
from runtime.providers.endpoint import ProviderEndpointError, ProviderEndpointResolver


_RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}


def _provider_trace_enabled() -> bool:
    value = os.environ.get("RUNTIME_PROVIDER_TRACE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _trace_provider_request(
    *,
    endpoint: str,
    model: str,
    api_key_present: bool,
    timeout_seconds: int,
    runtime_context: dict[str, Any],
) -> None:
    if not _provider_trace_enabled():
        return
    parsed = urlsplit(endpoint)
    safe_endpoint = endpoint
    diagnostic = {
        "provider": "openai_compatible",
        "provider_call_seq": runtime_context.get("provider_call_seq"),
        "model": model,
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "path": parsed.path,
        "endpoint": safe_endpoint,
        "auth": "bearer_present" if api_key_present else "none",
        "timeout_seconds": timeout_seconds,
        "sdk_retry": 0,
    }
    print(
        "[runtime-provider] " + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True),
        flush=True,
    )


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
        _trace_provider_request(
            endpoint=endpoint,
            model=model,
            api_key_present=bool(api_key),
            timeout_seconds=self.timeout_seconds,
            runtime_context=runtime_context,
        )
        request = Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=headers,
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeStepError(
                f"provider HTTP {exc.code}",
                code="PROVIDER_HTTP_ERROR",
                category=ErrorCategory.TRANSPORT,
                retryable=exc.code in _RETRYABLE_HTTP,
                details={"http_status": int(exc.code)},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
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
