from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "vendor" / "unified_agent_runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from runtime.providers.openai_compatible import OpenAICompatibleProviderAdapter
from runtime.reliability.errors import RuntimeStepError


class Result(BaseModel):
    ok: bool


class FakeResponse:
    def __init__(self, content: str, *, finish_reason: str = "stop", usage=None):
        self.status = 200
        self.headers = {"Content-Type": "application/json", "x-request-id": "storage-real-gate-mock"}
        self._raw = json.dumps(
            {
                "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
                "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._raw


def adapter():
    return OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema=Result,
        response_shape="json_object",
    )


def context(evidence: dict):
    return {
        "runtime": {
            "provider_call_seq": 1,
            "provider_evidence": evidence,
            "provider_config": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "qwen3.8-max",
                "model_ref": "qwen_prod",
                "agent_config_source": "config/runtime/storage.emmc.parameter_extract.yaml",
                "model_config_source": "config/model.windows.real.yaml",
                "config_hash": "integration-test-config-hash",
                "max_tokens": 8192,
                "temperature": 0,
                "capabilities": {},
                "capability_source": "UNKNOWN",
            },
        }
    }


def test_case1_legal_json_pass(monkeypatch):
    evidence = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: FakeResponse('{"ok":true}'),
    )
    result = adapter()({"source": "must-not-be-evidence"}, context(evidence))
    assert result == {"ok": True}
    assert evidence["strict_parse_status"] == "PASS"
    assert evidence["recovered"] is False
    assert evidence["request_max_tokens"] == 8192
    assert evidence["request_max_completion_tokens"] == "NOT_SENT"


def test_case2_wrapper_is_deterministically_recovered(monkeypatch):
    evidence = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: FakeResponse("```json\n{\"ok\":true}\n```"),
    )
    result = adapter()({"value": 1}, context(evidence))
    assert result == {"ok": True}
    assert evidence["recovered"] is True
    assert evidence["recovery_type"] == "DETERMINISTIC_WRAPPER_RECOVERY"
    assert evidence["strict_parse_after_recovery"] == "PASS"
    assert evidence["schema_validation_after_recovery"] == "PASS"


def test_case3_semantic_damage_fails_closed(monkeypatch):
    evidence = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: FakeResponse('{"ok":'),
    )
    with pytest.raises(RuntimeStepError) as exc_info:
        adapter()({"value": 1}, context(evidence))
    assert exc_info.value.code == "SEMANTIC_REPAIR_REQUIRED"
    assert evidence["recovered"] is False
    assert evidence["recovery_classification"] == "SEMANTIC_REPAIR_REQUIRED"


def test_request_limit_and_usage_are_kept_as_separate_facts(monkeypatch):
    evidence = {}
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: FakeResponse(
            '{"ok":true}',
            usage={
                "prompt_tokens": 3048,
                "completion_tokens": 70012,
                "total_tokens": 73008,
            },
        ),
    )
    result = adapter()({"value": 1}, context(evidence))
    assert result == {"ok": True}
    assert evidence["request_max_tokens"] == 8192
    assert evidence["raw_usage"]["completion_tokens"] == 70012
    assert evidence["token_usage_contract_anomaly"] is True
