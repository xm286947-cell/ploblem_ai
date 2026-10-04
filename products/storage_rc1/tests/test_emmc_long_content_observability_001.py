from __future__ import annotations

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from runtime import (
    ErrorCategory,
    ExecutionPolicy,
    RetryBudget,
    RetryPolicy,
    RuntimeStepError,
    RuntimeStatus,
    SqliteTaskStore,
)
from runtime.content import LongContentRecoveryExecutor
from runtime.engine import LightweightExecutionEngine
from storage_life import runtime_bridge
from storage_life.runtime_domain_strategy import EMMC_FIELD_ORDER, StorageEmmcDomainStrategy


RUNTIME_PIN = "9e36eeb0237459b884ee0d3e663ccf5833bfc685"


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "array",
                "minItems": len(EMMC_FIELD_ORDER),
                "maxItems": len(EMMC_FIELD_ORDER),
                "items": {
                    "type": "object",
                    "properties": {"field_key": {"type": "string", "enum": list(EMMC_FIELD_ORDER)}},
                },
            }
        },
        "required": ["fields"],
    }


def _payload() -> dict:
    source_id = "emmc-observability-source"
    page_text = "OBSERVABILITY_SOURCE_BODY_MUST_NOT_LEAK"
    return {
        "device_type": "eMMC",
        "vendor_hint": "Acme",
        "product_family_hint": "Observability Test",
        "primary_source_id": source_id,
        "pages": [{"source_id": source_id, "page": 1, "text": page_text}],
        "page_text": f"SOURCE {source_id} PAGE 1\n{page_text}",
    }


def _fact(field_key: str) -> dict:
    return {
        "field_key": field_key,
        "value": None,
        "unit": None,
        "condition": None,
        "scope_type": "product_family",
        "scope_values": [],
        "evidence": None,
        "conflict_evidence": [],
        "confidence": 1,
        "status": "missing",
        "derived": False,
        "knowledge_type": "specification",
    }


def _executor(tmp_path, provider):
    store = SqliteTaskStore(tmp_path / "emmc-observability-runtime.sqlite3")
    runtime = LightweightExecutionEngine(store)
    executor = LongContentRecoveryExecutor(
        runtime,
        store,
        chunk_payload_builder=runtime_bridge._emmc_chunk_payload_builder,
        merger=runtime_bridge._StorageEmmcResultMerger(),
        final_validator=runtime_bridge._emmc_final_validator,
        business_gate=runtime_bridge._emmc_business_gate,
        step_execution_policy=ExecutionPolicy(
            validation_retry=RetryPolicy(max_attempts=1),
            retry_budget=RetryBudget(max_provider_calls_per_step=2),
        ),
    )
    executor.bind_agent(
        agent_id=runtime_bridge.EMMC_PARAMETER_AGENT_ID,
        provider_handler=provider,
        execution_policy=executor.step_execution_policy,
    )
    return executor


def _bind_projection(monkeypatch, executor):
    runtime_bridge.reset_for_tests()
    monkeypatch.setattr(runtime_bridge, "_get_emmc_long_content_executor", lambda: executor)
    monkeypatch.setattr(
        runtime_bridge,
        "_get_runtime",
        lambda: (
            executor.runtime,
            {},
            None,
            None,
            {"head": None, "snapshot_commit": RUNTIME_PIN},
            None,
        ),
    )
def _run_bridge_call():
    return runtime_bridge._call_emmc_long_content(
        "OBSERVABILITY_INSTRUCTIONS_MUST_NOT_LEAK",
        _payload(),
        _schema(),
    )


def test_t01_completed_six_group_observation_uses_runtime_persisted_facts(tmp_path, monkeypatch):
    calls = Counter()

    def provider(request, _context):
        fields = request["provider_payload"]["target_fields"]
        calls[tuple(fields)] += 1
        return {"fields": [_fact(key) for key in fields]}

    executor = _executor(tmp_path, provider)
    _bind_projection(monkeypatch, executor)
    result = _run_bridge_call()
    event = runtime_bridge.last_executions()[-1]

    expected = [list(fields) for _name, fields in StorageEmmcDomainStrategy.atomic_groups]
    chunks = event["chunks"]
    assert result["fields"]
    assert event["observability_status"] == "READY"
    assert event["strategy_ref"] == StorageEmmcDomainStrategy.strategy_ref
    assert event["runtime_commit"] == RUNTIME_PIN
    assert event["chunk_count"] == 6
    assert event["provider_calls"] == executor.store.count_task_provider_calls(event["task_id"]) == 6
    assert [chunk["group_id"] for chunk in chunks] == [f"emmc:{name}" for name, _ in StorageEmmcDomainStrategy.atomic_groups]
    assert [chunk["target_fields"] for chunk in chunks] == expected
    assert [chunk["field_count"] for chunk in chunks] == [8, 4, 8, 5, 5, 7]
    assert all(chunk["status"] == "COMPLETED" for chunk in chunks)
    assert all(chunk["committed"] is True for chunk in chunks)
    assert all(chunk["provider_calls"] == 1 for chunk in chunks)
    assert all(chunk["attempt_count"] >= 1 and chunk["run_count"] == 1 for chunk in chunks)
    assert all(chunk["duration_ms"] >= 0 for chunk in chunks)
    assert sum(calls.values()) == 6


def test_t02_partial_failure_exposes_only_sanitized_runtime_error(tmp_path, monkeypatch):
    calls = []

    def provider(request, _context):
        fields = request["provider_payload"]["target_fields"]
        calls.append(tuple(fields))
        if len(calls) == 4:
            raise RuntimeStepError(
                "RAW_PROVIDER_ERROR_MUST_NOT_LEAK",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"body": "RAW_PROVIDER_BODY_MUST_NOT_LEAK"},
            )
        return {"fields": [_fact(key) for key in fields]}

    executor = _executor(tmp_path, provider)
    _bind_projection(monkeypatch, executor)
    with pytest.raises(runtime_bridge.RuntimeBridgeCallError):
        _run_bridge_call()
    event = runtime_bridge.last_executions()[-1]
    chunks = event["chunks"]

    assert event["observability_status"] == "READY"
    assert [chunk["committed"] for chunk in chunks[:3]] == [True, True, True]
    assert chunks[3]["status"] in {"FAILED", "PARTIAL", "ERROR"}
    assert chunks[3]["error"] == {
        "code": "OUTPUT_TRUNCATED",
        "category": "VALIDATION",
        "retryable": True,
    }
    assert chunks[4]["status"] == "PENDING"
    assert chunks[5]["status"] == "PENDING"
    assert chunks[4]["provider_calls"] == chunks[5]["provider_calls"] == 0
    assert len(calls) == 4
    serialized = json.dumps(event, ensure_ascii=False)
    assert "RAW_PROVIDER_ERROR_MUST_NOT_LEAK" not in serialized
    assert "RAW_PROVIDER_BODY_MUST_NOT_LEAK" not in serialized


def test_t03_resume_observation_reuses_committed_chunks_and_matches_store(tmp_path, monkeypatch):
    calls = []
    fail_once = {"pending": True}

    def provider(request, _context):
        fields = request["provider_payload"]["target_fields"]
        calls.append(tuple(fields))
        if len(calls) == 4 and fail_once["pending"]:
            fail_once["pending"] = False
            raise RuntimeStepError(
                "temporary failure",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )
        return {"fields": [_fact(key) for key in fields]}

    executor = _executor(tmp_path, provider)
    _bind_projection(monkeypatch, executor)
    with pytest.raises(runtime_bridge.RuntimeBridgeCallError):
        _run_bridge_call()

    original_duration = runtime_bridge._runtime_step_duration_ms
    reused_duration_reads = []

    def duration_with_reuse_sentinel(step_run):
        if (getattr(step_run, "metadata", {}) or {}).get("reused_committed_execution"):
            reused_duration_reads.append(step_run.step_run_id)
            return 123456
        return original_duration(step_run)

    monkeypatch.setattr(runtime_bridge, "_runtime_step_duration_ms", duration_with_reuse_sentinel)
    result = _run_bridge_call()
    event = runtime_bridge.last_executions()[-1]
    chunks = event["chunks"]

    request = executor.store.load_request(event["task_id"])
    step_chunk_map = request.input["step_chunk_map"]
    expected_active_duration_by_chunk = {}
    reused_step_count = 0
    for run in executor.store.list_runs(event["task_id"]):
        for step_run in executor.store.list_step_runs(run.run_id):
            if (getattr(step_run, "metadata", {}) or {}).get("reused_committed_execution"):
                reused_step_count += 1
                continue
            chunk_id = step_chunk_map.get(step_run.step_id)
            if chunk_id is not None:
                expected_active_duration_by_chunk[chunk_id] = (
                    expected_active_duration_by_chunk.get(chunk_id, 0)
                    + original_duration(step_run)
                )

    assert result["fields"]
    assert event["observability_status"] == "READY"
    assert event["resume_count"] >= 1
    assert event["provider_calls"] == executor.store.count_task_provider_calls(event["task_id"]) == 7
    assert [chunk["run_count"] for chunk in chunks] == [1, 1, 1, 2, 1, 1]
    assert [chunk["provider_calls"] for chunk in chunks] == [1, 1, 1, 2, 1, 1]
    assert all(chunk["committed"] is True for chunk in chunks)
    assert reused_step_count >= 3
    assert reused_duration_reads == []
    assert [chunk["duration_ms"] for chunk in chunks] == [
        expected_active_duration_by_chunk[chunk["chunk_id"]] for chunk in chunks
    ]
    assert event["duration_ms"] == sum(expected_active_duration_by_chunk.values())
    assert calls == [tuple(group) for _name, group in StorageEmmcDomainStrategy.atomic_groups[:4]] + [
        tuple(StorageEmmcDomainStrategy.atomic_groups[3][1]),
        tuple(StorageEmmcDomainStrategy.atomic_groups[4][1]),
        tuple(StorageEmmcDomainStrategy.atomic_groups[5][1]),
    ]


def test_t04_t05_payload_metrics_and_fingerprint_semantics(tmp_path, monkeypatch):
    def provider(request, _context):
        return {"fields": [_fact(key) for key in request["provider_payload"]["target_fields"]]}

    executor = _executor(tmp_path, provider)
    _bind_projection(monkeypatch, executor)
    _run_bridge_call()
    event = runtime_bridge.last_executions()[-1]

    assert all(chunk["provider_contract_chars"] > 0 for chunk in event["chunks"])
    assert all(chunk["source_text_chars"] > 0 for chunk in event["chunks"])
    assert all(chunk["selected_page_count"] == 1 for chunk in event["chunks"])
    assert event["source_fingerprint_kind"] == "structured_source_text_sha256"
    assert not {"source_sha256", "binary_sha256", "original_file_sha256", "body_bytes", "request_bytes"}.intersection(event)
    assert all("body_bytes" not in chunk and "request_bytes" not in chunk for chunk in event["chunks"])


def test_t06_event_does_not_leak_secret_prompt_or_source_text(tmp_path, monkeypatch):
    def provider(request, _context):
        return {"fields": [_fact(key) for key in request["provider_payload"]["target_fields"]]}

    executor = _executor(tmp_path, provider)
    _bind_projection(monkeypatch, executor)
    _run_bridge_call()
    serialized = json.dumps(runtime_bridge.last_executions()[-1], ensure_ascii=False)

    for forbidden in (
        "OBSERVABILITY_INSTRUCTIONS_MUST_NOT_LEAK",
        "OBSERVABILITY_SOURCE_BODY_MUST_NOT_LEAK",
        "Authorization",
        "mock-secret",
        "provider_payload",
    ):
        assert forbidden not in serialized


def test_t07_generic_runtime_event_remains_compatible_without_chunks(monkeypatch):
    class FakeRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeRuntime:
        def invoke(self, _request):
            return SimpleNamespace(
                execution=SimpleNamespace(
                    model_dump=lambda mode="json": {
                        "provider_calls": 1,
                        "duration_ms": 12,
                        "provider": "openai_compatible",
                        "model": "mock-gpt",
                    }
                ),
                status=RuntimeStatus.COMPLETED,
                task_id="generic-task",
                run_id="generic-run",
                error=None,
                data={"ok": True},
            )

    resolved = SimpleNamespace(
        provider=SimpleNamespace(type="openai_compatible", model="mock-gpt")
    )
    runtime_bridge.reset_for_tests()
    monkeypatch.setattr(
        runtime_bridge,
        "_get_runtime",
        lambda: (FakeRuntime(), {"generic-agent": resolved}, FakeRequest, RuntimeStatus, {"head": RUNTIME_PIN}, None),
    )
    result = runtime_bridge._invoke_json("generic-agent", "instruction", {"x": 1}, {"type": "object"})
    event = runtime_bridge.last_executions()[-1]

    assert result == {"ok": True}
    assert event["request_id"].startswith("storage-")
    assert event["provider_calls"] == 1
    assert "chunks" not in event
    assert "observability_status" not in event


def test_t08_observability_failure_does_not_change_successful_extraction(tmp_path, monkeypatch):
    def provider(request, _context):
        return {"fields": [_fact(key) for key in request["provider_payload"]["target_fields"]]}

    executor = _executor(tmp_path, provider)
    _bind_projection(monkeypatch, executor)
    monkeypatch.setattr(
        runtime_bridge,
        "_build_emmc_long_content_observation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unsafe failure text")),
    )
    result = _run_bridge_call()
    event = runtime_bridge.last_executions()[-1]

    assert len(result["fields"]) == len(EMMC_FIELD_ORDER)
    assert event["observability_status"] == "DEGRADED"
    assert event["observability_error"] == {
        "type": "RuntimeError",
        "message": "Runtime execution evidence could not be projected.",
    }
    assert "unsafe failure text" not in json.dumps(event)
