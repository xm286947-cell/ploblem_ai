from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from runtime.providers import (
    OpenAICompatibleProviderAdapter,
    reconstruct_transport_content,
)
from runtime.reliability import RuntimeStepError


class SimpleResult(BaseModel):
    ok: bool


class FakeResponse:
    def __init__(self, body: dict, *, status: int = 200):
        self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.status = status
        self.headers = {
            "Content-Type": "application/json",
            "x-request-id": "req-strict-json-001",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._raw


def response(
    content: str,
    *,
    finish_reason: str = "stop",
    usage: dict | None = None,
) -> FakeResponse:
    return FakeResponse(
        {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage
            or {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )


def context(
    *,
    evidence: dict,
    capabilities: dict | None = None,
    max_tokens: int = 8192,
) -> dict:
    return {
        "runtime": {
            "provider_call_seq": 1,
            "provider_evidence": evidence,
            "provider_config": {
                "base_url": "https://provider.example/v1",
                "model": "company-model",
                "model_ref": "company_prod",
                "agent_config_source": "config/runtime/agents/test.yaml",
                "model_config_source": "config/model.local.yaml",
                "config_hash": "cfg-hash-001",
                "max_tokens": max_tokens,
                "temperature": 0,
                "capabilities": capabilities or {},
                "capability_source": (
                    "CONFIGURED" if capabilities else "UNKNOWN"
                ),
            },
        }
    }


def adapter(*, response_shape: str = "json_object") -> OpenAICompatibleProviderAdapter:
    return OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema=SimpleResult,
        response_shape=response_shape,
    )


def test_provider_evidence_records_request_usage_finish_reason_and_token_anomaly(
    monkeypatch,
) -> None:
    evidence: dict = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: response(
            '{"ok":true}',
            usage={
                "prompt_tokens": 3048,
                "completion_tokens": 70012,
                "total_tokens": 73008,
            },
        ),
    )

    result = adapter()({"source_text": "must-not-enter-evidence"}, context(evidence=evidence))

    assert result == {"ok": True}
    assert evidence["resolved_model"] == "company-model"
    assert evidence["model_ref"] == "company_prod"
    assert evidence["config_hash"] == "cfg-hash-001"
    assert evidence["resolved_max_tokens"] == 8192
    assert evidence["request_max_tokens"] == 8192
    assert evidence["request_max_completion_tokens"] == "NOT_SENT"
    assert evidence["raw_finish_reason"] == "stop"
    assert evidence["raw_usage"] == {
        "prompt_tokens": 3048,
        "completion_tokens": 70012,
        "total_tokens": 73008,
    }
    assert evidence["token_usage_contract_anomaly"] is True
    assert evidence["structured_output_capability"] == "UNKNOWN"
    assert evidence["structured_output_request"] == "NONE"
    assert evidence["streaming"] is False
    assert evidence["chunk_diagnostics"] == "NOT_APPLICABLE"
    serialized = json.dumps(evidence, ensure_ascii=False)
    assert "must-not-enter-evidence" not in serialized
    assert "Return strict JSON." not in serialized


def test_json_schema_capability_is_explicitly_requested(monkeypatch) -> None:
    captured: dict = {}
    evidence: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return response('{"ok":true}')

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )

    result = adapter()(
        {"value": 1},
        context(
            evidence=evidence,
            capabilities={"structured_output": "json_schema"},
        ),
    )

    assert result == {"ok": True}
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert evidence["structured_output_capability"] == "SUPPORTED_AND_REQUESTED"
    assert evidence["structured_output_request"] == "response_format/json_schema"
    assert evidence["response_format_type"] == "json_schema"


def test_json_object_capability_does_not_fake_array_support(monkeypatch) -> None:
    captured: dict = {}
    evidence: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return response('[{"ok":true}]')

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )

    result = adapter(response_shape="json_array")(
        {"value": 1},
        context(
            evidence=evidence,
            capabilities={"structured_output": "json_object"},
        ),
    )

    assert result == [{"ok": True}]
    assert "response_format" not in captured
    assert evidence["structured_output_capability"] == "SUPPORTED_NOT_REQUESTED"
    assert (
        evidence["structured_output_reason"]
        == "JSON_OBJECT_INCOMPATIBLE_WITH_ARRAY"
    )
    assert evidence["structured_output_fallback"] == "PROMPT_ONLY"


def test_token_limit_capability_can_use_max_completion_tokens(monkeypatch) -> None:
    captured: dict = {}
    evidence: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return response('{"ok":true}')

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )

    adapter()(
        {"value": 1},
        context(
            evidence=evidence,
            capabilities={"token_limit_parameter": "max_completion_tokens"},
        ),
    )

    assert "max_tokens" not in captured
    assert captured["max_completion_tokens"] == 8192
    assert evidence["request_max_tokens"] == "NOT_SENT"
    assert evidence["request_max_completion_tokens"] == 8192


@pytest.mark.parametrize(
    "wrapped",
    [
        '"""PLACEHOLDER"""',
        "Provider explanation before JSON:\n{\"ok\":true}\nDone.",
    ],
)
def test_deterministic_wrapper_recovery_reparses_and_validates(
    monkeypatch,
    wrapped: str,
) -> None:
    if wrapped == '"""PLACEHOLDER"""':
        wrapped = "```json\n{\"ok\":true}\n```"
    evidence: dict = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: response(wrapped),
    )

    result = adapter()({"value": 1}, context(evidence=evidence))

    assert result == {"ok": True}
    assert evidence["recovered"] is True
    assert evidence["recovery_type"] == "DETERMINISTIC_WRAPPER_RECOVERY"
    assert evidence["strict_parse_after_recovery"] == "PASS"
    assert evidence["schema_validation_after_recovery"] == "PASS"
    assert evidence["original_content_hash"] != evidence["recovered_content_hash"]


def test_truncated_outer_container_is_not_silently_recovered(monkeypatch) -> None:
    evidence: dict = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: response('[{"ok":true}'),
    )

    with pytest.raises(RuntimeStepError) as exc_info:
        adapter(response_shape="json_array")(
            {"value": 1},
            context(evidence=evidence),
        )

    assert exc_info.value.code == "SEMANTIC_REPAIR_REQUIRED"
    assert evidence["recovered"] is False
    assert evidence["recovery_classification"] == "SEMANTIC_REPAIR_REQUIRED"


def test_wrapper_recovery_still_requires_schema_validation(monkeypatch) -> None:
    evidence: dict = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: response(
            "Here is JSON:\n{\"wrong\":true}\nEnd."
        ),
    )

    with pytest.raises(RuntimeStepError) as exc_info:
        adapter()({"value": 1}, context(evidence=evidence))

    assert exc_info.value.code == "PROVIDER_SCHEMA_INVALID"
    assert evidence["recovered"] is True
    assert evidence["strict_parse_after_recovery"] == "PASS"
    assert evidence["schema_validation_after_recovery"] == "FAIL"


def test_finish_reason_length_is_semantic_repair_required(monkeypatch) -> None:
    evidence: dict = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: response(
            '{"ok":',
            finish_reason="length",
        ),
    )

    with pytest.raises(RuntimeStepError) as exc_info:
        adapter()({"value": 1}, context(evidence=evidence))

    assert exc_info.value.code == "OUTPUT_TRUNCATED"
    assert evidence["raw_finish_reason"] == "length"
    assert evidence["recovery_classification"] == "SEMANTIC_REPAIR_REQUIRED"


def test_transport_reconstruction_recovers_only_complete_reordered_chunks() -> None:
    validated, evidence = adapter().validate_transport_chunks(
        [
            {"sequence": 1, "content": "true}"},
            {"sequence": 0, "content": '{"ok":'},
        ]
    )

    assert validated == {"ok": True}
    assert evidence["recovered"] is True
    assert evidence["recovery_type"] == "TRANSPORT_RECONSTRUCTION"
    assert evidence["chunk_order_valid"] is False
    assert evidence["missing_chunk_detected"] is False
    assert evidence["strict_parse_after_recovery"] == "PASS"
    assert evidence["schema_validation_after_recovery"] == "PASS"
    assert [item["sequence"] for item in evidence["chunks"]] == [1, 0]
    assert all(item["length"] > 0 and item["hash"] for item in evidence["chunks"])


def test_transport_reconstruction_rejects_missing_chunk() -> None:
    with pytest.raises(RuntimeStepError) as exc_info:
        reconstruct_transport_content(
            [
                {"sequence": 0, "content": '{"ok":'},
                {"sequence": 2, "content": "true}"},
            ]
        )

    assert exc_info.value.code == "TRANSPORT_RECONSTRUCTION_FAILED"
    assert exc_info.value.details["missing_chunk_detected"] is True
