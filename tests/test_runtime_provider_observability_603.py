"""Issue #603: no-content completion diagnostics for Hardware Stage B.

These tests use an in-memory OpenAI-compatible envelope; no external model,
credentials, filesystem data, or Provider calls are required.
"""

from __future__ import annotations

import json

import pytest

import runtime.providers.openai_compatible as provider_module
from runtime.providers import OpenAICompatibleProviderAdapter
from runtime.reliability import RuntimeStepError


class _FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, data):
        self.payload = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


@pytest.mark.parametrize(
    ("reason", "usage", "expected_code", "expected_reason"),
    [
        ("length", {"prompt_tokens": 100, "completion_tokens": 3072}, "OUTPUT_TRUNCATED", "length"),
        ("stop", {"prompt_tokens": 100, "completion_tokens": 66}, "INVALID_JSON", "stop"),
        (None, None, "INVALID_JSON", "unknown"),
    ],
)
def test_completion_error_keeps_bounded_metadata_without_content(
    monkeypatch, reason, usage, expected_code, expected_reason
):
    text = '{"truncated":'
    response = {"choices": [{"message": {"content": text}}]}
    if reason is not None:
        response["choices"][0]["finish_reason"] = reason
    if usage is not None:
        response["usage"] = usage

    monkeypatch.setattr(
        provider_module,
        "urlopen",
        lambda request, timeout: _FakeResponse(response),
    )
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema={"type": "object"},
        response_shape="json_object",
    )
    context = {
        "runtime": {
            "provider_config": {
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "mock-model",
                "max_tokens": 3072,
            }
        },
        "provider_metrics": {},
    }
    with pytest.raises(RuntimeStepError) as captured:
        adapter({"input": "private business text"}, context)
    error = captured.value
    assert error.code == expected_code
    assert error.details["finish_reason"] == expected_reason
    assert error.details["effective_max_tokens"] == 3072
    assert error.details["output_chars"] == len(text)
    assert error.details.get("prompt_tokens") == (usage or {}).get("prompt_tokens")
    assert error.details.get("completion_tokens") == (usage or {}).get("completion_tokens")
    if expected_code == "INVALID_JSON":
        assert type(error.details["json_error_pos"]) is int
    assert "truncated" not in str(error.details)
    assert "private business text" not in str(error.details)
    assert context["provider_metrics"]["finish_reason"] == expected_reason


def test_valid_json_tracks_usage_without_affecting_business_output(monkeypatch):
    response = {
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4},
    }
    monkeypatch.setattr(
        provider_module,
        "urlopen",
        lambda request, timeout: _FakeResponse(response),
    )
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema={"type": "object"},
    )
    context = {
        "runtime": {
            "provider_config": {
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "mock-model",
                "max_tokens": 3072,
            }
        },
        "provider_metrics": {},
    }
    assert adapter({"input": "redact-me"}, context) == {"ok": True}
    assert context["provider_metrics"] == {
        "finish_reason": "stop",
        "prompt_tokens": 11,
        "completion_tokens": 4,
        "effective_max_tokens": 3072,
        "output_chars": len('{"ok":true}'),
        "output_bytes": len('{"ok":true}'.encode("utf-8")),
    }
