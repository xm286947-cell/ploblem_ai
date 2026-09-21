from __future__ import annotations

import os
from pathlib import Path

import pytest

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.adapters import StorageFieldResult, StorageGoldenFieldComparator
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime


ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = "config/runtime/agents/storage.emmc.parameter_extract.yaml"
MODEL_CONFIG = "config/runtime/model.yaml"

GOLDEN = [
    {
        "field_id": "pe_cycle",
        "status": "FOUND",
        "normalized_value": 3000,
        "unit": "cycles",
        "evidence": [],
        "review_reason": None,
    }
]


def _require_real_provider() -> None:
    enabled = os.environ.get("STORAGE_REAL_E2E", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        pytest.skip("STORAGE-REAL-E2E-01 opt-in disabled")

    missing = [
        name
        for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip("NOT_RUN_NO_SECRET_OR_ENDPOINT: " + ",".join(missing))


def _loader() -> AgentConfigLoader:
    return AgentConfigLoader(
        root=ROOT,
        model_profiles=ROOT / MODEL_CONFIG,
        schemas={"StorageFieldResult": StorageFieldResult},
        content_strategies={
            "storage_linked_fields@1": {
                "version": "1",
                "kind": "storage_linked_fields",
            }
        },
        completeness_gates={
            "storage_parameter_gate": {
                "version": "v1",
                "kind": "storage_parameter_gate",
            }
        },
        environ=os.environ,
    )


def _raw_runtime_bytes(tmp_path: Path) -> bytes:
    chunks = []
    for path in sorted(tmp_path.glob("runtime.db*")):
        if path.is_file():
            chunks.append(path.read_bytes())
    return b"".join(chunks)


def test_storage_real_provider_e2e01_yaml_runtime_provider_schema_and_golden(tmp_path):
    _require_real_provider()

    store = SqliteTaskStore(tmp_path / "runtime.db")
    loader = _loader()
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    resolved = runtime.load_agent(AGENT_CONFIG)

    assert resolved.provider.profile_ref == "qwen_prod"
    assert resolved.provider.model == "qwen3.8-max"
    assert resolved.provider.base_url_env == "DASHSCOPE_BASE_URL"
    assert resolved.provider.api_key_env == "DASHSCOPE_API_KEY"
    assert os.environ["DASHSCOPE_API_KEY"] not in resolved.model_dump_json()

    result = runtime.invoke(
        AgentRequest(
            request_id="storage-real-e2e-01",
            agent_id="storage.emmc.parameter_extract",
            input={
                "device_type": "eMMC",
                "parameter_scope": "lifetime",
                "source_text": "The eMMC device life specification states pe_cycle = 3000 cycles.",
                "required_fields": ["pe_cycle"],
            },
        )
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert 1 <= result.execution.provider_calls <= 4

    actual = [StorageFieldResult.model_validate(item) for item in result.data]
    report = StorageGoldenFieldComparator().compare(
        GOLDEN,
        actual,
        accepted_differences={
            "pe_cycle.review_reason": "Provider wording is outside the smoke-gate contract."
        },
    )
    assert report.passed is True

    snapshot = store.get_execution_snapshot(result.execution.execution_snapshot_id)
    serialized_snapshot = snapshot.model_dump_json()
    secret = os.environ["DASHSCOPE_API_KEY"]

    assert secret not in serialized_snapshot
    assert secret not in result.model_dump_json()
    assert secret.encode("utf-8") not in _raw_runtime_bytes(tmp_path)
    assert "DASHSCOPE_API_KEY" in serialized_snapshot
