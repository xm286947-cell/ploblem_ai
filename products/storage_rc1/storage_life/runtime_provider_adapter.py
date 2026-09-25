"""Runtime-managed single-attempt Provider Adapter for Storage integration.

This module is intentionally separate from the legacy/local-direct provider path in
``storage_life.ai``.  It MUST be used for Unified Agent Runtime managed execution.

Boundary:
- one adapter invocation == one real Provider HTTP request;
- no SDK/transport/validation retry inside the adapter;
- provider/model/secret resolution comes from Runtime context, not Storage YAML/env;
- retryable failures are surfaced as RuntimeStepError and retried only by Runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import nullcontext
from typing import Any
from urllib.parse import urlsplit

import httpx


def _trace_enabled() -> bool:
    return str(os.environ.get("STORAGE_RUNTIME_HTTP_TRACE", "0")).strip().lower() not in {"", "0", "false", "no", "off"}


def _trace(message: str, **fields: Any) -> None:
    """Emit a single persistence-safe HTTP diagnostic line for product testing.

    Never print Authorization, API keys, prompt/source text, or full response bodies.
    """
    if not _trace_enabled():
        return
    safe = []
    for key, value in fields.items():
        if value is None:
            continue
        safe.append(f"{key}={value}")
    line = "[STORAGE_RUNTIME_HTTP] " + message
    if safe:
        line += " " + " ".join(safe)
    print(line, file=sys.stderr, flush=True)


def _proxy_env_summary() -> str:
    names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    return ",".join(f"{name}:{'set' if os.environ.get(name) else 'unset'}" for name in names)


def _runtime_types():
    """Lazy import keeps the legacy standalone Storage app runnable without Runtime."""
    try:
        from runtime.contracts import ErrorCategory
        from runtime.reliability import RuntimeStepError
    except ImportError as exc:  # pragma: no cover - exercised only outside integration env
        raise RuntimeError(
            "Unified Agent Runtime is required for runtime-managed provider execution"
        ) from exc
    return ErrorCategory, RuntimeStepError


def _raise_runtime_error(
    message: str,
    *,
    code: str,
    category_name: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
):
    ErrorCategory, RuntimeStepError = _runtime_types()
    category = getattr(ErrorCategory, category_name)
    raise RuntimeStepError(
        message,
        code=code,
        category=category,
        retryable=retryable,
        details=details or {},
    )


def _provider_config(context: dict[str, Any]) -> dict[str, Any]:
    runtime_context = (context or {}).get("runtime") or {}
    cfg = runtime_context.get("provider_config") or {}
    if not isinstance(cfg, dict):
        _raise_runtime_error(
            "Runtime provider_config is missing or invalid",
            code="PROVIDER_CONFIG_INVALID",
            category_name="EXECUTION",
            retryable=False,
        )
    if cfg.get("sdk_retry", 0) not in (0, None):
        _raise_runtime_error(
            "Runtime-managed Storage requires sdk_retry=0",
            code="PROVIDER_RETRY_OWNERSHIP_VIOLATION",
            category_name="EXECUTION",
            retryable=False,
            details={"sdk_retry": cfg.get("sdk_retry")},
        )
    base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
    model = str(cfg.get("model") or "").strip()
    if not base_url or not model:
        _raise_runtime_error(
            "Runtime provider_config must contain resolved base_url and model",
            code="PROVIDER_CONFIG_INCOMPLETE",
            category_name="EXECUTION",
            retryable=False,
        )
    return cfg


def _json_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if text.startswith("```"):
        import re

        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
            except ValueError:
                value = None
        else:
            value = None
    if not isinstance(value, dict):
        _raise_runtime_error(
            "Provider returned invalid structured JSON",
            code="PROVIDER_JSON_INVALID",
            category_name="VALIDATION",
            retryable=True,
        )
    return value


def call_openai_compatible_once(
    *,
    instructions: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    context: dict[str, Any],
    client: httpx.Client | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Make exactly one OpenAI-compatible chat/completions request.

    Runtime reserves/counts the Provider Attempt before calling the handler.  This
    adapter therefore MUST NOT retry.  A retryable error is mapped to RuntimeStepError
    and returned to the Runtime retry coordinator.
    """
    cfg = _provider_config(context)
    provider_type = str(cfg.get("type") or "openai_compatible").strip().lower()
    if provider_type not in {"openai_compatible", "openai-compatible", "generic"}:
        _raise_runtime_error(
            f"Unsupported Storage Runtime provider type: {provider_type}",
            code="PROVIDER_TYPE_UNSUPPORTED",
            category_name="EXECUTION",
            retryable=False,
            details={"provider_type": provider_type},
        )

    headers = {"Content-Type": "application/json"}
    api_key = cfg.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    body: dict[str, Any] = {
        "model": cfg["model"],
        "temperature": cfg.get("temperature", 0),
        "messages": [
            {
                "role": "system",
                "content": instructions
                + "\nReturn ONLY one JSON object matching this JSON Schema exactly. No markdown. JSON Schema: "
                + schema_text,
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    if cfg.get("max_tokens") is not None:
        body["max_tokens"] = int(cfg["max_tokens"])

    owned = client is None
    if owned:
        # httpx has no application retry by default; set transport retries=0 explicitly
        # so the Storage boundary remains auditable.
        transport = httpx.HTTPTransport(retries=0)
        client = httpx.Client(timeout=timeout_seconds, transport=transport)
    cm = client if owned else nullcontext(client)

    request_url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    parsed_url = urlsplit(request_url)
    runtime_context = (context or {}).get("runtime") or {}
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _trace(
        "request",
        provider_call_seq=runtime_context.get("provider_call_seq"),
        method="POST",
        url=request_url,
        scheme=parsed_url.scheme or "<missing>",
        host=parsed_url.hostname or "<missing>",
        port=parsed_url.port or (443 if parsed_url.scheme == "https" else 80 if parsed_url.scheme == "http" else "<default>"),
        path=parsed_url.path,
        provider=provider_type,
        model=cfg.get("model"),
        timeout_s=timeout_seconds,
        sdk_retry=cfg.get("sdk_retry", 0),
        auth=("bearer:redacted" if api_key else "none"),
        body_bytes=len(body_bytes),
        body_sha256=hashlib.sha256(body_bytes).hexdigest()[:16],
        proxy_env=_proxy_env_summary(),
    )
    if parsed_url.scheme not in {"http", "https"}:
        _trace("warning", issue="invalid_url_scheme", url=request_url)

    try:
        with cm as active_client:
            try:
                # Exactly one real Provider request per adapter invocation.
                response = active_client.post(
                    request_url,
                    json=body,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                _trace(
                    "exception",
                    provider_call_seq=runtime_context.get("provider_call_seq"),
                    error_type=type(exc).__name__,
                    error=str(exc).replace("\n", " ")[:500],
                    url=request_url,
                )
                _raise_runtime_error(
                    "Provider request timed out",
                    code="PROVIDER_TIMEOUT",
                    category_name="TRANSPORT",
                    retryable=True,
                    details={"error_type": type(exc).__name__},
                )
            except httpx.RequestError as exc:
                _trace(
                    "exception",
                    provider_call_seq=runtime_context.get("provider_call_seq"),
                    error_type=type(exc).__name__,
                    error=str(exc).replace("\n", " ")[:500],
                    url=request_url,
                )
                _raise_runtime_error(
                    "Provider network request failed",
                    code="PROVIDER_NETWORK_ERROR",
                    category_name="TRANSPORT",
                    retryable=True,
                    details={"error_type": type(exc).__name__, "url": request_url},
                )

            status = int(response.status_code)
            _trace(
                "response",
                provider_call_seq=runtime_context.get("provider_call_seq"),
                status=status,
                http_version=getattr(response, "http_version", None),
                content_type=response.headers.get("content-type"),
                content_length=response.headers.get("content-length"),
                url=request_url,
            )
            if status == 429:
                _raise_runtime_error(
                    "Provider rate limited request",
                    code="PROVIDER_RATE_LIMITED",
                    category_name="TRANSPORT",
                    retryable=True,
                    details={"http_status": status},
                )
            if status in {408, 425} or 500 <= status <= 599:
                _raise_runtime_error(
                    f"Provider temporary HTTP failure: {status}",
                    code="PROVIDER_TEMPORARY_HTTP_ERROR",
                    category_name="TRANSPORT",
                    retryable=True,
                    details={"http_status": status},
                )
            if status >= 400:
                _raise_runtime_error(
                    f"Provider rejected request: HTTP {status}",
                    code=("PROVIDER_AUTH_FAILED" if status in {401, 403} else "PROVIDER_REQUEST_REJECTED"),
                    category_name="EXECUTION",
                    retryable=False,
                    details={"http_status": status},
                )

            try:
                data = response.json()
            except ValueError:
                _raise_runtime_error(
                    "Provider response body is not JSON",
                    code="PROVIDER_PROTOCOL_INVALID",
                    category_name="VALIDATION",
                    retryable=True,
                )

            choices = data.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                _raise_runtime_error(
                    "Provider response has no usable choice",
                    code="PROVIDER_RESPONSE_INCOMPLETE",
                    category_name="VALIDATION",
                    retryable=True,
                )
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                _raise_runtime_error(
                    "Provider output was truncated by token/output limit",
                    code="OUTPUT_TRUNCATED",
                    category_name="VALIDATION",
                    retryable=True,
                    details={"finish_reason": finish_reason},
                )
            if finish_reason in {"content_filter", "insufficient_system_resource", "aborted"}:
                _raise_runtime_error(
                    f"Provider returned terminal finish_reason={finish_reason}",
                    code="PROVIDER_TERMINAL_RESPONSE",
                    category_name="EXECUTION",
                    retryable=False,
                    details={"finish_reason": finish_reason},
                )

            content = (choice.get("message") or {}).get("content", "")
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            if not str(content or "").strip():
                _raise_runtime_error(
                    "Provider returned empty structured content",
                    code="PROVIDER_RESPONSE_EMPTY",
                    category_name="VALIDATION",
                    retryable=True,
                )
            return _json_object(str(content))
    finally:
        # Owned clients are closed by their context manager; injected clients are never
        # closed here because the caller owns their lifecycle.
        pass


def runtime_provider_handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Canonical handler shape for Unified Runtime registration.

    S-A03 will provide the Storage Domain Strategy that creates these three inputs from
    SourceBundle / AtomicGroup.  S-A02 freezes only the Provider boundary.
    """
    if not isinstance(payload, dict):
        _raise_runtime_error(
            "Storage Runtime provider payload must be an object",
            code="PROVIDER_PAYLOAD_INVALID",
            category_name="EXECUTION",
            retryable=False,
        )
    instructions = payload.get("instructions")
    provider_payload = payload.get("provider_payload")
    schema = payload.get("schema")
    if not isinstance(instructions, str) or not isinstance(provider_payload, dict) or not isinstance(schema, dict):
        _raise_runtime_error(
            "Storage Runtime provider payload requires instructions/provider_payload/schema",
            code="PROVIDER_PAYLOAD_INVALID",
            category_name="EXECUTION",
            retryable=False,
        )
    return call_openai_compatible_once(
        instructions=instructions,
        payload=provider_payload,
        schema=schema,
        context=context,
    )
