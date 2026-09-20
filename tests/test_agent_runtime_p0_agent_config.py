from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime import (
    AgentRequest,
    ErrorCategory,
    RuntimeStatus,
    RuntimeStepError,
    SqliteTaskStore,
)
from runtime.adapters import StorageFieldResult
from runtime.config import (
    AgentConfigLoader,
    ConfigReferenceNotFoundError,
    ConfigValidationError,
    ConfiguredAgentRuntime,
    SecretEnvNotFoundError,
)
from runtime.content import ContentStrategyRegistry
from runtime.contracts import ContentStrategyDefinition
from runtime.reliability import SimulatedCrash


SECRET = "super-secret-provider-key"


def _agent_yaml(
    *,
    model: str = "qwen3.8-max",
    max_tokens: int = 8192,
    provider_calls: int = 4,
    transport: int = 2,
    validation: int = 3,
    step: int = 1,
) -> str:
    return f"""
agent_id: storage.emmc.parameter_extract
version: v1
label: Storage eMMC Parameter Extract
provider_ref: qwen_prod
model: {model}

prompt:
  ref: prompts/emmc_parameter_extract.md
  version: v1

output_schema:
  ref: StorageFieldResult
  version: v1

execution:
  timeout_seconds: 120
  retry:
    transport_attempts: {transport}
    validation_attempts: {validation}
    step_attempts: {step}
  budget:
    max_provider_calls_per_step: {provider_calls}
  model:
    max_tokens: {max_tokens}
    temperature: 0

long_content:
  enabled: true
  strategy_ref: storage_linked_fields@1

completeness_gate:
  ref: storage_parameter_gate
  version: v1

metadata:
  business_domain: STORAGE
  acceptance_case: E2E-01
""".strip()


def _write_fixture(
    root: Path,
    *,
    agent_yaml: str | None = None,
) -> Path:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "emmc_parameter_extract.md").write_text(
        "Return strict StorageFieldResult JSON.",
        encoding="utf-8",
    )
    (root / "providers.yaml").write_text(
        """
providers:
  qwen_prod:
    type: openai_compatible
    mode: env
    base_url_env: DASHSCOPE_BASE_URL
    api_key_env: DASHSCOPE_API_KEY
""".strip(),
        encoding="utf-8",
    )
    config_path = root / "storage.emmc.parameter_extract.yaml"
    config_path.write_text(
        agent_yaml or _agent_yaml(),
        encoding="utf-8",
    )
    return config_path


def _strategy_registry() -> ContentStrategyRegistry:
    registry = ContentStrategyRegistry()
    registry.register(
        ContentStrategyDefinition(
            strategy_id="storage_linked_fields",
            version="1",
            projector_ref="storage.projector",
            planner_ref="runtime.content.planner",
        )
    )
    return registry


def _loader(
    root: Path,
    *,
    environ: dict[str, str] | None = None,
) -> AgentConfigLoader:
    return AgentConfigLoader(
        root=root,
        provider_profiles="providers.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        content_strategies=_strategy_registry(),
        completeness_gates={
            "storage_parameter_gate": lambda value: value,
        },
        environ=environ
        or {
            "DASHSCOPE_BASE_URL": "http://provider.invalid/v1",
            "DASHSCOPE_API_KEY": SECRET,
        },
    )


def _snapshot(store: SqliteTaskStore, result):
    return store.get_execution_snapshot(
        result.execution.execution_snapshot_id
    )


def test_ac01_yaml_maps_to_agent_definition(tmp_path):
    config_path = _write_fixture(tmp_path)
    resolved = _loader(tmp_path).load(config_path)

    definition = resolved.definition
    assert definition.agent_id == "storage.emmc.parameter_extract"
    assert definition.provider == "openai_compatible"
    assert definition.model == "qwen3.8-max"
    assert definition.prompt_ref == "prompts/emmc_parameter_extract.md"
    assert definition.output_schema == "StorageFieldResult"
    assert definition.content_strategy_ref == "storage_linked_fields@1"
    assert definition.version == "v1"
    assert definition.definition_hash == resolved.config_hash


def test_ac02_yaml_maps_to_execution_policy(tmp_path):
    config_path = _write_fixture(tmp_path)
    policy = _loader(tmp_path).load(config_path).execution_policy

    assert policy.timeout_seconds == 120
    assert policy.transport_retry.max_attempts == 2
    assert policy.validation_retry.max_attempts == 3
    assert policy.step_retry.max_attempts == 1
    assert policy.retry_budget.max_provider_calls_per_step == 4
    assert policy.retry_budget.max_transport_attempts_per_model_call == 2
    assert policy.retry_budget.max_validation_cycles_per_step_attempt == 3


def test_ac03_provider_profile_reference_resolves(tmp_path):
    config_path = _write_fixture(tmp_path)
    provider = _loader(tmp_path).load(config_path).provider

    assert provider.profile_ref == "qwen_prod"
    assert provider.type == "openai_compatible"
    assert provider.model == "qwen3.8-max"
    assert provider.base_url_env == "DASHSCOPE_BASE_URL"
    assert provider.api_key_env == "DASHSCOPE_API_KEY"


def test_ac04_environment_variables_resolve_without_retaining_secret(tmp_path):
    config_path = _write_fixture(tmp_path)
    resolved = _loader(tmp_path).load(config_path)

    assert resolved.provider.base_url == "http://provider.invalid/v1"
    dumped = resolved.model_dump_json()
    assert SECRET not in dumped
    assert "DASHSCOPE_API_KEY" in dumped


def test_ac05_secret_does_not_enter_execution_snapshot(tmp_path):
    config_path = _write_fixture(tmp_path)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=_loader(tmp_path),
    )
    runtime.register_agent_from_config(
        config_path,
        lambda payload, _context: payload,
    )

    result = runtime.invoke(
        AgentRequest(
            request_id="ac05",
            agent_id="storage.emmc.parameter_extract",
            input={"ok": True},
        )
    )
    snapshot = _snapshot(store, result)
    serialized = snapshot.model_dump_json()

    assert result.status == RuntimeStatus.COMPLETED
    assert SECRET not in serialized
    assert "DASHSCOPE_API_KEY" in serialized
    assert snapshot.agent_definition["metadata"]["agent_config_hash"]


def test_ac06_secret_does_not_enter_runtime_error_or_attempt(tmp_path):
    config_path = _write_fixture(tmp_path)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=_loader(tmp_path),
    )

    def leaking_provider(_payload, _context):
        raise RuntimeStepError(
            f"provider rejected key {SECRET}",
            code="PROVIDER_FAILURE",
            category=ErrorCategory.EXECUTION,
            retryable=False,
            details={"provider_message": f"bad credential {SECRET}"},
        )

    runtime.register_agent_from_config(config_path, leaking_provider)
    result = runtime.invoke(
        AgentRequest(
            request_id="ac06",
            agent_id="storage.emmc.parameter_extract",
            input={},
        )
    )

    assert result.status == RuntimeStatus.FAILED
    error_json = result.error.model_dump_json()
    assert SECRET not in error_json
    assert "[REDACTED]" in error_json

    run = store.list_runs(result.task_id)[0]
    step_run = store.list_step_runs(run.run_id)[0]
    attempt_json = json.dumps(
        [
            item.model_dump(mode="json")
            for item in store.list_attempts(step_run.step_run_id)
        ],
        ensure_ascii=False,
        default=str,
    )
    assert SECRET not in attempt_json


def test_ac07_retry_policy_and_budget_map_consistently(tmp_path):
    config_path = _write_fixture(
        tmp_path,
        agent_yaml=_agent_yaml(
            transport=2,
            validation=3,
            step=2,
            provider_calls=4,
        ),
    )
    policy = _loader(tmp_path).load(config_path).execution_policy

    assert policy.transport_retry.max_attempts == 2
    assert policy.validation_retry.max_attempts == 3
    assert policy.step_retry.max_attempts == 2
    assert policy.retry_budget.max_provider_calls_per_step == 4
    assert policy.retry_budget.max_step_attempts == 2
    assert policy.retry_budget.max_validation_cycles_per_step_attempt == 3
    assert policy.retry_budget.max_transport_attempts_per_model_call == 2


def test_ac08_model_parameters_enter_model_policy(tmp_path):
    config_path = _write_fixture(tmp_path)
    policy = _loader(tmp_path).load(config_path).execution_policy

    assert policy.model_policy["max_tokens"] == 8192
    assert policy.model_policy["temperature"] == 0
    assert policy.model_policy["sdk_retry"] == 0


def test_ac09_prompt_schema_strategy_and_gate_are_frozen_into_snapshot(tmp_path):
    config_path = _write_fixture(tmp_path)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=_loader(tmp_path),
    )
    runtime.register_agent_from_config(
        config_path,
        lambda payload, _context: payload,
    )
    result = runtime.invoke(
        AgentRequest(
            request_id="ac09",
            agent_id="storage.emmc.parameter_extract",
            input={"ok": True},
        )
    )

    snapshot = _snapshot(store, result)
    assert snapshot.prompt_ref == "prompts/emmc_parameter_extract.md"
    assert snapshot.prompt_hash
    assert snapshot.output_schema_ref == "StorageFieldResult"
    assert snapshot.output_schema_version == "v1"
    assert snapshot.output_schema_hash
    assert snapshot.content_strategy_ref == "storage_linked_fields@1"
    assert snapshot.content_strategy_version == "1"
    assert snapshot.content_strategy_hash
    assert snapshot.completeness_gate_ref == "storage_parameter_gate"
    assert snapshot.completeness_gate_version == "v1"


def test_ac10_invalid_config_fails_before_provider_call(tmp_path):
    calls = {"n": 0}

    def provider(_payload, _context):
        calls["n"] += 1
        return {}

    bad_budget = _write_fixture(
        tmp_path,
        agent_yaml=_agent_yaml(
            validation=3,
            provider_calls=2,
        ),
    )
    with pytest.raises(ConfigValidationError) as exc:
        _loader(tmp_path).load(bad_budget)
    assert exc.value.code == "CONFIG_VALIDATION_FAILED"
    assert calls["n"] == 0

    _write_fixture(tmp_path)
    missing_env_loader = _loader(
        tmp_path,
        environ={
            "DASHSCOPE_BASE_URL": "http://provider.invalid/v1",
        },
    )
    with pytest.raises(SecretEnvNotFoundError) as exc:
        missing_env_loader.load(
            tmp_path / "storage.emmc.parameter_extract.yaml"
        )
    assert exc.value.code == "SECRET_ENV_NOT_FOUND"
    assert calls["n"] == 0

    missing_schema_loader = AgentConfigLoader(
        root=tmp_path,
        provider_profiles="providers.yaml",
        schemas={},
        content_strategies=_strategy_registry(),
        completeness_gates={
            "storage_parameter_gate": lambda value: value,
        },
        environ={
            "DASHSCOPE_BASE_URL": "http://provider.invalid/v1",
            "DASHSCOPE_API_KEY": SECRET,
        },
    )
    with pytest.raises(ConfigReferenceNotFoundError) as exc:
        missing_schema_loader.load(
            tmp_path / "storage.emmc.parameter_extract.yaml"
        )
    assert exc.value.code == "CONFIG_REFERENCE_NOT_FOUND"
    assert calls["n"] == 0


def test_ac10_literal_api_key_in_yaml_is_rejected(tmp_path):
    config_path = _write_fixture(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\nmetadata:\n  api_key: sk-do-not-commit\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError) as exc:
        _loader(tmp_path).load(config_path)
    assert exc.value.code == "CONFIG_VALIDATION_FAILED"


def test_ac11_runtime_managed_sdk_retry_is_zero(tmp_path):
    config_path = _write_fixture(tmp_path)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=_loader(tmp_path),
    )
    observed = {}

    def handler(payload, context):
        observed["provider"] = context["runtime"]["provider_config"]
        observed["guard"] = context["runtime"]["sdk_retry_policy"]
        return payload

    runtime.register_agent_from_config(config_path, handler)
    result = runtime.invoke(
        AgentRequest(
            request_id="ac11",
            agent_id="storage.emmc.parameter_extract",
            input={"ok": True},
        )
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert observed["provider"]["sdk_retry"] == 0
    assert observed["guard"]["implicit_retry_enabled"] is False
    assert (
        observed["guard"]["adapter_must_report_actual_provider_requests"]
        is True
    )


def test_ac12_storage_emmc_agent_registers_entirely_from_config(tmp_path):
    config_path = _write_fixture(tmp_path)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=_loader(tmp_path),
    )

    resolved = runtime.load_agent(
        config_path,
        lambda payload, _context: {
            "field_id": payload["field_id"],
            "status": "FOUND",
            "normalized_value": payload["value"],
        },
    )
    result = runtime.invoke(
        AgentRequest(
            request_id="ac12",
            agent_id="storage.emmc.parameter_extract",
            input={"field_id": "emmc.life", "value": 100},
        )
    )

    assert resolved.definition.agent_id == "storage.emmc.parameter_extract"
    assert result.status == RuntimeStatus.COMPLETED
    assert result.data["field_id"] == "emmc.life"
    snapshot = _snapshot(store, result)
    assert snapshot.execution_policy.validation_retry.max_attempts == 3
    assert snapshot.model_policy["max_tokens"] == 8192


def test_ac13_new_task_uses_new_snapshot_after_config_change(tmp_path):
    config_path = _write_fixture(tmp_path)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=_loader(tmp_path),
    )
    handler = lambda payload, _context: payload

    runtime.load_agent(config_path, handler)
    first = runtime.invoke(
        AgentRequest(
            request_id="ac13-v1",
            agent_id="storage.emmc.parameter_extract",
            input={"value": 1},
        )
    )
    first_snapshot = _snapshot(store, first)

    config_path.write_text(
        _agent_yaml(model="qwen-next", max_tokens=4096),
        encoding="utf-8",
    )
    runtime.load_agent(config_path)
    second = runtime.invoke(
        AgentRequest(
            request_id="ac13-v2",
            agent_id="storage.emmc.parameter_extract",
            input={"value": 2},
        )
    )
    second_snapshot = _snapshot(store, second)

    assert first_snapshot.fingerprint != second_snapshot.fingerprint
    assert first_snapshot.agent_definition["model"] == "qwen3.8-max"
    assert second_snapshot.agent_definition["model"] == "qwen-next"
    assert first_snapshot.model_policy["max_tokens"] == 8192
    assert second_snapshot.model_policy["max_tokens"] == 4096


def test_ac14_resume_keeps_original_snapshot_after_yaml_change(tmp_path):
    config_path = _write_fixture(tmp_path)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    crash_once = {"enabled": True}
    observed = []

    def fault(point):
        if (
            crash_once["enabled"]
            and point == "after_provider_return_before_commit"
        ):
            raise SimulatedCrash(point)

    def handler(payload, context):
        observed.append(
            {
                "model": context["runtime"]["agent_definition"]["model"],
                "max_tokens": context["runtime"]["model_policy"]["max_tokens"],
                "provider_model": context["runtime"]["provider_config"]["model"],
                "config_hash": context["runtime"]["agent_config_hash"],
            }
        )
        return payload

    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=_loader(tmp_path),
        fault_injector=fault,
    )
    runtime.load_agent(config_path, handler)

    with pytest.raises(SimulatedCrash):
        runtime.invoke(
            AgentRequest(
                request_id="ac14",
                agent_id="storage.emmc.parameter_extract",
                input={"value": 1},
            )
        )

    task = store.get_task_by_request_id("ac14")
    assert task is not None
    original_snapshot = store.get_execution_snapshot(
        task.execution_snapshot_id
    )
    original_hash = original_snapshot.agent_definition["metadata"][
        "agent_config_hash"
    ]

    config_path.write_text(
        _agent_yaml(model="qwen-changed", max_tokens=2048),
        encoding="utf-8",
    )
    runtime.load_agent(config_path)
    assert runtime.get_agent_config(
        "storage.emmc.parameter_extract"
    ).definition.model == "qwen-changed"

    crash_once["enabled"] = False
    runtime.fault_injector = None
    resumed = runtime.resume(task.task_id)

    assert resumed.status == RuntimeStatus.COMPLETED
    assert len(observed) == 2
    assert observed[1]["model"] == "qwen3.8-max"
    assert observed[1]["provider_model"] == "qwen3.8-max"
    assert observed[1]["max_tokens"] == 8192
    assert observed[1]["config_hash"] == original_hash

    task_after = store.get_task(task.task_id)
    assert task_after.execution_snapshot_id == task.execution_snapshot_id


@pytest.mark.parametrize(
    ("provider_ref", "base_url", "api_key"),
    [
        ("qwen_test", "http://qwen.test/v1", "qwen-direct-key"),
        ("zhipu_test", "http://zhipu.test/v1", "zhipu-direct-key"),
        ("deepseek_test", "http://deepseek.test/v1", "deepseek-direct-key"),
    ],
)
def test_provider_profiles_support_direct_test_mode_without_snapshot_secret(
    tmp_path,
    provider_ref,
    base_url,
    api_key,
):
    config_path = _write_fixture(tmp_path)
    (tmp_path / "providers.yaml").write_text(
        f"""
providers:
  {provider_ref}:
    type: openai_compatible
    mode: direct
    base_url: {base_url}
    api_key: {api_key}
""".strip(),
        encoding="utf-8",
    )
    config_path.write_text(
        _agent_yaml().replace(
            "provider_ref: qwen_prod",
            f"provider_ref: {provider_ref}",
        ),
        encoding="utf-8",
    )

    loader = AgentConfigLoader(
        root=tmp_path,
        provider_profiles="providers.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        content_strategies=_strategy_registry(),
        completeness_gates={
            "storage_parameter_gate": lambda value: value,
        },
        environ={},
    )
    resolved = loader.load(config_path)

    assert resolved.provider.mode == "direct"
    assert resolved.provider.base_url == base_url
    assert resolved.provider.api_key_env is None
    assert api_key not in resolved.model_dump_json()

    observed = {}
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)

    def handler(payload, context):
        observed.update(context["runtime"]["provider_config"])
        return payload

    runtime.load_agent(config_path, handler)
    result = runtime.invoke(
        AgentRequest(
            request_id=f"direct-{provider_ref}",
            agent_id="storage.emmc.parameter_extract",
            input={"ok": True},
        )
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert observed["mode"] == "direct"
    assert observed["base_url"] == base_url
    assert observed["api_key"] == api_key
    assert observed["sdk_retry"] == 0
    snapshot = _snapshot(store, result)
    assert api_key not in snapshot.model_dump_json()


@pytest.mark.parametrize(
    ("provider_ref", "base_env", "key_env"),
    [
        ("qwen_prod", "DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY"),
        ("zhipu_prod", "ZHIPU_BASE_URL", "ZHIPU_API_KEY"),
        ("deepseek_prod", "DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY"),
    ],
)
def test_provider_profiles_support_env_production_mode(
    tmp_path,
    provider_ref,
    base_env,
    key_env,
):
    config_path = _write_fixture(tmp_path)
    (tmp_path / "providers.yaml").write_text(
        f"""
providers:
  {provider_ref}:
    type: openai_compatible
    mode: env
    base_url_env: {base_env}
    api_key_env: {key_env}
""".strip(),
        encoding="utf-8",
    )
    config_path.write_text(
        _agent_yaml().replace(
            "provider_ref: qwen_prod",
            f"provider_ref: {provider_ref}",
        ),
        encoding="utf-8",
    )
    secret = f"{provider_ref}-env-key"
    loader = AgentConfigLoader(
        root=tmp_path,
        provider_profiles="providers.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        content_strategies=_strategy_registry(),
        completeness_gates={
            "storage_parameter_gate": lambda value: value,
        },
        environ={
            base_env: f"http://{provider_ref}.prod/v1",
            key_env: secret,
        },
    )
    resolved = loader.load(config_path)

    assert resolved.provider.mode == "env"
    assert resolved.provider.base_url_env == base_env
    assert resolved.provider.api_key_env == key_env
    assert secret not in resolved.model_dump_json()


def test_direct_provider_secret_is_redacted_from_runtime_error(tmp_path):
    direct_secret = "direct-provider-secret"
    config_path = _write_fixture(tmp_path)
    (tmp_path / "providers.yaml").write_text(
        f"""
providers:
  qwen_test:
    type: openai_compatible
    mode: direct
    base_url: http://qwen.test/v1
    api_key: {direct_secret}
""".strip(),
        encoding="utf-8",
    )
    config_path.write_text(
        _agent_yaml().replace(
            "provider_ref: qwen_prod",
            "provider_ref: qwen_test",
        ),
        encoding="utf-8",
    )
    loader = AgentConfigLoader(
        root=tmp_path,
        provider_profiles="providers.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        content_strategies=_strategy_registry(),
        completeness_gates={
            "storage_parameter_gate": lambda value: value,
        },
        environ={},
    )
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)

    def handler(_payload, _context):
        raise RuntimeStepError(
            f"provider rejected {direct_secret}",
            code="PROVIDER_FAILURE",
            category=ErrorCategory.EXECUTION,
            retryable=False,
            details={"raw": direct_secret},
        )

    runtime.load_agent(config_path, handler)
    result = runtime.invoke(
        AgentRequest(
            request_id="direct-secret-redaction",
            agent_id="storage.emmc.parameter_extract",
            input={},
        )
    )

    assert result.status == RuntimeStatus.FAILED
    serialized = result.error.model_dump_json()
    assert direct_secret not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.parametrize(
    "model",
    [
        "qwen3-vl:8b-thinking-q4_K_M",
        "qwen3-vl:8b-thinking-local",
        "gemma4:e4b",
    ],
)
def test_ollama_local_supports_auth_none_and_model_switching(
    tmp_path,
    model,
):
    config_path = _write_fixture(tmp_path)
    (tmp_path / "providers.yaml").write_text(
        """
providers:
  ollama_local:
    type: openai_compatible
    mode: direct
    auth: none
    base_url: http://192.168.1.100:11434/v1
""".strip(),
        encoding="utf-8",
    )
    config_path.write_text(
        _agent_yaml(model=model).replace(
            "provider_ref: qwen_prod",
            "provider_ref: ollama_local",
        ),
        encoding="utf-8",
    )

    loader = AgentConfigLoader(
        root=tmp_path,
        provider_profiles="providers.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        content_strategies=_strategy_registry(),
        completeness_gates={
            "storage_parameter_gate": lambda value: value,
        },
        environ={},
    )
    resolved = loader.load(config_path)

    assert resolved.provider.mode == "direct"
    assert resolved.provider.auth == "none"
    assert resolved.provider.base_url == "http://192.168.1.100:11434/v1"
    assert resolved.provider.api_key_env is None
    assert resolved.provider.model == model

    observed = {}
    store = SqliteTaskStore(tmp_path / f"runtime-{model.replace(':', '-')}.db")
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)

    def handler(payload, context):
        observed.update(context["runtime"]["provider_config"])
        return payload

    runtime.load_agent(config_path, handler)
    result = runtime.invoke(
        AgentRequest(
            request_id=f"ollama-{model}",
            agent_id="storage.emmc.parameter_extract",
            input={"ok": True},
        )
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert observed["auth"] == "none"
    assert observed["api_key"] is None
    assert observed["model"] == model
    assert observed["sdk_retry"] == 0


def test_auth_none_rejects_api_key_configuration(tmp_path):
    config_path = _write_fixture(tmp_path)
    (tmp_path / "providers.yaml").write_text(
        """
providers:
  bad_ollama:
    type: openai_compatible
    mode: direct
    auth: none
    base_url: http://192.168.1.100:11434/v1
    api_key: should-not-be-here
""".strip(),
        encoding="utf-8",
    )
    config_path.write_text(
        _agent_yaml().replace(
            "provider_ref: qwen_prod",
            "provider_ref: bad_ollama",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError) as exc:
        AgentConfigLoader(
            root=tmp_path,
            provider_profiles="providers.yaml",
            schemas={"StorageFieldResult": StorageFieldResult},
            content_strategies=_strategy_registry(),
            completeness_gates={
                "storage_parameter_gate": lambda value: value,
            },
            environ={},
        )

    assert exc.value.code == "CONFIG_VALIDATION_FAILED"


def test_simple_model_yaml_is_the_public_configuration_path(tmp_path):
    (tmp_path / "prompts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompts" / "simple.md").write_text(
        "Return strict JSON.",
        encoding="utf-8",
    )
    (tmp_path / "model.yaml").write_text(
        """
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url_env: DASHSCOPE_BASE_URL
    api_key_env: DASHSCOPE_API_KEY
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192

  ollama_qwen:
    provider: openai_compatible
    base_url: http://192.168.1.100:11434/v1
    model: qwen3-vl:8b-thinking-q4_K_M
    temperature: 0
    max_tokens: 8192
""".strip(),
        encoding="utf-8",
    )
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        """
agent_id: demo.simple
version: v1
model_ref: ollama_qwen
prompt:
  ref: prompts/simple.md
  version: v1
output_schema:
  ref: StorageFieldResult
  version: v1
execution:
  retry:
    transport_attempts: 1
    validation_attempts: 1
    step_attempts: 1
  budget:
    max_provider_calls_per_step: 1
""".strip(),
        encoding="utf-8",
    )

    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        environ={
            "DASHSCOPE_BASE_URL": "https://example.invalid/v1",
            "DASHSCOPE_API_KEY": "env-key",
        },
    )
    resolved = loader.load(config_path)

    assert resolved.definition.model == "qwen3-vl:8b-thinking-q4_K_M"
    assert resolved.provider.base_url == "http://192.168.1.100:11434/v1"
    assert resolved.provider.auth == "none"
    assert resolved.execution_policy.model_policy["max_tokens"] == 8192
    assert resolved.execution_policy.model_policy["temperature"] == 0


def test_simple_model_yaml_infers_direct_key_and_env_key_without_mode_or_auth(
    tmp_path,
):
    (tmp_path / "prompts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompts" / "simple.md").write_text(
        "Return strict JSON.",
        encoding="utf-8",
    )
    (tmp_path / "model.yaml").write_text(
        """
models:
  cloud_test:
    provider: openai_compatible
    base_url: https://test.example/v1
    api_key: local-test-key
    model: test-model
  cloud_prod:
    provider: openai_compatible
    base_url_env: CLOUD_BASE_URL
    api_key_env: CLOUD_API_KEY
    model: prod-model
""".strip(),
        encoding="utf-8",
    )

    def write_agent(ref):
        path = tmp_path / f"{ref}.yaml"
        path.write_text(
            f"""
agent_id: demo.{ref}
model_ref: {ref}
prompt:
  ref: prompts/simple.md
output_schema:
  ref: StorageFieldResult
execution:
  retry:
    transport_attempts: 1
    validation_attempts: 1
    step_attempts: 1
  budget:
    max_provider_calls_per_step: 1
""".strip(),
            encoding="utf-8",
        )
        return path

    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        environ={
            "CLOUD_BASE_URL": "https://prod.example/v1",
            "CLOUD_API_KEY": "prod-secret",
        },
    )

    direct = loader.load(write_agent("cloud_test"))
    prod = loader.load(write_agent("cloud_prod"))

    assert direct.provider.mode == "direct"
    assert direct.provider.auth == "api_key"
    assert direct.provider.base_url == "https://test.example/v1"
    assert "local-test-key" not in direct.model_dump_json()

    assert prod.provider.mode == "env"
    assert prod.provider.auth == "api_key"
    assert prod.provider.base_url == "https://prod.example/v1"
    assert "prod-secret" not in prod.model_dump_json()


def test_agent_can_use_active_model_without_model_ref(tmp_path):
    (tmp_path / "prompts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompts" / "simple.md").write_text(
        "Return strict JSON.",
        encoding="utf-8",
    )
    (tmp_path / "model.yaml").write_text(
        """
active_model: ollama_gemma
models:
  ollama_gemma:
    provider: openai_compatible
    base_url: http://192.168.1.100:11434/v1
    model: gemma4:e4b
    temperature: 0
    max_tokens: 4096
""".strip(),
        encoding="utf-8",
    )
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        """
agent_id: demo.default
prompt:
  ref: prompts/simple.md
output_schema:
  ref: StorageFieldResult
execution:
  retry:
    transport_attempts: 1
    validation_attempts: 1
    step_attempts: 1
  budget:
    max_provider_calls_per_step: 1
""".strip(),
        encoding="utf-8",
    )

    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"StorageFieldResult": StorageFieldResult},
        environ={},
    )
    resolved = loader.load(config_path)

    assert resolved.definition.model == "gemma4:e4b"
    assert resolved.provider.profile_ref == "ollama_gemma"
    assert resolved.execution_policy.model_policy["max_tokens"] == 4096
