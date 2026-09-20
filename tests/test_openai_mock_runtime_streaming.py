from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Iterator
from urllib.request import Request, urlopen

from openai import OpenAI

from runtime import (
    AgentRequest,
    ErrorCategory,
    ExecutionMode,
    ExecutionPolicy,
    LightweightExecutionEngine,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
    SqliteTaskStore,
)
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


def configure(host: str, port: int, payload: str, behavior: dict | None = None) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": behavior or {},
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


def policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        mode=ExecutionMode.SINGLE,
        transport_retry=RetryPolicy(max_attempts=1),
        validation_retry=RetryPolicy(max_attempts=1),
        step_retry=RetryPolicy(max_attempts=1),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=1,
            max_step_attempts=1,
            max_validation_cycles_per_step_attempt=1,
            max_transport_attempts_per_model_call=1,
        ),
    )


def run_stream(tmp_path, base_url: str, request_id: str, observed: list[str]):
    store = SqliteTaskStore(tmp_path / f"{request_id}.db")
    runtime = LightweightExecutionEngine(store)

    def handler(_payload, _context):
        client = OpenAI(
            api_key="mock-key",
            base_url=base_url,
            max_retries=0,
        )
        stream = client.responses.create(
            model="mock-gpt",
            input="stream",
            stream=True,
        )
        chunks: list[str] = []
        completed = False
        try:
            for event in stream:
                if event.type == "response.output_text.delta":
                    chunks.append(event.delta)
                    observed.append(event.delta)
                elif event.type == "response.completed":
                    completed = True
        except Exception as exc:
            raise RuntimeStepError(
                f"stream transport interrupted: {exc}",
                code="STREAM_TRANSPORT_INTERRUPTED",
                category=ErrorCategory.TRANSPORT,
                retryable=True,
            ) from exc
        if not completed:
            raise RuntimeStepError(
                "stream ended before response.completed",
                code="STREAM_INCOMPLETE",
                category=ErrorCategory.TRANSPORT,
                retryable=True,
            )
        return "".join(chunks)

    runtime.register_agent("stream.mock-http", handler)
    return runtime.invoke(
        AgentRequest(
            request_id=request_id,
            agent_id="stream.mock-http",
            input={"prompt": "stream"},
            execution_policy=policy(),
        )
    )


def test_rt_mock_003_runtime_streaming_completes_in_order(tmp_path):
    observed: list[str] = []
    payload = "streaming-complete-" + ("abc123" * 40)
    with running_server() as (host, port):
        configure(host, port, payload, {"chunk_size": 17})
        result = run_stream(
            tmp_path,
            f"http://{host}:{port}/v1",
            "rt-mock-003-stream",
            observed,
        )

        assert result.status == RuntimeStatus.COMPLETED
        assert result.data == payload
        assert "".join(observed) == payload
        assert result.execution.provider_calls == 1


def test_rt_mock_009_stream_disconnect_preserves_observable_partial_and_fails(tmp_path):
    observed: list[str] = []
    payload = "partial-stream-" + ("0123456789abcdef" * 2048)
    with running_server() as (host, port):
        configure(
            host,
            port,
            payload,
            {
                "chunk_size": 32,
                "disconnect_at": 5000,
            },
        )
        result = run_stream(
            tmp_path,
            f"http://{host}:{port}/v1",
            "rt-mock-009-stream-disconnect",
            observed,
        )

        assert result.status == RuntimeStatus.FAILED
        assert result.execution.provider_calls == 1
        assert result.error is not None
        assert result.error.category == ErrorCategory.TRANSPORT
        assert observed
        assert len("".join(observed)) < len(payload)
