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
        response_format = body.get("response_format")
        diagnostic["headers"] = {
            "Content-Type": "application/json",
            **({"Authorization": "Bearer [REDACTED]"} if api_key else {}),
        }
        diagnostic["request_contract"] = {
            "model": body.get("model"),
            "temperature": body.get("temperature", "NOT_SENT"),
            "max_tokens": body.get("max_tokens", "NOT_SENT"),
            "max_completion_tokens": body.get(
                "max_completion_tokens",
                "NOT_SENT",
            ),
            "response_format_type": (
                response_format.get("type")
                if isinstance(response_format, dict)
                else "NOT_SENT"
            ),
            "stream": bool(body.get("stream", False)),
            "message_count": (
                len(body.get("messages") or [])
                if isinstance(body.get("messages"), list)
                else 0
            ),
        }
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stage_semantic_handoff(
    runtime_context: dict[str, Any],
    content: str,
) -> None:
    handoff = runtime_context.get("semantic_handoff")
    if not isinstance(handoff, dict):
        return
    handoff.clear()
    handoff.update(
        {
            "content": content,
            "content_hash": _sha256_text(content),
            "content_length": len(content),
            "recoverable_content_available": bool(content),
        }
    )


def _safe_usage(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_usage(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_usage(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:128]
    return str(value)[:128]


def _schema_for_response_format(
    schema: Any,
    response_shape: str | None,
) -> dict[str, Any] | None:
    json_schema: dict[str, Any] | None = None
    if isinstance(schema, TypeAdapter):
        json_schema = schema.json_schema()
    elif isinstance(schema, type) and issubclass(schema, BaseModel):
        json_schema = schema.model_json_schema()
    elif isinstance(schema, dict):
        json_schema = schema
    else:
        getter = getattr(schema, "json_schema", None)
        if callable(getter):
            candidate = getter()
            if isinstance(candidate, dict):
                json_schema = candidate

    if json_schema is None:
        return None
    if response_shape in {"json_array", "array", "list"}:
        return {"type": "array", "items": json_schema}
    return json_schema


def _structured_output_contract(
    provider: dict[str, Any],
    output_schema: Any,
    response_shape: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    capabilities = provider.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}
    source = str(provider.get("capability_source") or "UNKNOWN")

    raw = capabilities.get("structured_output")
    supported: set[str] = set()
    explicitly_unsupported = False

    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"json_schema", "json_object"}:
            supported.add(normalized)
        elif normalized in {"unsupported", "none", "false"}:
            explicitly_unsupported = True
    elif isinstance(raw, dict):
        if raw.get("json_schema") is True:
            supported.add("json_schema")
        if raw.get("json_object") is True:
            supported.add("json_object")
        if raw.get("supported") is False:
            explicitly_unsupported = True
    elif raw is False:
        explicitly_unsupported = True

    response_format = capabilities.get("response_format")
    if isinstance(response_format, str):
        normalized = response_format.strip().lower()
        if normalized in {"json_schema", "json_object"}:
            supported.add(normalized)
    elif isinstance(response_format, list):
        for item in response_format:
            normalized = str(item).strip().lower()
            if normalized in {"json_schema", "json_object"}:
                supported.add(normalized)

    if capabilities.get("json_schema") is True:
        supported.add("json_schema")
    if capabilities.get("json_object") is True:
        supported.add("json_object")

    evidence = {
        "structured_output_capability_source": source,
        "structured_output_modes": sorted(supported),
        "structured_output_request": "NONE",
        "response_format_type": "NOT_SENT",
        "structured_output_fallback": "PROMPT_ONLY",
    }

    if "json_schema" in supported:
        schema = _schema_for_response_format(output_schema, response_shape)
        if schema is not None:
            evidence.update(
                {
                    "structured_output_capability": "SUPPORTED_AND_REQUESTED",
                    "structured_output_request": "response_format/json_schema",
                    "response_format_type": "json_schema",
                    "structured_output_fallback": "NONE",
                }
            )
            return (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "runtime_output",
                        "strict": True,
                        "schema": schema,
                    },
                },
                evidence,
            )
        evidence.update(
            {
                "structured_output_capability": "SUPPORTED_NOT_REQUESTED",
                "structured_output_reason": "JSON_SCHEMA_UNAVAILABLE",
            }
        )
        return None, evidence

    if "json_object" in supported:
        if response_shape in {"json_array", "array", "list"}:
            evidence.update(
                {
                    "structured_output_capability": "SUPPORTED_NOT_REQUESTED",
                    "structured_output_reason": "JSON_OBJECT_INCOMPATIBLE_WITH_ARRAY",
                }
            )
            return None, evidence
        evidence.update(
            {
                "structured_output_capability": "SUPPORTED_AND_REQUESTED",
                "structured_output_request": "response_format/json_object",
                "response_format_type": "json_object",
                "structured_output_fallback": "NONE",
            }
        )
        return {"type": "json_object"}, evidence

    if explicitly_unsupported:
        evidence["structured_output_capability"] = "UNSUPPORTED"
    elif raw is True:
        evidence.update(
            {
                "structured_output_capability": "SUPPORTED_NOT_REQUESTED",
                "structured_output_reason": "MODE_UNSPECIFIED",
            }
        )
    else:
        evidence["structured_output_capability"] = "UNKNOWN"
    return None, evidence


def _deterministic_wrapper_recovery(content: str) -> str | None:
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, int]] = []
    for index, char in enumerate(content):
        if char not in "[{":
            continue
        try:
            parsed, end = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, (dict, list)):
            continue
        candidates.append((index, end))

    maximal: list[tuple[int, int]] = []
    for candidate in candidates:
        start, end = candidate
        if any(
            other_start <= start
            and end <= other_end
            and (other_start, other_end) != candidate
            for other_start, other_end in candidates
        ):
            continue
        if candidate not in maximal:
            maximal.append(candidate)

    if len(maximal) != 1:
        return None

    start, end = maximal[0]
    prefix = content[:start]
    suffix = content[end:]
    # Never extract an inner value from a damaged outer JSON container.
    if any(char in prefix for char in "[{") or any(
        char in suffix for char in "]}"
    ):
        return None

    recovered = content[start:end]
    try:
        parsed = json.loads(recovered)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    return recovered


def reconstruct_transport_content(
    chunks: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    sequences: list[int] = []
    normalized: list[tuple[int, str]] = []
    for chunk in chunks:
        sequence = chunk.get("sequence")
        content = chunk.get("content")
        if not isinstance(sequence, int) or not isinstance(content, str):
            raise RuntimeStepError(
                "transport chunk metadata is invalid",
                code="TRANSPORT_RECONSTRUCTION_FAILED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )
        sequences.append(sequence)
        normalized.append((sequence, content))

    duplicates = len(set(sequences)) != len(sequences)
    ordered = sorted(normalized, key=lambda item: item[0])
    expected = list(range(len(ordered)))
    actual = [item[0] for item in ordered]
    missing = actual != expected
    if duplicates or missing:
        raise RuntimeStepError(
            "transport chunk sequence is incomplete",
            code="TRANSPORT_RECONSTRUCTION_FAILED",
            category=ErrorCategory.VALIDATION,
            retryable=True,
            details={
                "duplicate_chunk_detected": duplicates,
                "missing_chunk_detected": missing,
                "chunk_count": len(chunks),
            },
        )

    aggregate = "".join(item[1] for item in ordered)
    evidence = {
        "chunk_count": len(chunks),
        "chunk_sequence": sequences,
        "chunks": [
            {
                "sequence": sequence,
                "length": len(content),
                "hash": _sha256_text(content),
            }
            for sequence, content in normalized
        ],
        "chunk_order_valid": sequences == expected,
        "missing_chunk_detected": False,
        "duplicate_chunk_detected": False,
        "aggregate_length": len(aggregate),
        "aggregate_hash": _sha256_text(aggregate),
        "recovered": sequences != expected,
        "recovery_type": (
            "TRANSPORT_RECONSTRUCTION"
            if sequences != expected
            else "NONE"
        ),
    }
    return aggregate, evidence


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

    def validate_transport_chunks(
        self,
        chunks: list[dict[str, Any]],
    ) -> tuple[Any, dict[str, Any]]:
        arrival_content = "".join(
            str(chunk.get("content") or "")
            for chunk in chunks
        )
        recovered_content, evidence = reconstruct_transport_content(chunks)
        evidence.update(
            {
                "original_content_length": len(arrival_content),
                "original_content_hash": _sha256_text(arrival_content),
                "recovered_content_length": len(recovered_content),
                "recovered_content_hash": _sha256_text(recovered_content),
                "recovery_classification": (
                    "TRANSPORT_RECONSTRUCTION"
                    if evidence.get("recovered")
                    else "NONE"
                ),
            }
        )
        try:
            parsed = json.loads(recovered_content)
        except json.JSONDecodeError as exc:
            evidence["strict_parse_after_recovery"] = "FAIL"
            raise RuntimeStepError(
                "transport reconstruction did not produce strict JSON",
                code="TRANSPORT_RECONSTRUCTION_FAILED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            ) from exc
        evidence["strict_parse_after_recovery"] = "PASS"
        try:
            validated = self._validate_output(parsed)
        except RuntimeStepError:
            evidence["schema_validation_after_recovery"] = "FAIL"
            raise
        evidence["schema_validation_after_recovery"] = "PASS"
        return validated, evidence

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
        evidence = runtime_context.get("provider_evidence")
        if not isinstance(evidence, dict):
            evidence = {}

        base_url = str(provider.get("base_url") or "").strip()
        model = str(provider.get("model") or "").strip()
        api_key = provider.get("api_key")
        evidence.update(
            {
                "provider": "openai_compatible",
                "provider_call_seq": runtime_context.get("provider_call_seq"),
                "resolved_model": model or None,
                "model_ref": provider.get("model_ref") or provider.get("profile_ref"),
                "agent_config_source": provider.get("agent_config_source"),
                "model_config_source": provider.get("model_config_source"),
                "config_hash": provider.get("config_hash"),
                "resolved_max_tokens": provider.get("max_tokens"),
                "sdk_retry": 0,
                "streaming": False,
                "chunk_diagnostics": "NOT_APPLICABLE",
                "chunk_count": 0,
                "chunk_sequence_verification": "NOT_APPLICABLE_NON_STREAMING",
            }
        )
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

        max_tokens = provider.get("max_tokens")
        capabilities = provider.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
        token_parameter = str(
            capabilities.get("token_limit_parameter") or "max_tokens"
        ).strip()
        if max_tokens is not None:
            if token_parameter == "max_completion_tokens":
                body["max_completion_tokens"] = max_tokens
            else:
                body["max_tokens"] = max_tokens

        response_format, structured_evidence = _structured_output_contract(
            provider,
            self.output_schema,
            self.response_shape,
        )
        evidence.update(structured_evidence)
        if response_format is not None:
            body["response_format"] = response_format

        evidence.update(
            {
                "request_max_tokens": body.get("max_tokens", "NOT_SENT"),
                "request_max_completion_tokens": body.get(
                    "max_completion_tokens",
                    "NOT_SENT",
                ),
            }
        )

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
        evidence.update(
            {
                "request_method": "POST",
                "request_path": urlsplit(endpoint).path,
                "request_body_length": len(body_bytes),
                "request_body_hash": hashlib.sha256(body_bytes).hexdigest(),
            }
        )
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
                evidence["response_http_status"] = getattr(response, "status", None)
                evidence["provider_request_id"] = _provider_request_id(
                    getattr(response, "headers", None)
                )
                raw_bytes = response.read()
                evidence["response_body_length"] = len(raw_bytes)
                evidence["response_body_hash"] = hashlib.sha256(raw_bytes).hexdigest()
                raw_body = raw_bytes.decode("utf-8")
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
                            "response_body_length": len(raw_bytes),
                            "response_body_hash": hashlib.sha256(raw_bytes).hexdigest(),
                        }
                    )
        except HTTPError as exc:
            error_body = b""
            try:
                error_body = exc.read()
            except Exception:
                error_body = b""
            request_id = _provider_request_id(getattr(exc, "headers", None))
            evidence.update(
                {
                    "response_http_status": int(exc.code),
                    "provider_request_id": request_id,
                    "response_body_length": len(error_body),
                    "response_body_hash": hashlib.sha256(error_body).hexdigest(),
                }
            )
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
                    "response_body_length": len(error_body),
                    "response_body_hash": hashlib.sha256(error_body).hexdigest(),
                }
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
            evidence["transport_error_type"] = type(exc).__name__
            if _provider_trace_enabled():
                _write_provider_trace(
                    {
                        "ts": datetime.now().isoformat(timespec="milliseconds"),
                        "phase": "transport_error",
                        "provider_call_seq": runtime_context.get("provider_call_seq"),
                        "error_type": type(exc).__name__,
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
            usage = envelope.get("usage")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            evidence["envelope_parse_status"] = "FAIL"
            raise RuntimeStepError(
                "provider returned an invalid OpenAI-compatible envelope",
                code="PROVIDER_ENVELOPE_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            ) from exc

        evidence["envelope_parse_status"] = "PASS"
        evidence["raw_finish_reason"] = (
            finish_reason if finish_reason is not None else "NOT_RETURNED"
        )
        evidence["raw_usage"] = (
            _safe_usage(usage) if usage is not None else "NOT_RETURNED"
        )

        if not isinstance(content, str) or not content.strip():
            evidence.update(
                {
                    "content_length": len(content) if isinstance(content, str) else 0,
                    "content_hash": (
                        _sha256_text(content) if isinstance(content, str) else None
                    ),
                }
            )
            raise RuntimeStepError(
                "provider returned empty content",
                code="EMPTY_PROVIDER_CONTENT",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )

        original_hash = _sha256_text(content)
        evidence.update(
            {
                "content_length": len(content),
                "content_hash": original_hash,
                "aggregate_length": len(content),
                "aggregate_hash": original_hash,
                "failure_origin": "PROVIDER_CONTENT",
                "recovered": False,
                "recovery_type": "NONE",
                "original_content_hash": original_hash,
            }
        )

        completion_tokens = None
        if isinstance(usage, dict):
            candidate = usage.get("completion_tokens")
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                completion_tokens = candidate
        request_limit = body.get("max_tokens", body.get("max_completion_tokens"))
        evidence["token_usage_contract_anomaly"] = bool(
            isinstance(request_limit, (int, float))
            and completion_tokens is not None
            and completion_tokens > request_limit
        )

        if str(finish_reason or "").lower() == "length":
            evidence["recovery_classification"] = "SEMANTIC_REPAIR_REQUIRED"
            _stage_semantic_handoff(runtime_context, content)
            raise RuntimeStepError(
                "provider output was truncated",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={
                    "finish_reason": "length",
                    "recovery_classification": "SEMANTIC_REPAIR_REQUIRED",
                },
            )

        try:
            parsed = json.loads(content)
            evidence["strict_parse_status"] = "PASS"
        except json.JSONDecodeError as exc:
            evidence["strict_parse_status"] = "FAIL"
            recovered_content = _deterministic_wrapper_recovery(content)
            if recovered_content is None:
                evidence["recovery_classification"] = "SEMANTIC_REPAIR_REQUIRED"
                _stage_semantic_handoff(runtime_context, content)
                raise RuntimeStepError(
                    "provider content requires semantic repair",
                    code="SEMANTIC_REPAIR_REQUIRED",
                    category=ErrorCategory.VALIDATION,
                    retryable=True,
                    details={
                        "original_content_hash": original_hash,
                        "recovered": False,
                    },
                ) from exc

            recovered_hash = _sha256_text(recovered_content)
            evidence.update(
                {
                    "recovered": True,
                    "recovery_type": "DETERMINISTIC_WRAPPER_RECOVERY",
                    "recovery_classification": "DETERMINISTIC_WRAPPER_RECOVERY",
                    "recovered_content_length": len(recovered_content),
                    "recovered_content_hash": recovered_hash,
                }
            )
            try:
                parsed = json.loads(recovered_content)
            except json.JSONDecodeError as recovery_exc:
                evidence["strict_parse_after_recovery"] = "FAIL"
                raise RuntimeStepError(
                    "deterministic wrapper recovery failed strict JSON parse",
                    code="DETERMINISTIC_WRAPPER_RECOVERY_FAILED",
                    category=ErrorCategory.VALIDATION,
                    retryable=True,
                ) from recovery_exc
            evidence["strict_parse_after_recovery"] = "PASS"

        try:
            validated = self._validate_output(parsed)
        except RuntimeStepError:
            if evidence.get("recovered"):
                evidence["schema_validation_after_recovery"] = "FAIL"
            raise
        if evidence.get("recovered"):
            evidence["schema_validation_after_recovery"] = "PASS"
        evidence["schema_validation_status"] = "PASS"
        return validated


__all__ = ["OpenAICompatibleProviderAdapter", "reconstruct_transport_content"]
