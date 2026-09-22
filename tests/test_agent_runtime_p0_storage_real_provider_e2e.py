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


def _model_config_path() -> Path:
    configured = os.environ.get("STORAGE_MODEL_CONFIG", "").strip()
    if not configured:
        return ROOT / MODEL_CONFIG

    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    if not path.is_file():
        pytest.fail(f"STORAGE_MODEL_CONFIG does not exist: {path}")
    return path


def _require_real_provider() -> None:
    enabled = os.environ.get("STORAGE_REAL_E2E", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        pytest.skip("STORAGE-REAL-E2E-01 opt-in disabled")

    if os.environ.get("STORAGE_MODEL_CONFIG", "").strip():
        _model_config_path()
        return

    missing = [
        name
        for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip("NOT_RUN_NO_SECRET_OR_ENDPOINT: " + ",".join(missing))


def _loader(
    *,
    model_config: Path | None = None,
    environ=None,
) -> AgentConfigLoader:
    return AgentConfigLoader(
        root=ROOT,
        model_profiles=model_config or _model_config_path(),
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
        environ=os.environ if environ is None else environ,
    )


def _raw_runtime_bytes(tmp_path: Path) -> bytes:
    chunks = []
    for path in sorted(tmp_path.glob("runtime.db*")):
        if path.is_file():
            chunks.append(path.read_bytes())
    return b"".join(chunks)


def test_storage_external_model_config_resolves_qwen_without_provider_env(
    tmp_path: Path,
) -> None:
    secret = "LOCAL_ONLY_TEST_SECRET"
    model_config = tmp_path / "model.local.yaml"
    model_config.write_text(
        f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: https://workspace.example/compatible-mode/v1
    api_key: {secret}
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192
""".strip(),
        encoding="utf-8",
    )

    loader = _loader(model_config=model_config, environ={})
    resolved = loader.load(AGENT_CONFIG)
    resolved_secret = loader.get_runtime_api_key(
        resolved.config_hash,
        api_key_env=resolved.provider.api_key_env,
    )

    assert resolved.provider.profile_ref == "qwen_prod"
    assert resolved.provider.model == "qwen3.8-max"
    assert resolved.provider.base_url == (
        "https://workspace.example/compatible-mode/v1"
    )
    assert resolved.provider.base_url_env is None
    assert resolved.provider.api_key_env is None
    assert resolved_secret == secret
    assert secret not in resolved.model_dump_json()


def test_storage_real_provider_e2e01_yaml_runtime_provider_schema_and_golden(tmp_path):
    _require_real_provider()

    store = SqliteTaskStore(tmp_path / "runtime.db")
    loader = _loader()
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    resolved = runtime.load_agent(AGENT_CONFIG)
    secret = loader.get_runtime_api_key(
        resolved.config_hash,
        api_key_env=resolved.provider.api_key_env,
    )

    assert resolved.provider.profile_ref == "qwen_prod"
    assert resolved.provider.model == "qwen3.8-max"
    assert resolved.provider.type == "openai_compatible"
    assert resolved.provider.base_url
    assert secret
    assert secret not in resolved.model_dump_json()

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

    run = store.list_runs(result.task_id)[0]
    step = store.list_step_runs(run.run_id)[0]
    attempts = store.list_attempts(step.step_run_id)
    completed_attempts = [
        attempt
        for attempt in attempts
        if attempt.status == RuntimeStatus.COMPLETED
    ]
    assert completed_attempts, "real Provider run must persist one completed attempt"
    provider_evidence = completed_attempts[-1].execution_metrics.get(
        "provider_evidence"
    )
    assert isinstance(provider_evidence, dict)
    assert provider_evidence["resolved_model"] == resolved.provider.model
    assert provider_evidence["model_ref"] == resolved.provider.profile_ref
    assert provider_evidence["config_hash"] == resolved.config_hash
    assert provider_evidence["agent_config_source"] == resolved.source_path
    assert provider_evidence["model_config_source"] == str(
        _model_config_path().resolve()
    )
    assert provider_evidence["resolved_max_tokens"] == (
        resolved.execution_policy.model_policy.get("max_tokens")
    )
    assert (
        provider_evidence["request_max_tokens"] != "NOT_SENT"
        or provider_evidence["request_max_completion_tokens"] != "NOT_SENT"
    )
    assert provider_evidence["raw_usage"] != "NOT_RETURNED"
    assert provider_evidence["raw_finish_reason"] != "NOT_RETURNED"
    assert provider_evidence["structured_output_capability"] in {
        "SUPPORTED_AND_REQUESTED",
        "SUPPORTED_NOT_REQUESTED",
        "UNSUPPORTED",
        "UNKNOWN",
    }
    assert provider_evidence["structured_output_request"] in {
        "NONE",
        "response_format/json_object",
        "response_format/json_schema",
    }
    assert provider_evidence["streaming"] is False
    assert provider_evidence["chunk_diagnostics"] == "NOT_APPLICABLE"

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

    assert secret not in serialized_snapshot
    assert secret not in result.model_dump_json()
    assert secret.encode("utf-8") not in _raw_runtime_bytes(tmp_path)
    if resolved.provider.api_key_env:
        assert resolved.provider.api_key_env in serialized_snapshot
