from __future__ import annotations

import json
from pathlib import Path

from runtime import RuntimeStatus, SqliteTaskStore
from runtime.adapters import StorageFieldResult
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime
from storage_e2e import StorageSemanticSecondPassCoordinator


ROOT = Path(__file__).resolve().parents[1]
PRIMARY_CONFIG = "config/runtime/agents/storage.emmc.parameter_extract.yaml"
SECOND_CONFIG = "config/runtime/agents/storage.emmc.semantic_reextract.yaml"


class FakeResponse:
    def __init__(self, content: str, *, finish_reason: str = "stop"):
        self.status = 200
        self.headers = {
            "Content-Type": "application/json",
            "x-request-id": "storage-semantic-second-pass",
        }
        self._raw = json.dumps(
            {
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._raw


def _runtime(tmp_path: Path) -> tuple[ConfiguredAgentRuntime, SqliteTaskStore]:
    model_config = tmp_path / "model.yaml"
    model_config.write_text(
        """
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: https://provider.example/v1
    api_key: semantic-second-pass-test-secret
    model: mock-company-model
    temperature: 0
    max_tokens: 8192
    metadata:
      capability_source: CONFIGURED
      capabilities:
        structured_output: unsupported
        token_limit_parameter: max_tokens
""".strip(),
        encoding="utf-8",
    )
    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=model_config,
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
        environ={},
    )
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    runtime.load_agent(PRIMARY_CONFIG)
    runtime.load_agent(SECOND_CONFIG)
    return runtime, store


def _field_json(value: int = 3000) -> str:
    return json.dumps(
        [
            {
                "field_id": "pe_cycle",
                "status": "FOUND",
                "normalized_value": value,
                "unit": "cycles",
                "evidence": [],
            }
        ],
        ensure_ascii=False,
    )


def test_primary_success_does_not_trigger_semantic_second_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict] = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse(_field_json())

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )
    runtime, _store = _runtime(tmp_path)
    coordinator = StorageSemanticSecondPassCoordinator(runtime)

    outcome = coordinator.execute(
        {
            "device_type": "eMMC",
            "parameter_scope": "lifetime",
            "source_text": "The source states pe_cycle = 3000 cycles.",
            "required_fields": ["pe_cycle"],
        },
        request_id="storage-semantic-primary-pass",
    )

    assert outcome.initial_result.status == RuntimeStatus.COMPLETED
    assert outcome.second_pass_triggered is False
    assert outcome.second_pass_result is None
    assert outcome.accepted is True
    assert outcome.business_error is None
    assert outcome.reviewed_specification is not None
    assert outcome.reviewed_specification[0].normalized_value == 3000
    assert len(calls) == 1


def test_semantic_failure_uses_first_handoff_for_one_business_second_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_material = (
        "Useful Provider material: pe_cycle is 3000 cycles, "
        "but this response is not JSON."
    )
    later_material_2 = (
        "Retry material says pe_cycle is 4000 cycles, still not JSON."
    )
    later_material_3 = (
        "Retry material says pe_cycle is 5000 cycles, still not JSON."
    )
    original_source = "ORIGINAL_PDF_TEXT_MUST_NOT_ENTER_SECOND_PASS"

    responses = iter(
        [
            first_material,
            later_material_2,
            later_material_3,
            _field_json(3000),
        ]
    )
    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse(next(responses))

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )
    runtime, store = _runtime(tmp_path)
    coordinator = StorageSemanticSecondPassCoordinator(runtime)

    outcome = coordinator.execute(
        {
            "device_type": "eMMC",
            "parameter_scope": "lifetime",
            "source_text": original_source,
            "required_fields": ["pe_cycle"],
        },
        request_id="storage-semantic-second-pass",
    )

    assert outcome.initial_result.status != RuntimeStatus.COMPLETED
    assert outcome.initial_result.error is not None
    assert outcome.initial_result.error.code == "SEMANTIC_REPAIR_REQUIRED"
    assert outcome.second_pass_triggered is True
    assert outcome.second_pass_result is not None
    assert outcome.second_pass_result.status == RuntimeStatus.COMPLETED
    assert outcome.final_runtime_result.status == RuntimeStatus.COMPLETED
    assert outcome.accepted is True
    assert outcome.business_error is None
    assert outcome.reviewed_specification is not None
    assert outcome.reviewed_specification[0].normalized_value == 3000

    handoffs = store.list_semantic_handoffs(
        task_id=outcome.initial_result.task_id
    )
    assert len(handoffs) == 3
    assert outcome.semantic_handoff_ref == handoffs[0].content_ref
    assert (
        runtime.read_semantic_handoff_content(
            task_id=outcome.initial_result.task_id,
            content_ref=outcome.semantic_handoff_ref,
        )
        == first_material
    )

    assert len(requests) == 4
    second_pass_user_payload = json.loads(
        requests[-1]["messages"][1]["content"]
    )
    assert second_pass_user_payload == {
        "device_type": "eMMC",
        "parameter_scope": "lifetime",
        "required_fields": ["pe_cycle"],
        "provider_material": first_material,
    }
    assert original_source not in requests[-1]["messages"][1]["content"]
    assert later_material_2 not in requests[-1]["messages"][1]["content"]
    assert later_material_3 not in requests[-1]["messages"][1]["content"]

    second_task = store.get_task(
        outcome.second_pass_result.task_id
    )
    assert second_task is not None
    assert second_task.metadata["semantic_pass"] == 2
    assert (
        second_task.metadata["parent_task_id"]
        == outcome.initial_result.task_id
    )
    assert (
        second_task.metadata["semantic_handoff_ref"]
        == handoffs[0].content_ref
    )


def test_second_pass_terminal_failure_does_not_trigger_third_business_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    primary_materials = [
        "first primary useful but invalid response",
        "second primary useful but invalid response",
        "third primary useful but invalid response",
    ]
    second_pass_materials = [
        "second pass invalid response one",
        "second pass invalid response two",
    ]
    responses = iter(primary_materials + second_pass_materials)
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return FakeResponse(next(responses))

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )
    runtime, store = _runtime(tmp_path)
    coordinator = StorageSemanticSecondPassCoordinator(runtime)

    outcome = coordinator.execute(
        {
            "device_type": "eMMC",
            "parameter_scope": "lifetime",
            "source_text": "original source",
            "required_fields": ["pe_cycle"],
        },
        request_id="storage-semantic-no-third-pass",
    )

    assert outcome.second_pass_triggered is True
    assert outcome.second_pass_result is not None
    assert outcome.second_pass_result.status != RuntimeStatus.COMPLETED
    assert outcome.final_runtime_result is outcome.second_pass_result
    assert outcome.reviewed_specification is None
    assert outcome.accepted is False
    assert outcome.business_error == "SECOND_PASS_FAILED"
    assert calls == 5

    tasks = [
        store.get_task(outcome.initial_result.task_id),
        store.get_task(outcome.second_pass_result.task_id),
    ]
    assert all(task is not None for task in tasks)
    assert tasks[0].metadata["semantic_pass"] == 1
    assert tasks[1].metadata["semantic_pass"] == 2


def test_second_pass_rejects_handoff_integrity_mismatch_before_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    responses = iter(
        [
            "first invalid semantic response",
            "second invalid semantic response",
            "third invalid semantic response",
        ]
    )
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return FakeResponse(next(responses))

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )
    runtime, _store = _runtime(tmp_path)

    original_reader = runtime.read_semantic_handoff_content

    def tampered_reader(*, task_id: str, content_ref: str):
        value = original_reader(task_id=task_id, content_ref=content_ref)
        assert value is not None
        return value + "-tampered"

    monkeypatch.setattr(
        runtime,
        "read_semantic_handoff_content",
        tampered_reader,
    )
    coordinator = StorageSemanticSecondPassCoordinator(runtime)

    outcome = coordinator.execute(
        {
            "device_type": "eMMC",
            "parameter_scope": "lifetime",
            "source_text": "original source",
            "required_fields": ["pe_cycle"],
        },
        request_id="storage-semantic-integrity-fail",
    )

    assert outcome.second_pass_triggered is False
    assert outcome.second_pass_result is None
    assert outcome.accepted is False
    assert outcome.business_error == "SEMANTIC_HANDOFF_INTEGRITY_FAILED"
    assert calls == 3


def test_second_pass_requires_exact_required_field_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    responses = iter(
        [
            "primary invalid one",
            "primary invalid two",
            "primary invalid three",
            json.dumps(
                [
                    {
                        "field_id": "unexpected_field",
                        "status": "FOUND",
                        "normalized_value": 123,
                        "unit": None,
                        "evidence": [],
                    }
                ]
            ),
        ]
    )

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda request, timeout: FakeResponse(next(responses)),
    )
    runtime, _store = _runtime(tmp_path)
    coordinator = StorageSemanticSecondPassCoordinator(runtime)

    outcome = coordinator.execute(
        {
            "device_type": "eMMC",
            "parameter_scope": "lifetime",
            "source_text": "original source",
            "required_fields": ["pe_cycle"],
        },
        request_id="storage-semantic-field-gate",
    )

    assert outcome.second_pass_triggered is True
    assert outcome.second_pass_result is not None
    assert outcome.second_pass_result.status == RuntimeStatus.COMPLETED
    assert outcome.accepted is False
    assert outcome.reviewed_specification is None
    assert outcome.business_error == "SECOND_PASS_FIELD_SET_MISMATCH"
