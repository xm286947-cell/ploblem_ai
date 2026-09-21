from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from urllib.request import Request, urlopen

import pytest
from pydantic import BaseModel

from runtime import AgentConfigLoader, AgentRequest, ConfiguredAgentRuntime, RuntimeStatus, SqliteTaskStore
from runtime.adapters import StorageFieldResult
from runtime.reliability import RuntimeStepError
from runtime.providers import OpenAICompatibleProviderAdapter
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = "config/runtime/agents/storage.emmc.parameter_extract.yaml"
MODEL_CONFIG = "config/runtime/model.yaml"
SECRET = "ORCH_B01_SECRET_MUST_NOT_PERSIST"


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


def configure(host: str, port: int, payload, behavior=None) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": behavior or {},
        },
        ensure_ascii=False,
    ).encode()
    req = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=2) as response:
        assert response.status == 200


def counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def request_history(host: str, port: int) -> list[dict]:
    with urlopen(f"http://{host}:{port}/__mock__/requests", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def storage_loader(base_url: str) -> AgentConfigLoader:
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
        environ={
            "DASHSCOPE_BASE_URL": base_url,
            "DASHSCOPE_API_KEY": SECRET,
        },
    )


def raw_database_dump(store: SqliteTaskStore) -> str:
    with sqlite3.connect(store.db_path) as connection:
        return "\n".join(connection.iterdump())


def storage_payload() -> dict:
    return {
        "device_type": "eMMC",
        "parameter_scope": "lifetime",
        "source_text": "The eMMC device life specification states pe_cycle = 3000 cycles.",
        "required_fields": ["pe_cycle"],
    }


def storage_result() -> list[dict]:
    return [
        {
            "field_id": "pe_cycle",
            "status": "FOUND",
            "normalized_value": 3000,
            "unit": "cycles",
            "evidence": [],
        }
    ]


def test_orch_b01_storage_calls_provider_without_business_http_handler(tmp_path: Path) -> None:
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(host, port, storage_result())
        store = SqliteTaskStore(tmp_path / "runtime.db")
        runtime = ConfiguredAgentRuntime(
            store,
            config_loader=storage_loader(base_url),
        )

        resolved = runtime.load_agent(AGENT_CONFIG)
        result = runtime.invoke(
            AgentRequest(
                request_id="orch-b01-storage",
                agent_id="storage.emmc.parameter_extract",
                input=storage_payload(),
            )
        )

        assert resolved.provider.type == "openai_compatible"
        assert result.status == RuntimeStatus.COMPLETED
        assert result.data[0]["field_id"] == "pe_cycle"
        assert result.data[0]["normalized_value"] == 3000
        assert result.execution.provider_calls == 1
        assert counters(host, port)["default"] == 1

        run = store.list_runs(result.task_id)[0]
        step = store.list_step_runs(run.run_id)[0]
        attempt = store.list_attempts(step.step_run_id)[0]
        assert attempt.provider == "openai_compatible"
        assert attempt.model_name == "qwen3.8-max"
        assert attempt.provider_call_seq == 1
        assert attempt.execution_metrics["sdk_retry"] == 0

        assert SECRET not in result.model_dump_json()
        assert SECRET not in raw_database_dump(store)
        history = request_history(host, port)
        assert history[0]["authorization"] == {"present": True, "scheme": "Bearer"}
        assert history[0]["headers"]["Authorization"] == "[REDACTED]"


def test_orch_b01_runtime_retry_owns_multiple_real_http_requests(tmp_path: Path) -> None:
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(
            host,
            port,
            storage_result(),
            {"fail_first_n": 1, "fail_status": 429, "retry_after": "0"},
        )
        store = SqliteTaskStore(tmp_path / "runtime.db")
        runtime = ConfiguredAgentRuntime(
            store,
            config_loader=storage_loader(base_url),
        )
        runtime.load_agent(AGENT_CONFIG)

        result = runtime.invoke(
            AgentRequest(
                request_id="orch-b01-429",
                agent_id="storage.emmc.parameter_extract",
                input=storage_payload(),
            )
        )

        assert result.status == RuntimeStatus.COMPLETED
        assert result.execution.provider_calls == 2
        assert counters(host, port)["default"] == 2


def test_orch_b01_validation_retry_is_runtime_owned(tmp_path: Path) -> None:
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(host, port, "not-json")
        store = SqliteTaskStore(tmp_path / "runtime.db")
        runtime = ConfiguredAgentRuntime(
            store,
            config_loader=storage_loader(base_url),
        )
        runtime.load_agent(AGENT_CONFIG)

        result = runtime.invoke(
            AgentRequest(
                request_id="orch-b01-invalid-json",
                agent_id="storage.emmc.parameter_extract",
                input=storage_payload(),
            )
        )

        assert result.status == RuntimeStatus.PARTIAL
        assert result.error is not None
        assert result.error.code == "INVALID_JSON"
        assert result.execution.provider_calls == 3
        assert counters(host, port)["default"] == 3


class SimpleResult(BaseModel):
    ok: bool


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _write_authless_agent(root: Path) -> Path:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts/simple.md").write_text(
        "Return only strict JSON with boolean field ok.",
        encoding="utf-8",
    )
    (root / "model.yaml").write_text(
        """
active_model: local
models:
  local:
    provider: openai_compatible
    base_url: http://127.0.0.1:11434/v1
    model: local-model
    temperature: 0
""".strip(),
        encoding="utf-8",
    )
    path = root / "agent.yaml"
    path.write_text(
        """
agent_id: authless.local
model_ref: local
prompt:
  ref: prompts/simple.md
output_schema:
  ref: SimpleResult
metadata:
  provider_response_shape: json_object
""".strip(),
        encoding="utf-8",
    )
    return path


def test_orch_b01_authless_local_provider_sends_no_authorization(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    agent = _write_authless_agent(tmp_path)
    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"SimpleResult": SimpleResult},
        environ={},
    )
    runtime = ConfiguredAgentRuntime(
        SqliteTaskStore(tmp_path / "runtime.db"),
        config_loader=loader,
    )
    runtime.load_agent(agent)

    result = runtime.invoke(
        AgentRequest(
            request_id="orch-b01-authless",
            agent_id="authless.local",
            input={"value": 1},
        )
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert result.data == {"ok": True}
    assert captured["authorization"] is None
    assert captured["url"].endswith("/v1/chat/completions")


def test_orch_b01_finish_reason_length_maps_to_runtime_truncation(tmp_path: Path, monkeypatch) -> None:
    def fake_urlopen(_request, timeout):
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {"content": '{"ok":'},
                        "finish_reason": "length",
                    }
                ]
            }
        )

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    agent = _write_authless_agent(tmp_path)
    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"SimpleResult": SimpleResult},
        environ={},
    )
    runtime = ConfiguredAgentRuntime(
        SqliteTaskStore(tmp_path / "runtime.db"),
        config_loader=loader,
    )
    resolved = runtime.load_agent(agent)
    # One validation attempt is enough here; ORCH-B02 owns the recovery chain.
    assert resolved.provider.sdk_retry == 0

    result = runtime.invoke(
        AgentRequest(
            request_id="orch-b01-length",
            agent_id="authless.local",
            input={"value": 1},
        )
    )

    assert result.status == RuntimeStatus.FAILED
    assert result.error is not None
    assert result.error.code == "OUTPUT_TRUNCATED"
    assert result.error.category.value == "VALIDATION"
    assert result.execution.provider_calls == 1



def test_orch_b01_invalid_provider_base_url_fails_fast_before_http(monkeypatch) -> None:
    called = {"value": False}

    def fake_urlopen(_request, timeout):
        called["value"] = True
        raise AssertionError("HTTP must not be attempted for invalid base_url")

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema=SimpleResult,
        response_shape="json_object",
    )

    with pytest.raises(RuntimeStepError) as exc_info:
        adapter(
            {"value": 1},
            {
                "runtime": {
                    "provider_call_seq": 1,
                    "provider_config": {
                        "base_url": "workspace.example/compatible-mode/v1",
                        "model": "qwen3.8-max",
                        "api_key": SECRET,
                    },
                }
            },
        )

    assert called["value"] is False
    assert exc_info.value.code == "PROVIDER_BASE_URL_INVALID"
    assert exc_info.value.category.value == "EXECUTION"
    assert exc_info.value.retryable is False
    assert SECRET not in str(exc_info.value.details)


def test_orch_b01_provider_trace_prints_safe_resolved_endpoint(
    monkeypatch,
    capsys,
) -> None:
    def fake_urlopen(request, timeout):
        assert request.full_url == (
            "https://workspace.example/compatible-mode/v1/chat/completions"
        )
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE", "1")
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema=SimpleResult,
        response_shape="json_object",
    )

    result = adapter(
        {"value": 1},
        {
            "runtime": {
                "provider_call_seq": 7,
                "provider_config": {
                    "base_url": "https://workspace.example/compatible-mode/v1",
                    "model": "qwen3.8-max",
                    "api_key": SECRET,
                },
            }
        },
    )

    assert result == {"ok": True}
    trace = capsys.readouterr().out
    assert "[runtime-provider]" in trace
    assert '"provider_call_seq": 7' in trace
    assert '"model": "qwen3.8-max"' in trace
    assert (
        '"endpoint": "https://workspace.example/compatible-mode/v1/chat/completions"'
        in trace
    )
    assert '"auth": "bearer_present"' in trace
    assert SECRET not in trace



def test_orch_b01_provider_trace_file_is_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    trace_file = tmp_path / "provider_runtime.log"
    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE", "1")
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE_FILE", str(trace_file))
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema=SimpleResult,
        response_shape="json_object",
    )

    result = adapter(
        {"value": 1},
        {
            "runtime": {
                "provider_call_seq": 3,
                "provider_config": {
                    "base_url": "http://127.0.0.1:18080/v1",
                    "model": "qwen3.8-max",
                    "api_key": SECRET,
                },
            }
        },
    )

    assert result == {"ok": True}
    text = trace_file.read_text(encoding="utf-8")
    assert '"phase": "request"' in text
    assert '"method": "POST"' in text
    assert '"endpoint": "http://127.0.0.1:18080/v1/chat/completions"' in text
    assert '"auth": "bearer_present"' in text
    assert '"body_bytes":' in text
    assert '"body_sha256":' in text
    assert SECRET not in text
    assert "Return strict JSON." not in text
