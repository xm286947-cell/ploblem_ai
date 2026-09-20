from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

from builder.ai_client import OpenAICompatibleClient
from runtime import (
    AgentRequest,
    ExecutionMode,
    ExecutionPolicy,
    LightweightExecutionEngine,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    SqliteTaskStore,
)
from runtime.adapters import LegacyQualityIssueStageHandlerAdapter
from tools.openai_mock.server import create_server


@contextmanager
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def configure(host: str, port: int, *, payload: str, behavior: dict) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": behavior,
        }
    ).encode()
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def write_fixture(root: Path, base_url: str) -> None:
    config = root / "config"
    prompts = root / "quality_knowledge" / "prompts"
    config.mkdir(parents=True)
    prompts.mkdir(parents=True)
    (config / "model.yaml").write_text(
        f"""
ai:
  enabled: true
  provider: openai_compatible
  base_url: {base_url}
  api_key_env: OPENAI_MOCK_TEST_KEY
  model: mock-gpt
  temperature: 0
  max_tokens: 4096
  timeout_seconds: 2
  max_retries: 9
  validation_retries: 2
quality_issue_agents:
  quality:
    enabled: true
    provider: openai_compatible
    model: mock-gpt
""".strip(),
        encoding="utf-8",
    )
    for stage in ("occurrence", "escape", "recurrence", "capability_gap"):
        (prompts / f"{stage}.md").write_text(
            f"strict json prompt for {stage}\n",
            encoding="utf-8",
        )


def runtime_policy(*, transport: int, budget: int) -> ExecutionPolicy:
    return ExecutionPolicy(
        mode=ExecutionMode.SINGLE,
        transport_retry=RetryPolicy(max_attempts=transport),
        validation_retry=RetryPolicy(max_attempts=1),
        step_retry=RetryPolicy(max_attempts=1),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=budget,
            max_step_attempts=1,
            max_validation_cycles_per_step_attempt=1,
            max_transport_attempts_per_model_call=transport,
        ),
    )


def run_occurrence(
    tmp_path: Path,
    *,
    base_url: str,
    client: OpenAICompatibleClient,
    policy: ExecutionPolicy,
    request_id: str,
):
    root = tmp_path / request_id
    write_fixture(root, base_url)
    store = SqliteTaskStore(tmp_path / f"{request_id}.db")
    engine = LightweightExecutionEngine(store)
    adapter = LegacyQualityIssueStageHandlerAdapter(
        root,
        client=client,
        agent_id="quality",
    )
    engine.register_agent(
        "legacy.occurrence.mock-http",
        adapter.handler("occurrence"),
    )
    result = engine.invoke(
        AgentRequest(
            request_id=request_id,
            agent_id="legacy.occurrence.mock-http",
            input={
                "issue": {
                    "knowledge_id": "QK-MOCK",
                    "issue_version_id": "V1",
                }
            },
            execution_policy=policy,
        )
    )
    return store, result


def test_runtime_real_http_429_then_success_is_owned_by_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MOCK_TEST_KEY", "mock-secret")
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(
            host,
            port,
            payload="{}",
            behavior={
                "fail_first_n": 2,
                "fail_status": 429,
                "retry_after": "0",
            },
        )
        client = OpenAICompatibleClient(
            {
                "base_url": base_url,
                "model": "mock-gpt",
                "api_key_env": "OPENAI_MOCK_TEST_KEY",
                "timeout_seconds": 2,
                "max_retries": 9,
            }
        )

        _, result = run_occurrence(
            tmp_path,
            base_url=base_url,
            client=client,
            policy=runtime_policy(transport=3, budget=3),
            request_id="mock-runtime-429-recovery",
        )

        assert client.max_retries == 0
        assert result.status == RuntimeStatus.COMPLETED
        assert result.execution.provider_calls == 3
        assert counters(host, port)["default"] == 3


def test_runtime_real_http_persistent_503_stops_at_hard_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MOCK_TEST_KEY", "mock-secret")
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(
            host,
            port,
            payload="{}",
            behavior={
                "status": 503,
            },
        )
        client = OpenAICompatibleClient(
            {
                "base_url": base_url,
                "model": "mock-gpt",
                "api_key_env": "OPENAI_MOCK_TEST_KEY",
                "timeout_seconds": 2,
                "max_retries": 9,
            }
        )

        _, result = run_occurrence(
            tmp_path,
            base_url=base_url,
            client=client,
            policy=runtime_policy(transport=5, budget=2),
            request_id="mock-runtime-503-budget",
        )

        assert client.max_retries == 0
        assert result.status == RuntimeStatus.FAILED
        assert result.execution.provider_calls == 2
        assert result.execution.retry_budget_exhausted is True
        assert counters(host, port)["default"] == 2


def test_runtime_real_http_500_once_then_success(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MOCK_TEST_KEY", "mock-secret")
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(
            host,
            port,
            payload="{}",
            behavior={
                "fail_first_n": 1,
                "fail_status": 500,
            },
        )
        client = OpenAICompatibleClient(
            {
                "base_url": base_url,
                "model": "mock-gpt",
                "api_key_env": "OPENAI_MOCK_TEST_KEY",
                "timeout_seconds": 2,
                "max_retries": 5,
            }
        )

        _, result = run_occurrence(
            tmp_path,
            base_url=base_url,
            client=client,
            policy=runtime_policy(transport=2, budget=2),
            request_id="mock-runtime-500-recovery",
        )

        assert client.max_retries == 0
        assert result.status == RuntimeStatus.COMPLETED
        assert result.execution.provider_calls == 2
        assert counters(host, port)["default"] == 2


def test_runtime_real_http_persistent_429_stops_at_hard_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MOCK_TEST_KEY", "mock-secret")
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(
            host,
            port,
            payload="{}",
            behavior={
                "status": 429,
                "retry_after": "0",
            },
        )
        client = OpenAICompatibleClient(
            {
                "base_url": base_url,
                "model": "mock-gpt",
                "api_key_env": "OPENAI_MOCK_TEST_KEY",
                "timeout_seconds": 2,
                "max_retries": 9,
            }
        )

        _, result = run_occurrence(
            tmp_path,
            base_url=base_url,
            client=client,
            policy=runtime_policy(transport=5, budget=2),
            request_id="mock-runtime-429-budget",
        )

        assert client.max_retries == 0
        assert result.status == RuntimeStatus.FAILED
        assert result.execution.provider_calls == 2
        assert result.execution.retry_budget_exhausted is True
        assert counters(host, port)["default"] == 2


def test_runtime_real_http_timeout_is_classified_and_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MOCK_TEST_KEY", "mock-secret")
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(
            host,
            port,
            payload="{}",
            behavior={
                "delay_ms": 1500,
            },
        )
        client = OpenAICompatibleClient(
            {
                "base_url": base_url,
                "model": "mock-gpt",
                "api_key_env": "OPENAI_MOCK_TEST_KEY",
                "timeout_seconds": 1,
                "max_retries": 9,
            }
        )

        _, result = run_occurrence(
            tmp_path,
            base_url=base_url,
            client=client,
            policy=runtime_policy(transport=1, budget=1),
            request_id="mock-runtime-timeout",
        )

        assert client.max_retries == 0
        assert result.status == RuntimeStatus.FAILED
        assert result.execution.provider_calls == 1
        assert counters(host, port)["default"] == 1
        assert result.error is not None
        assert result.error.category.value == "TRANSPORT"
        assert "超时" in result.error.message or "timed out" in result.error.message.lower()
