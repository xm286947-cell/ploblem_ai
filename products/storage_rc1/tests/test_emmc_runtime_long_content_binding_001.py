from __future__ import annotations

import json
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from runtime import (
    ExecutionPolicy,
    ErrorCategory,
    LongContentPolicy,
    RuntimeStepError,
    RuntimeStatus,
    RetryBudget,
    RetryPolicy,
    SqliteTaskStore,
)
from runtime.content import LongContentRecoveryExecutor
from runtime.engine import LightweightExecutionEngine
from storage_life import ai, runtime_bridge
from storage_life.runtime_domain_strategy import EMMC_FIELD_ORDER, StorageEmmcDomainStrategy
from tools.openai_mock.server import Behavior, MockState, Scenario, create_server


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "array",
                "minItems": 37,
                "maxItems": 37,
                "items": {"type": "object", "properties": {"field_key": {"type": "string", "enum": list(EMMC_FIELD_ORDER)}}},
            }
        },
        "required": ["fields"],
    }


def _bundle():
    descriptor = StorageEmmcDomainStrategy.descriptor(
        source_ref={
            "source_id": "emmc-datasheet-1",
            "source_type": "STORAGE_DATASHEET",
            "content_hash": "source-hash",
            "fingerprint": "source-fingerprint",
        },
        source_text="SOURCE emmc-datasheet-1 PAGE 1\nFrozen datasheet text.",
    )
    bundle = StorageEmmcDomainStrategy.to_runtime_bundle(descriptor)
    bundle.shared_context["storage_provider_request"] = {
        "instructions": "Extract exactly the requested target fields.",
        "provider_payload": {
            "device_type": "eMMC",
            "vendor_hint": "Acme",
            "product_family_hint": "X",
            "primary_source_id": "emmc-datasheet-1",
            "pages": [{"source_id": "emmc-datasheet-1", "page": 1, "text": "Frozen datasheet text."}],
            "page_text": "SOURCE emmc-datasheet-1 PAGE 1\nFrozen datasheet text.",
            "target_fields": list(EMMC_FIELD_ORDER),
        },
        "schema": _schema(),
    }
    return bundle


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
    store = SqliteTaskStore(tmp_path / "storage-emmc-runtime.db")
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
        agent_id="storage.emmc.parameter_extract",
        provider_handler=provider,
        execution_policy=executor.step_execution_policy,
    )
    return executor, store


class _ResponseSequenceState(MockState):
    def __init__(self, payloads):
        super().__init__()
        self.payloads = list(payloads)
        self.call_no = 0

    def register_call(self, key, record):
        self.call_no = super().register_call(key, record)
        return self.call_no

    def scenario(self, key):
        index = min(max(self.call_no - 1, 0), len(self.payloads) - 1)
        return Scenario(key=key, payload=self.payloads[index], behavior=Behavior())


def test_t01_frozen_37_fields_and_six_atomic_groups_form_runtime_chunks(tmp_path):
    seen = []

    def provider(request, _context):
        payload = request["provider_payload"]
        fields = payload["target_fields"]
        schema = request["schema"]
        assert schema["properties"]["fields"]["items"]["properties"]["field_key"]["enum"] == fields
        assert schema["properties"]["fields"]["minItems"] == len(fields)
        assert schema["properties"]["fields"]["maxItems"] == len(fields)
        assert payload["pages"] and payload["page_text"]
        seen.append(fields)
        return runtime_bridge._validate_emmc_chunk_result(
            {"fields": [_fact(key) for key in fields]},
            request,
        )

    executor, _store = _executor(tmp_path, provider)
    outcome = executor.execute(
        _bundle(),
        request_id="storage-emmc-binding-plan",
        policy=LongContentPolicy(max_units_per_chunk=8, max_payload_chars=8000),
        strategy_ref=StorageEmmcDomainStrategy.strategy_ref,
    )

    expected_groups = [list(fields) for _name, fields in StorageEmmcDomainStrategy.atomic_groups]
    assert seen == expected_groups
    assert all(1 <= len(group) <= 8 for group in seen)
    assert outcome.status == RuntimeStatus.COMPLETED
    assert outcome.business_consumable
    assert [item["field_key"] for item in outcome.merge.data["fields"]] == list(EMMC_FIELD_ORDER)


def test_t02_provider_execution_is_runtime_owned_one_call_per_completed_chunk(tmp_path):
    calls = Counter()

    def provider(request, context):
        fields = request["provider_payload"]["target_fields"]
        calls[tuple(fields)] += 1
        assert len(context["long_content"]["unit_ids"]) == len(fields)
        return {"fields": [_fact(key) for key in fields]}

    executor, _store = _executor(tmp_path, provider)
    outcome = executor.execute(
        _bundle(),
        request_id="storage-emmc-binding-provider-owner",
        strategy_ref=StorageEmmcDomainStrategy.strategy_ref,
    )

    assert outcome.provider_calls == 6
    assert len(calls) == 6
    assert all(count == 1 for count in calls.values())


def test_t02b_configured_runtime_bridge_executes_six_provider_chunks(tmp_path, monkeypatch):
    outputs = [
        json.dumps({"fields": [_fact(key) for key in fields]})
        for _name, fields in StorageEmmcDomainStrategy.atomic_groups
    ]
    server = create_server("127.0.0.1", 0, state=_ResponseSequenceState(outputs))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    runtime_bridge.reset_for_tests()
    try:
        host, port = server.server_address
        model_config = tmp_path / "model.yaml"
        model_config.write_text(
            f"""active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key_env: STORAGE_AGENT_API_KEY
    model: mock-gpt
    temperature: 0
    max_tokens: 8192
""",
            encoding="utf-8",
        )
        runtime_root = Path(__file__).resolve().parents[3]
        monkeypatch.setenv("UNIFIED_AGENT_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("STORAGE_LIFE_ALLOW_UNPINNED_RUNTIME", "1")
        monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
        monkeypatch.setenv("STORAGE_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("STORAGE_AGENT_API_KEY", "mock-secret")
        monkeypatch.setenv("STORAGE_LIFE_RUNTIME_DB", str(tmp_path / "configured-runtime.sqlite3"))

        schema, _ = ai._single_pass_schema("eMMC")
        request_payload = {
            "device_type": "eMMC",
            "vendor_hint": "Acme",
            "product_family_hint": "X",
            "primary_source_id": "emmc-datasheet-1",
            "pages": [{"source_id": "emmc-datasheet-1", "page": 1, "text": "Frozen datasheet text."}],
            "page_text": "SOURCE emmc-datasheet-1 PAGE 1\nFrozen datasheet text.",
        }
        result = runtime_bridge.call_parameter_extract(
            "Extract each requested eMMC field exactly once.",
            request_payload,
            schema,
        )
        repeated = runtime_bridge.call_parameter_extract(
            "Extract each requested eMMC field exactly once.", request_payload, schema
        )

        assert [item["field_key"] for item in result["fields"]] == list(EMMC_FIELD_ORDER)
        assert repeated == result
        assert runtime_bridge.last_executions()[-1]["runtime_long_content"] is True
        assert len(server.state.requests()) == 6
        requests = [row["body"]["messages"][1]["content"] for row in server.state.requests()]
        payloads = [json.loads(item) for item in requests]
        groups = [item["provider_payload"]["target_fields"] for item in payloads]
        assert groups == [list(fields) for _name, fields in StorageEmmcDomainStrategy.atomic_groups]
        assert all(len(item["schema"]["properties"]["fields"]["items"]["properties"]["field_key"]["enum"]) <= 8 for item in payloads)
    finally:
        runtime_bridge.reset_for_tests()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_t03_storage_merge_keeps_field_order_evidence_and_semantic_conflicts():
    left = SimpleNamespace(
        partial_id="partial-1",
        data={"fields": [{**_fact("manufacturer"), "status": "found", "value": "Acme", "evidence": [{"source_id": "doc", "page": 1}]}]},
    )
    right = SimpleNamespace(
        partial_id="partial-2",
        data={"fields": [{**_fact("manufacturer"), "status": "found", "value": "Other", "evidence": [{"source_id": "doc", "page": 2}]}]},
    )

    merged = runtime_bridge._StorageEmmcResultMerger().merge([left, right], SimpleNamespace(merge_key="merge-1"))

    assert merged.data["fields"][0]["field_key"] == "manufacturer"
    assert merged.data["fields"][0]["status"] == "conflict"
    assert [item["page"] for item in merged.data["fields"][0]["evidence"]] == [1, 2]
    assert merged.data["merge_conflicts"][0]["type"] == "semantic_conflict"

    downstream_review = {
        "fields": [{**_fact(key), "status": "found", "value": "x"} if key == "manufacturer" else _fact(key) for key in EMMC_FIELD_ORDER],
        "missing_field_keys": [],
        "merge_conflicts": [],
    }
    assert runtime_bridge._emmc_business_gate(
        SimpleNamespace(data=downstream_review), [SimpleNamespace(complete=True)], None, None
    ) is True

def test_t04_partial_run_does_not_fabricate_later_groups(tmp_path):
    calls = []
    failed = {"value": False}

    def provider(request, _context):
        fields = request["provider_payload"]["target_fields"]
        calls.append(tuple(fields))
        if len(calls) == 4 and not failed["value"]:
            failed["value"] = True
            raise RuntimeStepError(
                "provider chunk failed",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )
        return {"fields": [_fact(key) for key in fields]}

    executor, store = _executor(tmp_path, provider)
    outcome = executor.execute(
        _bundle(),
        request_id="storage-emmc-binding-partial",
        strategy_ref=StorageEmmcDomainStrategy.strategy_ref,
    )

    assert outcome.status == RuntimeStatus.PARTIAL, (outcome.runtime_status, outcome.runtime_error)
    assert len(outcome.committed_partials) == 3
    assert outcome.merge is None
    assert len(store.list_partial_results(outcome.task_id)) == 3
    assert calls == [tuple(group) for _name, group in StorageEmmcDomainStrategy.atomic_groups[:4]]


def test_t05_resume_reuses_first_three_commits_and_runs_only_unfinished_groups(tmp_path):
    calls = []
    fail_group_four_once = {"value": True}

    def provider(request, _context):
        fields = request["provider_payload"]["target_fields"]
        calls.append(tuple(fields))
        if len(calls) == 4 and fail_group_four_once["value"]:
            fail_group_four_once["value"] = False
            raise RuntimeStepError(
                "temporary provider failure",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )
        return {"fields": [_fact(key) for key in fields]}

    executor, _store = _executor(tmp_path, provider)
    first = executor.execute(
        _bundle(),
        request_id="storage-emmc-binding-resume",
        strategy_ref=StorageEmmcDomainStrategy.strategy_ref,
    )
    assert first.status == RuntimeStatus.PARTIAL, (first.runtime_status, first.runtime_error, calls)
    resumed = executor.resume(first.task_id)
    assert resumed.status == RuntimeStatus.COMPLETED, (calls, resumed.runtime_error, resumed.gate, resumed.coverages)
    assert resumed.business_consumable
    expected = [tuple(fields) for _name, fields in StorageEmmcDomainStrategy.atomic_groups]
    assert calls == expected[:4] + expected[3:]
    assert resumed.provider_calls == 7
    assert len(resumed.committed_partials) == 6


def test_t06_only_full_emmc_contract_uses_long_content_and_other_routes_stay_unchanged(monkeypatch):
    calls = []

    def invoke(agent_id, instructions, payload, schema):
        calls.append((agent_id, instructions, payload, schema))
        return {"legacy": True}

    monkeypatch.setattr(runtime_bridge, "_invoke_json", invoke)
    result = runtime_bridge.call_parameter_extract(
        "instruction",
        {"device_type": "NAND Flash", "pages": []},
        _schema(),
    )
    emmc_handoff_result = runtime_bridge.call_parameter_extract(
        "instruction",
        {"device_type": "eMMC", "operation": "secondary_structured_extraction", "previous_provider_text": "source text"},
        _schema(),
    )

    assert result == {"legacy": True}
    assert emmc_handoff_result == {"legacy": True}
    assert len(calls) == 2
    assert all(call[0] == runtime_bridge.EMMC_PARAMETER_AGENT_ID for call in calls)
    assert runtime_bridge.route_for_device_type("NOR Flash")["agent_id"] != runtime_bridge.EMMC_PARAMETER_AGENT_ID
    assert runtime_bridge.route_for_device_type("NAND Flash")["agent_id"] != runtime_bridge.EMMC_PARAMETER_AGENT_ID
    assert runtime_bridge.route_for_device_type("SSD")["agent_id"] != runtime_bridge.EMMC_PARAMETER_AGENT_ID
    assert isinstance(runtime_bridge.last_executions(), list)
