from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from runtime import AgentConfigLoader, AgentRequest, ConfiguredAgentRuntime, RuntimeStatus, SqliteTaskStore
from tools.openai_mock.server import create_server


class SimpleResult(BaseModel):
    ok: bool


@contextmanager
def _running_server() -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _configure(host: str, port: int, payload: str) -> None:
    raw = json.dumps({"scenario_key": "default", "payload": payload, "behavior": {}}).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def _build_runtime(root: Path, base_url: str) -> tuple[ConfiguredAgentRuntime, SqliteTaskStore]:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts/semantic.md").write_text("Return only strict JSON with boolean field ok.", encoding="utf-8")
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
        """
agent_id: runtime.semantic.handoff.test
model_ref: mock
prompt:
  ref: prompts/semantic.md
output_schema:
  ref: SimpleResult
execution:
  retry:
    transport_attempts: 1
    validation_attempts: 1
    step_attempts: 1
  budget:
    max_provider_calls_per_step: 1
metadata:
  provider_response_shape: json_object
""".strip(),
        encoding="utf-8",
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
    return runtime, store


@pytest.mark.parametrize(
    "payload,expect_pass,expect_recovered",
    [
        ('{"ok":true}', True, False),
        ('```json\n{"ok":true}\n```', True, True),
        ('Result follows:\n{"ok":true}\nEnd.', True, True),
        ('{"ok":true', False, False),
        ('{"ok":tru}', False, False),
        ('{"ok":true}\n{"ok":false}', False, False),
        ('ok=true; confidence=high; useful facts', False, False),
    ],
    ids=[
        "strict_json",
        "markdown_wrapper",
        "explanation_single_json",
        "truncated_json",
        "broken_json",
        "multi_json",
        "useful_but_invalid_json",
    ],
)
def test_runtime_semantic_handoff_matrix(tmp_path: Path, payload: str, expect_pass: bool, expect_recovered: bool) -> None:
    with _running_server() as (host, port):
        _configure(host, port, payload)
        runtime, store = _build_runtime(tmp_path, f"http://{host}:{port}/v1")
        result = runtime.invoke(
            AgentRequest(
                request_id="matrix-" + hashlib.sha256(payload.encode()).hexdigest()[:12],
                agent_id="runtime.semantic.handoff.test",
                input={"document_ref": "controlled-input-ref"},
            )
        )

        run = store.list_runs(result.task_id)[0]
        step = store.list_step_runs(run.run_id)[0]
        attempt = store.list_attempts(step.step_run_id)[0]
        evidence = attempt.execution_metrics["provider_evidence"]

        if expect_pass:
            assert result.status == RuntimeStatus.COMPLETED
            assert result.error is None
            assert result.data == {"ok": True}
            assert evidence["recovered"] is expect_recovered
            assert store.list_semantic_handoffs(task_id=result.task_id) == []
            return

        assert result.status != RuntimeStatus.COMPLETED
        assert result.data is None
        assert result.error is not None
        assert result.error.code == "SEMANTIC_REPAIR_REQUIRED"
        details = result.error.details
        assert details["recoverable_content_available"] is True
        assert details["content_hash"] == hashlib.sha256(payload.encode()).hexdigest()
        assert details["content_length"] == len(payload)
        assert details["content_access_scope"] == "TASK"
        content_ref = details["content_ref"]
        assert runtime.read_semantic_handoff_content(task_id=result.task_id, content_ref=content_ref) == payload
        assert runtime.read_semantic_handoff_content(task_id="wrong-task", content_ref=content_ref) is None
        assert payload not in result.model_dump_json()
        assert payload not in attempt.model_dump_json()
