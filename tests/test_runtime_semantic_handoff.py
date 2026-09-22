from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from runtime import AgentConfigLoader, AgentRequest, ConfiguredAgentRuntime, RuntimeStatus, SqliteTaskStore
from tools.openai_mock.server import create_server


class SimpleResult(BaseModel):
    ok: bool


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


def configure(host: str, port: int, payload: str) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def write_runtime_config(
    root: Path,
    *,
    base_url: str,
    validation_attempts: int = 1,
) -> Path:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts/semantic.md").write_text(
        "Return only strict JSON with boolean field ok.",
        encoding="utf-8",
    )
    (root / "model.yaml").write_text(
        f"""
active_model: mock
models:
  mock:
    provider: openai_compatible
    base_url: {base_url}
    api_key: mock-test-secret
    model: mock-gpt
    temperature: 0
    max_tokens: 256
    metadata:
      capability_source: CONFIGURED
      capabilities:
        structured_output: unsupported
        token_limit_parameter: max_tokens
""".strip(),
        encoding="utf-8",
    )
    agent = root / "agent.yaml"
    agent.write_text(
        f"""
agent_id: runtime.semantic.handoff.test
model_ref: mock
prompt:
  ref: prompts/semantic.md
output_schema:
  ref: SimpleResult
execution:
  retry:
    transport_attempts: 1
    validation_attempts: {validation_attempts}
    step_attempts: 1
  budget:
    max_provider_calls_per_step: {validation_attempts}
metadata:
  provider_response_shape: json_object
""".strip(),
        encoding="utf-8",
    )
    return agent


def build_runtime(
    root: Path,
    *,
    base_url: str,
    validation_attempts: int = 1,
) -> tuple[ConfiguredAgentRuntime, SqliteTaskStore, Path]:
    agent = write_runtime_config(
        root,
        base_url=base_url,
        validation_attempts=validation_attempts,
    )
    store = SqliteTaskStore(root / "runtime.db")
    loader = AgentConfigLoader(
        root=root,
        model_profiles=root / "model.yaml",
        schemas={"SimpleResult": SimpleResult},
        environ={},
    )
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    runtime.load_agent(agent)
    return runtime, store, agent


MOCK_MATRIX = [
    (
        "strict_json",
        '{"ok":true}',
        "PASS",
        False,
    ),
    (
        "markdown_wrapper",
        '```json\n{"ok":true}\n```',
        "PASS",
        True,
    ),
    (
        "explanation_single_json",
        'Result follows:\n{"ok":true}\nEnd of result.',
        "PASS",
        True,
    ),
    (
        "truncated_json",
        '{"ok":true',
        "SEMANTIC_REPAIR_REQUIRED",
        False,
    ),
    (
        "broken_json",
        '{"ok":tru}',
        "SEMANTIC_REPAIR_REQUIRED",
        False,
    ),
    (
        "multi_json",
        '{"ok":true}\n{"ok":false}',
        "SEMANTIC_REPAIR_REQUIRED",
        False,
    ),
    (
        "useful_but_invalid_json",
        "ok=true; confidence=high; note=provider returned useful facts",
        "SEMANTIC_REPAIR_REQUIRED",
        False,
    ),
]


@pytest.mark.parametrize(
    "case_name,payload,expected,recovered",
    MOCK_MATRIX,
    ids=[case[0] for case in MOCK_MATRIX],
)
def test_openai_mock_semantic_handoff_contract_matrix(
    tmp_path: Path,
    case_name: str,
    payload: str,
    expected: str,
    recovered: bool,
) -> None:
    with running_server() as (host, port):
        configure(host, port, payload)
        case_root = tmp_path / case_name
        case_root.mkdir()
        runtime, store, _ = build_runtime(
            case_root,
            base_url=f"http://{host}:{port}/v1",
        )

        result = runtime.invoke(
            AgentRequest(
                request_id=f"semantic-matrix-{case_name}",
                agent_id="runtime.semantic.handoff.test",
                input={"document_ref": "controlled-input-ref"},
            )
        )

        run = store.list_runs(result.task_id)[0]
        step = store.list_step_runs(run.run_id)[0]
        attempts = store.list_attempts(step.step_run_id)
        assert len(attempts) == 1
        attempt = attempts[0]
        provider_evidence = attempt.execution_metrics["provider_evidence"]

        if expected == "PASS":
            assert result.status == RuntimeStatus.COMPLETED
            assert result.error is None
            assert result.data == {"ok": True}
            assert provider_evidence["recovered"] is recovered
            if recovered:
                assert (
                    provider_evidence["recovery_type"]
                    == "DETERMINISTIC_WRAPPER_RECOVERY"
                )
                assert provider_evidence["strict_parse_after_recovery"] == "PASS"
                assert provider_evidence["schema_validation_after_recovery"] == "PASS"
            assert store.list_semantic_handoffs(task_id=result.task_id) == []
            return

        assert result.status != RuntimeStatus.COMPLETED
        assert result.data is None
        assert result.error is not None
        assert result.error.code == "SEMANTIC_REPAIR_REQUIRED"
        details = result.error.details

        assert details["recoverable_content_available"] is True
        assert str(details["content_ref"]).startswith("semantic-handoff:")
        assert details["content_hash"] == hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()
        assert details["content_length"] == len(payload)
        assert details["content_access_scope"] == "TASK"
        assert details["raw_finish_reason"] == "stop"
        assert isinstance(details["raw_usage"], dict)
        assert details["request_max_tokens"] == 256
        assert details["request_max_completion_tokens"] == "NOT_SENT"
        assert details["structured_output_capability"] == "UNSUPPORTED"
        assert details["structured_output_request"] == "NONE"
        assert details["response_format_type"] == "NOT_SENT"

        content_ref = details["content_ref"]
        assert (
            store.read_semantic_handoff_content(
                content_ref,
                task_id=result.task_id,
            )
            == payload
        )
        assert (
            store.read_semantic_handoff_content(
                content_ref,
                task_id="task-not-authorized",
            )
            is None
        )
        handoff = store.get_semantic_handoff(
            content_ref,
            task_id=result.task_id,
        )
        assert handoff is not None
        assert handoff.attempt_id == attempt.attempt_id
        assert attempt.raw_response_ref == content_ref
        assert attempt.execution_metrics["semantic_handoff_ref"] == content_ref

        serialized_result = result.model_dump_json()
        serialized_attempt = attempt.model_dump_json()
        assert payload not in serialized_result
        assert payload not in serialized_attempt
        assert "Return only strict JSON with boolean field ok." not in serialized_result
        assert "mock-test-secret" not in serialized_result
        assert "mock-test-secret" not in serialized_attempt

        with sqlite3.connect(store.db_path) as connection:
            row = connection.execute(
                """
                SELECT record_json, content_text
                FROM runtime_semantic_handoff
                WHERE content_ref=?
                """,
                (content_ref,),
            ).fetchone()
        assert row is not None
        assert payload not in row[0]
        assert row[1] == payload


class FakeResponse:
    def __init__(self, content: str):
        self.status = 200
        self.headers = {
            "Content-Type": "application/json",
            "x-request-id": "semantic-first-response",
        }
        self._body = json.dumps(
            {
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 6,
                    "total_tokens": 16,
                },
            }
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def test_final_failure_context_keeps_first_semantic_provider_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = "first useful response: ok=true but invalid json"
    second = "second retry response: ok=false but still invalid json"
    responses = iter([first, second])

    def fake_urlopen(_request, timeout):
        return FakeResponse(next(responses))

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        fake_urlopen,
    )
    runtime, store, _ = build_runtime(
        tmp_path,
        base_url="https://provider.example/v1",
        validation_attempts=2,
    )

    result = runtime.invoke(
        AgentRequest(
            request_id="semantic-first-response",
            agent_id="runtime.semantic.handoff.test",
            input={"document_ref": "controlled-input-ref"},
        )
    )

    assert result.error is not None
    assert result.error.code == "SEMANTIC_REPAIR_REQUIRED"
    handoffs = store.list_semantic_handoffs(task_id=result.task_id)
    assert len(handoffs) == 2
    assert handoffs[0].provider_call_seq == 1
    assert handoffs[1].provider_call_seq == 2

    canonical_ref = result.error.details["content_ref"]
    assert canonical_ref == handoffs[0].content_ref
    assert (
        store.read_semantic_handoff_content(
            canonical_ref,
            task_id=result.task_id,
        )
        == first
    )

    run = store.list_runs(result.task_id)[0]
    step = store.list_step_runs(run.run_id)[0]
    attempts = store.list_attempts(step.step_run_id)
    assert [attempt.raw_response_ref for attempt in attempts] == [
        handoffs[0].content_ref,
        handoffs[1].content_ref,
    ]
    assert second not in result.model_dump_json()
