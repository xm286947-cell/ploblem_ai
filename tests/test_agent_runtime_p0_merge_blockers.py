from __future__ import annotations

from pathlib import Path

from builder.ai_client import AIClientError, AIResponse
from runtime import (
    AgentRequest,
    ErrorCategory,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    LightweightExecutionEngine,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    SqliteTaskStore,
)
from runtime.adapters import LegacyQualityIssueStageHandlerAdapter


class SequencedClient:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0
        self.max_retries = 99
        self.messages = []

    def complete(self, messages):
        self.calls += 1
        self.messages.append(messages)
        action = self.actions[min(self.calls - 1, len(self.actions) - 1)]
        if isinstance(action, Exception):
            raise action
        return AIResponse(
            content=action,
            model="test-model",
            raw={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": action},
                    }
                ]
            },
        )


def write_runtime_managed_quality_fixture(root: Path) -> None:
    config = root / "config"
    prompts = root / "quality_knowledge" / "prompts"
    config.mkdir(parents=True)
    prompts.mkdir(parents=True)
    (config / "model.yaml").write_text(
        """
ai:
  enabled: true
  provider: openai_compatible
  base_url: http://unit.invalid/v1
  api_key_env: TEST_API_KEY
  model: base-model
  temperature: 0
  max_tokens: 4096
  timeout_seconds: 30
  max_retries: 2
  validation_retries: 2
quality_issue_agents:
  quality:
    enabled: true
    provider: openai_compatible
    model: quality-model
""".strip(),
        encoding="utf-8",
    )
    for stage in ("occurrence", "escape", "recurrence", "capability_gap"):
        (prompts / f"{stage}.md").write_text(
            f"strict json prompt for {stage}\n",
            encoding="utf-8",
        )


def runtime_policy(*, transport: int, validation: int, budget: int) -> ExecutionPolicy:
    return ExecutionPolicy(
        mode=ExecutionMode.SINGLE,
        transport_retry=RetryPolicy(max_attempts=transport),
        validation_retry=RetryPolicy(max_attempts=validation),
        step_retry=RetryPolicy(max_attempts=1),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=budget,
            max_step_attempts=1,
            max_validation_cycles_per_step_attempt=validation,
            max_transport_attempts_per_model_call=transport,
        ),
        failure_policy=FailurePolicy.PARTIAL,
    )


def run_occurrence(tmp_path, client, policy, request_id):
    root = tmp_path / request_id
    write_runtime_managed_quality_fixture(root)
    store = SqliteTaskStore(tmp_path / f"{request_id}.db")
    engine = LightweightExecutionEngine(store)
    adapter = LegacyQualityIssueStageHandlerAdapter(
        root,
        client=client,
        agent_id="quality",
    )
    engine.register_agent(
        "legacy.occurrence.real",
        adapter.handler("occurrence"),
    )
    result = engine.invoke(
        AgentRequest(
            request_id=request_id,
            agent_id="legacy.occurrence.real",
            input={
                "issue": {
                    "knowledge_id": "QK-BLOCKER",
                    "issue_version_id": "V1",
                }
            },
            execution_policy=policy,
        )
    )
    return store, result


def test_blocker_b01_runtime_test_dependencies_are_repository_declared():
    root = Path(__file__).resolve().parents[1]
    deps = (root / "requirements-runtime-p0-test.txt").read_text(
        encoding="utf-8"
    )
    workflow = (
        root / ".github" / "workflows" / "agent-runtime-p0.yml"
    ).read_text(encoding="utf-8")

    assert "langgraph>=1,<2" in deps
    assert "pytest>=8,<9" in deps
    assert (
        "pip install -r requirements-runtime-p0-test.txt"
        in workflow
    )
    assert (
        'pip install --upgrade pip "pytest>=8,<9"'
        not in workflow
    )


def test_blocker_b02_transport_retry_is_owned_and_counted_by_runtime(tmp_path):
    client = SequencedClient(
        [
            AIClientError("temporary network failure"),
            "{}",
        ]
    )
    _, result = run_occurrence(
        tmp_path,
        client,
        runtime_policy(transport=2, validation=1, budget=2),
        "blocker-b02-transport",
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert client.max_retries == 0
    assert client.calls == 2
    assert result.execution.provider_calls == 2
    assert result.execution.provider_calls <= 2


def test_blocker_b02_validation_retry_is_owned_and_counted_by_runtime(tmp_path):
    client = SequencedClient(
        [
            "not-json",
            "{}",
        ]
    )
    _, result = run_occurrence(
        tmp_path,
        client,
        runtime_policy(transport=1, validation=2, budget=2),
        "blocker-b02-validation",
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert client.max_retries == 0
    assert client.calls == 2
    assert result.execution.provider_calls == 2
    assert result.execution.provider_calls <= 2
    assert "上一次输出未通过结构校验" in (
        client.messages[1][-1]["content"]
    )


def test_blocker_b02_hard_budget_cannot_be_bypassed_by_legacy_client(tmp_path):
    client = SequencedClient(
        [AIClientError("provider unavailable")]
    )
    _, result = run_occurrence(
        tmp_path,
        client,
        runtime_policy(transport=5, validation=1, budget=2),
        "blocker-b02-hard-cap",
    )

    assert result.status == RuntimeStatus.FAILED
    assert client.max_retries == 0
    assert client.calls == 2
    assert result.execution.provider_calls == 2
    assert result.execution.retry_budget_exhausted is True
    assert result.error is not None
    assert result.error.code == "RETRY_BUDGET_EXHAUSTED"
