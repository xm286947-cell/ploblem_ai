from __future__ import annotations

import json

from runtime import EvidenceLocator, EvidenceReference, SourceRef, SqliteTaskStore
from runtime.engine import LightweightExecutionEngine

from storage_e2e import JsonTruncationAwareStorageAdapter, ProviderResponse


def _source():
    return SourceRef(
        source_id="emmc-datasheet-1",
        source_type="DATASHEET",
        revision="1",
        content_hash="sha256-demo-v1",
        fingerprint="emmc-demo-v1",
        uri="file://fixtures/emmc_demo.pdf",
    )


def _golden_json():
    src = _source()
    evidence = EvidenceReference(
        evidence_id="ev-emmc-life-1",
        source=src,
        locator=EvidenceLocator(
            type="PAGE",
            value={"page": 12, "table": "Device Life Time"},
        ),
        excerpt="Device Life Time: 3000 cycles",
    ).model_dump(mode="json")
    return [
        {
            "field_id": "pe_cycle",
            "status": "FOUND",
            "normalized_value": 3000,
            "unit": "cycles",
            "evidence": [evidence],
            "review_reason": "datasheet explicit value",
        },
        {
            "field_id": "health_status_observable",
            "status": "FOUND",
            "normalized_value": True,
            "unit": None,
            "evidence": [
                EvidenceReference(
                    evidence_id="ev-emmc-health-1",
                    source=src,
                    locator=EvidenceLocator(
                        type="PAGE",
                        value={"page": 18, "table": "EXT_CSD"},
                    ),
                    excerpt="DEVICE_LIFE_TIME_EST_TYP_A/B and PRE_EOL_INFO",
                ).model_dump(mode="json")
            ],
            "review_reason": "EXT_CSD health fields declared",
        },
    ]


def test_e2e01_first_response_truncated_then_runtime_validation_retry_completes(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = []
    golden = _golden_json()

    def provider(payload, context):
        seq = context["runtime"]["provider_call_seq"]
        calls.append(seq)
        if seq == 1:
            return ProviderResponse(
                text='[{"field_id":"pe_cycle","status":"FOUND",',
                finish_reason="length",
            )
        return ProviderResponse(
            text=json.dumps(golden, ensure_ascii=False),
            finish_reason="stop",
        )

    adapter = JsonTruncationAwareStorageAdapter(runtime, provider)
    outcome = adapter.execute(
        {"device_type": "eMMC", "parameter_scope": "lifetime"},
        request_id="storage-e2e01-truncation-recovery",
        golden=golden,
        max_provider_calls=3,
    )

    assert outcome.runtime_result.status.value == "COMPLETED"
    assert calls == [1, 2]
    assert outcome.runtime_result.execution.provider_calls == 2
    assert outcome.reviewed_specification is not None
    assert outcome.golden_report is not None
    assert outcome.golden_report.passed is True
    assert outcome.golden_report.diffs == []

    task = store.get_task(outcome.runtime_result.task_id)
    committed_keys = store.list_committed_execution_keys(task.task_id)
    assert len(committed_keys) == 1
    committed = store.get_committed_execution(committed_keys[0])
    assert committed is not None
    assert committed.result_data == golden


def test_e2e01_normal_response_only_one_provider_call(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    golden = _golden_json()
    calls = []

    def provider(payload, context):
        calls.append(context["runtime"]["provider_call_seq"])
        return ProviderResponse(
            text=json.dumps(golden, ensure_ascii=False),
            finish_reason="stop",
        )

    adapter = JsonTruncationAwareStorageAdapter(runtime, provider)
    outcome = adapter.execute(
        {"device_type": "eMMC", "parameter_scope": "lifetime"},
        request_id="storage-e2e01-normal",
        golden=golden,
        max_provider_calls=3,
    )

    assert outcome.runtime_result.status.value == "COMPLETED"
    assert calls == [1]
    assert outcome.runtime_result.execution.provider_calls == 1
    assert outcome.golden_report.passed is True


def test_e2e01_exhausted_truncation_never_reaches_reviewed_specification(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)

    def provider(payload, context):
        return ProviderResponse(
            text='[{"field_id":"pe_cycle"',
            finish_reason="length",
        )

    adapter = JsonTruncationAwareStorageAdapter(runtime, provider)
    outcome = adapter.execute(
        {"device_type": "eMMC", "parameter_scope": "lifetime"},
        request_id="storage-e2e01-always-truncated",
        golden=_golden_json(),
        max_provider_calls=2,
    )

    assert outcome.runtime_result.status.value != "COMPLETED"
    assert outcome.runtime_result.execution.provider_calls == 2
    assert outcome.reviewed_specification is None
    assert outcome.golden_report is None
