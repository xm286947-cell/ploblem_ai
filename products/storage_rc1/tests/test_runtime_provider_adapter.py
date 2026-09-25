import json
import sys
import types
from enum import Enum

import httpx
import pytest


def _install_runtime_stub(monkeypatch):
    class ErrorCategory(str, Enum):
        TRANSPORT = "TRANSPORT"
        VALIDATION = "VALIDATION"
        EXECUTION = "EXECUTION"
        BUSINESS = "BUSINESS"

    class RuntimeStepError(Exception):
        def __init__(self, message, *, code=None, category=None, retryable=None, details=None):
            super().__init__(message)
            self.code = code
            self.category = category
            self.retryable = retryable
            self.details = details or {}

    runtime = types.ModuleType("runtime")
    contracts = types.ModuleType("runtime.contracts")
    reliability = types.ModuleType("runtime.reliability")
    contracts.ErrorCategory = ErrorCategory
    reliability.RuntimeStepError = RuntimeStepError
    monkeypatch.setitem(sys.modules, "runtime", runtime)
    monkeypatch.setitem(sys.modules, "runtime.contracts", contracts)
    monkeypatch.setitem(sys.modules, "runtime.reliability", reliability)
    return ErrorCategory, RuntimeStepError


def _context(**overrides):
    cfg = {
        "type": "openai_compatible",
        "model": "glm-test",
        "base_url": "https://provider.example/v1",
        "api_key": "secret-test-only",
        "sdk_retry": 0,
        "max_tokens": 4096,
        "temperature": 0,
    }
    cfg.update(overrides)
    return {
        "runtime": {
            "provider_call_seq": 1,
            "provider_config": cfg,
        }
    }


def _schema():
    return {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }


def test_one_adapter_invocation_equals_one_http_request(monkeypatch):
    _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    seen = {"requests": 0, "body": None, "auth": None}

    def handler(request):
        seen["requests"] += 1
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = call_openai_compatible_once(
        instructions="test",
        payload={"x": 1},
        schema=_schema(),
        context=_context(),
        client=client,
    )
    assert result == {"ok": True}
    assert seen["requests"] == 1
    assert seen["body"]["model"] == "glm-test"
    assert seen["body"]["max_tokens"] == 4096
    assert seen["auth"] == "Bearer secret-test-only"


def test_output_truncation_maps_to_runtime_validation_without_internal_retry(monkeypatch):
    ErrorCategory, RuntimeStepError = _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": '{"ok":'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeStepError) as exc:
        call_openai_compatible_once(
            instructions="test",
            payload={},
            schema=_schema(),
            context=_context(),
            client=client,
        )
    assert calls["n"] == 1
    assert exc.value.code == "OUTPUT_TRUNCATED"
    assert exc.value.category == ErrorCategory.VALIDATION
    assert exc.value.retryable is True


def test_rate_limit_maps_to_transport_without_internal_retry(monkeypatch):
    ErrorCategory, RuntimeStepError = _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeStepError) as exc:
        call_openai_compatible_once(
            instructions="test", payload={}, schema=_schema(), context=_context(), client=client
        )
    assert calls["n"] == 1
    assert exc.value.category == ErrorCategory.TRANSPORT
    assert exc.value.retryable is True


def test_nonretryable_provider_rejection_maps_to_execution(monkeypatch):
    ErrorCategory, RuntimeStepError = _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeStepError) as exc:
        call_openai_compatible_once(
            instructions="test", payload={}, schema=_schema(), context=_context(), client=client
        )
    assert calls["n"] == 1
    assert exc.value.category == ErrorCategory.EXECUTION
    assert exc.value.retryable is False


def test_invalid_json_maps_to_validation_without_internal_retry(monkeypatch):
    ErrorCategory, RuntimeStepError = _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"broken":'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeStepError) as exc:
        call_openai_compatible_once(
            instructions="test", payload={}, schema=_schema(), context=_context(), client=client
        )
    assert calls["n"] == 1
    assert exc.value.code == "PROVIDER_JSON_INVALID"
    assert exc.value.category == ErrorCategory.VALIDATION
    assert exc.value.retryable is True


def test_sdk_retry_nonzero_is_rejected_before_http_request(monkeypatch):
    ErrorCategory, RuntimeStepError = _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeStepError) as exc:
        call_openai_compatible_once(
            instructions="test",
            payload={},
            schema=_schema(),
            context=_context(sdk_retry=1),
            client=client,
        )
    assert calls["n"] == 0
    assert exc.value.code == "PROVIDER_RETRY_OWNERSHIP_VIOLATION"
    assert exc.value.category == ErrorCategory.EXECUTION
    assert exc.value.retryable is False


def test_adapter_does_not_resolve_storage_local_provider_config(monkeypatch):
    _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    # No STORAGE_LIFE_AGENT_* or DASHSCOPE/ZHIPU env is required: Runtime context is source of truth.
    for name in (
        "STORAGE_LIFE_AGENT_BASE_URL",
        "STORAGE_LIFE_AGENT_MODEL",
        "STORAGE_LIFE_AGENT_API_KEY",
        "DASHSCOPE_API_KEY",
        "ZHIPU_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = call_openai_compatible_once(
        instructions="test", payload={}, schema=_schema(), context=_context(), client=client
    )
    assert result == {"ok": True}
    assert calls["n"] == 1


def test_provider_call_reconciliation_contract(monkeypatch):
    _install_runtime_stub(monkeypatch)
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    actual_requests = {"n": 0}

    def handler(request):
        actual_requests["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # Simulate two Runtime Provider Attempts. Each attempt invokes the Adapter once.
    for seq in (1, 2):
        context = _context()
        context["runtime"]["provider_call_seq"] = seq
        call_openai_compatible_once(
            instructions="test", payload={}, schema=_schema(), context=context, client=client
        )
    runtime_provider_calls = 2
    assert actual_requests["n"] == runtime_provider_calls


def test_http_trace_prints_endpoint_but_never_secret(monkeypatch, capsys):
    _install_runtime_stub(monkeypatch)
    monkeypatch.setenv("STORAGE_RUNTIME_HTTP_TRACE", "1")
    from storage_life.runtime_provider_adapter import call_openai_compatible_once

    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = call_openai_compatible_once(
        instructions="top secret prompt",
        payload={"private": "business-source-text"},
        schema=_schema(),
        context=_context(),
        client=client,
    )
    assert result == {"ok": True}
    trace = capsys.readouterr().err
    assert "[STORAGE_RUNTIME_HTTP] request" in trace
    assert "url=https://provider.example/v1/chat/completions" in trace
    assert "scheme=https" in trace
    assert "host=provider.example" in trace
    assert "auth=bearer:redacted" in trace
    assert "secret-test-only" not in trace
    assert "top secret prompt" not in trace
    assert "business-source-text" not in trace
