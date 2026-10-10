from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import sys
import threading
from typing import Iterator
from urllib.error import HTTPError
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
        # Offline tests must never depend on local user/Provider configs.
        # Use the same runtime profile contract with an explicit in-memory mock.
        model_profiles={
            "active_model": "qwen_prod",
            "models": {
                "qwen_prod": {
                    "provider": "openai_compatible",
                    "base_url_env": "DASHSCOPE_BASE_URL",
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "model": "qwen3.8-max",
                    "temperature": 0,
                },
            },
        },
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
    assert exc_info.value.category.value == "CONFIG"
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
    def fake_urlopen(request, timeout):
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


def test_provider_trace_survives_strict_gbk_console(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class StrictGbkStdout:
        encoding = "cp936"

        def write(self, value: str) -> int:
            # Simulate a Windows CP936 console that cannot encode the marker.
            value.encode("gbk")
            return len(value)

        def flush(self) -> None:
            return None

    def fake_urlopen(request, timeout):
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

    trace_file = tmp_path / "provider_gbk.log"
    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "stdout", StrictGbkStdout())
    monkeypatch.setenv("RUNTIME_PROVIDER_DIAGNOSTICS", "1")
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE_FILE", str(trace_file))
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema=SimpleResult,
        response_shape="json_object",
    )

    result = adapter(
        {"datasheet_marker": "Ω ✓"},
        {
            "runtime": {
                "provider_call_seq": 13,
                "provider_config": {
                    "base_url": "http://127.0.0.1:18080/v1",
                    "model": "qwen3.8-max",
                    "api_key": SECRET,
                },
            }
        },
    )

    assert result == {"ok": True}
    trace = trace_file.read_text(encoding="utf-8")
    assert "Ω ✓" in trace
    assert '"phase": "request"' in trace
    assert '"phase": "response"' in trace



def test_provider_diagnostics_prints_actual_request_body_with_secret_redaction(
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
    monkeypatch.setenv("RUNTIME_PROVIDER_DIAGNOSTICS", "1")
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return strict JSON.",
        output_schema=SimpleResult,
        response_shape="json_object",
    )

    result = adapter(
        {
            "normal": "visible-value",
            "password": "BUSINESS_PASSWORD_MUST_NOT_LOG",
            "api_key": "BUSINESS_API_KEY_MUST_NOT_LOG",
        },
        {
            "runtime": {
                "provider_call_seq": 11,
                "provider_config": {
                    "base_url": "https://workspace.example/compatible-mode/v1",
                    "model": "qwen3.8-max",
                    "api_key": SECRET,
                    "temperature": 0,
                    "max_tokens": 8192,
                },
            }
        },
    )

    assert result == {"ok": True}
    trace = capsys.readouterr().out
    assert '"phase": "request"' in trace
    assert '"headers": {"Authorization": "Bearer [REDACTED]", "Content-Type": "application/json"}' in trace
    assert '"model": "qwen3.8-max"' in trace
    assert '"temperature": 0' in trace
    assert '"max_tokens": 8192' in trace
    assert "visible-value" in trace
    assert "Return strict JSON." in trace
    assert "BUSINESS_PASSWORD_MUST_NOT_LOG" not in trace
    assert "BUSINESS_API_KEY_MUST_NOT_LOG" not in trace
    assert SECRET not in trace
    assert "[REDACTED]" in trace


def test_provider_diagnostics_captures_http_400_error_body_and_request_id(
    monkeypatch,
    capsys,
) -> None:
    error_payload = json.dumps(
        {
            "error": {
                "code": "InvalidParameter",
                "message": "invalid request: model is not supported",
                "api_key": SECRET,
            }
        }
    ).encode()

    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {
                "Content-Type": "application/json",
                "x-request-id": "req-agent-400-001",
                "Set-Cookie": "MUST_NOT_LOG",
            },
            BytesIO(error_payload),
        )

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    monkeypatch.setenv("RUNTIME_PROVIDER_DIAGNOSTICS", "1")
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
                    "provider_call_seq": 12,
                    "provider_config": {
                        "base_url": "https://workspace.example/compatible-mode/v1",
                        "model": "qwen3.8-max",
                        "api_key": SECRET,
                    },
                }
            },
        )

    error = exc_info.value
    assert error.code == "PROVIDER_HTTP_ERROR"
    assert error.details["http_status"] == 400
    assert error.details["provider_request_id"] == "req-agent-400-001"
    assert error.retryable is False

    trace = capsys.readouterr().out
    assert '"phase": "http_error"' in trace
    assert '"status": 400' in trace
    assert '"provider_request_id": "req-agent-400-001"' in trace
    assert "invalid request: model is not supported" in trace
    assert '"api_key": "[REDACTED]"' in trace
    assert "Set-Cookie" not in trace
    assert "MUST_NOT_LOG" not in trace
    assert SECRET not in trace


def test_storage475_invalid_json_keeps_diagnostic_metadata_without_response_leak(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Existing Runtime DB and trace expose metadata, never provider text."""
    import hashlib

    marker = "CONFIDENTIAL_PROVIDER_OUTPUT_475"
    # An incomplete fence must still raise INVALID_JSON after bounded reuse.
    content = (chr(96) * 3) + 'json\n{"ok":true,"marker":"' + marker + '"}\n'
    calls = []

    class SafeResponse(_FakeResponse):
        status = 200
        headers = {"X-Request-Id": "req-475-mock", "Content-Type": "application/json"}

    def fake_urlopen(_request, timeout):
        calls.append(1)
        return SafeResponse({
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 33},
        })

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    trace = tmp_path / "provider-trace.jsonl"
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE_FILE", str(trace))
    monkeypatch.delenv("RUNTIME_PROVIDER_TRACE", raising=False)
    monkeypatch.delenv("RUNTIME_PROVIDER_DIAGNOSTICS", raising=False)
    agent = _write_authless_agent(tmp_path)
    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"SimpleResult": SimpleResult},
        environ={},
    )
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    runtime.load_agent(agent)
    result = runtime.invoke(AgentRequest(
        request_id="storage475-diagnostics",
        agent_id="authless.local",
        input={"test": "mock-only"},
    ))

    assert len(calls) >= 1
    assert result.status != RuntimeStatus.COMPLETED
    assert result.error.code == "INVALID_JSON"
    details = result.error.details
    assert details["finish_reason"] == "stop"
    assert details["http_status"] == 200
    assert details["provider_request_id"] == "req-475-mock"
    assert details["content_length_bytes"] == len(content.encode())
    assert details["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert details["starts_with_markdown_fence"] is True
    assert details["json_error_line"] == 1
    assert details["json_error_column"] == 1
    assert details["json_error_position"] == 0
    assert details["prompt_tokens"] == 123
    assert details["completion_tokens"] == 33
    assert details["max_tokens"] == "NOT_RECORDED"

    # The same Runtime failure metadata is durable, and the text stays absent.
    db_dump = raw_database_dump(store)
    trace_text = trace.read_text(encoding="utf-8")
    console = capsys.readouterr().out
    assert "content_sha256" in db_dump and "json_error_position" in db_dump
    assert "invalid_json" in trace_text and "json_error_line" in trace_text
    assert "invalid_json" in console
    for private in (marker, content, "Authorization", "Bearer"):
        assert private not in db_dump
        assert private not in trace_text
        assert private not in console


def test_storage475_finish_reason_length_still_maps_to_output_truncated(
    tmp_path: Path, monkeypatch,
) -> None:
    class LimitedResponse(_FakeResponse):
        status = 200
        headers = {}

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: LimitedResponse({
            "choices": [{"message": {"content": '{"ok":'}, "finish_reason": "LENGTH"}],
            "usage": {"completion_tokens": 512},
        }),
    )
    trace = tmp_path / "provider-trace.jsonl"
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE_FILE", str(trace))
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Only JSON", output_schema=SimpleResult, response_shape="json_object",
    )
    with pytest.raises(RuntimeStepError) as err:
        adapter({"value": 1}, {"runtime": {"provider_call_seq": 1, "provider_config": {
            "base_url": "http://127.0.0.1:9001/v1",
            "model": "mock-only",
            "max_tokens": 512,
        }}})
    assert err.value.code == "OUTPUT_TRUNCATED"
    assert err.value.details["finish_reason"] == "length"
    assert err.value.details["completion_tokens"] == 512
    assert err.value.details["max_tokens"] == 512
    assert "json_error_position" not in err.value.details
    assert "output_truncated" in trace.read_text(encoding="utf-8")


def test_storage475_valid_json_and_missing_metadata_remain_safe(tmp_path: Path, monkeypatch) -> None:
    responses = iter(['{"ok":true}', "not-json"])
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda request, timeout: _FakeResponse({
            "choices": [{"message": {"content": next(responses)}}],
        }),
    )
    trace = tmp_path / "provider-trace.jsonl"
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE_FILE", str(trace))
    monkeypatch.delenv("RUNTIME_PROVIDER_TRACE", raising=False)
    monkeypatch.delenv("RUNTIME_PROVIDER_DIAGNOSTICS", raising=False)
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Only JSON", output_schema=SimpleResult, response_shape="json_object",
    )
    ctx = {"runtime": {"provider_call_seq": 1, "provider_config": {
        "base_url": "http://127.0.0.1:9001/v1", "model": "mock-only",
    }}}
    assert adapter({"value": 1}, ctx) == {"ok": True}
    assert not trace.exists()
    with pytest.raises(RuntimeStepError) as err:
        adapter({"value": 1}, ctx)
    assert err.value.code == "INVALID_JSON"
    for field in ("finish_reason", "http_status", "provider_request_id",
                  "prompt_tokens", "completion_tokens", "max_tokens"):
        assert err.value.details[field] == "NOT_RECORDED"
    assert "not-json" not in trace.read_text(encoding="utf-8")


def test_storage475_json_schema_fail_closed_not_relabelled(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda request, timeout: _FakeResponse({
            "choices": [{"message": {"content": '{"ok":"not a boolean"}'}, "finish_reason": "stop"}],
        }),
    )
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Only JSON", output_schema=SimpleResult, response_shape="json_object",
    )
    with pytest.raises(RuntimeStepError) as err:
        adapter({"value": 1}, {"runtime": {"provider_config": {
            "base_url": "http://127.0.0.1:9001/v1", "model": "mock-only",
        }}})
    assert err.value.code == "PROVIDER_SCHEMA_INVALID"


@pytest.mark.parametrize("content", [
    ' \\n' + chr(96) * 3 + 'json\\n{"ok":true}\\n' + chr(96) * 3 + ' \\n',
    chr(96) * 3 + 'JSON\\n{"ok":true}\\n' + chr(96) * 3,
    chr(96) * 3 + '\\n{"ok":true}\\n' + chr(96) * 3,
])
def test_storage475_reuses_complete_json_fence_only_for_object_agents(
    content: str, monkeypatch, tmp_path: Path,
) -> None:
    response_text = bytes(content, "utf-8").decode("unicode_escape")
    class SafeResponse(_FakeResponse):
        status = 200
        headers = {}

    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: SafeResponse({
            "choices": [{"message": {"content": response_text}, "finish_reason": "stop"}],
        }),
    )
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("RUNTIME_PROVIDER_TRACE_FILE", str(trace))
    monkeypatch.delenv("RUNTIME_PROVIDER_TRACE", raising=False)
    monkeypatch.delenv("RUNTIME_PROVIDER_DIAGNOSTICS", raising=False)
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Strict JSON", output_schema=SimpleResult, response_shape="json_object",
    )
    result = adapter({"v": 1}, {"runtime": {
        "provider_call_seq": 1,
        "provider_config": {"base_url": "http://127.0.0.1:9001/v1", "model": "mock-only"},
    }})
    assert result == {"ok": True}
    lines = trace.read_text(encoding="utf-8")
    assert "json_fence_normalized" in lines
    assert response_text not in lines
    assert "Bearer" not in lines


@pytest.mark.parametrize("content", [
    chr(96) * 3 + 'json\\n{"ok":true}',
    chr(96) * 3 + 'json\\n{"ok":true}\\n' + chr(96) * 3 + '\\nextra',
    'note\\n' + chr(96) * 3 + 'json\\n{"ok":true}\\n' + chr(96) * 3,
    chr(96) * 3 + 'json\\n{"ok":true}\\n' + chr(96) * 3
        + '\\n' + chr(96) * 3 + 'json\\n{"ok":true}\\n' + chr(96) * 3,
    chr(96) * 3 + 'json\\n{"ok":\\n' + chr(96) * 3,
    chr(96) * 3 + 'python\\n{"ok":true}\\n' + chr(96) * 3,
    '{"ok":true} {"ok":false}',
])
def test_storage475_rejects_incomplete_or_ambiguous_json_fences(
    content: str, monkeypatch,
) -> None:
    response_text = bytes(content, "utf-8").decode("unicode_escape")
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: _FakeResponse({
            "choices": [{"message": {"content": response_text}, "finish_reason": "stop"}],
        }),
    )
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Strict JSON", output_schema=SimpleResult, response_shape="json_object",
    )
    with pytest.raises(RuntimeStepError) as error:
        adapter({"v": 1}, {"runtime": {
            "provider_config": {"base_url": "http://127.0.0.1:9001/v1", "model": "mock-only"},
        }})
    assert error.value.code == "INVALID_JSON"


def test_storage475_complete_fence_remains_schema_fail_closed(monkeypatch) -> None:
    fence = chr(96) * 3
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: _FakeResponse({
            "choices": [{"message": {"content": fence + "json" + chr(10) + '{"ok":"nonsense"}' + chr(10) + fence},
                         "finish_reason": "stop"}],
        }),
    )
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Strict JSON", output_schema=SimpleResult, response_shape="json_object",
    )
    with pytest.raises(RuntimeStepError) as error:
        adapter({"v": 1}, {"runtime": {
            "provider_config": {"base_url": "http://127.0.0.1:9001/v1", "model": "mock-only"},
        }})
    assert error.value.code == "PROVIDER_SCHEMA_INVALID"


def test_storage475_fence_not_accepted_for_array_response_shape(monkeypatch) -> None:
    fence = chr(96) * 3
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: _FakeResponse({
            "choices": [{"message": {"content": fence + 'json\\n[1,2]\\n' + fence},
                         "finish_reason": "stop"}],
        }),
    )
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Strict JSON", output_schema=None, response_shape="json_array",
    )
    with pytest.raises(RuntimeStepError) as error:
        adapter({"v": 1}, {"runtime": {
            "provider_config": {"base_url": "http://127.0.0.1:9001/v1", "model": "mock-only"},
        }})
    assert error.value.code == "INVALID_JSON"


def _hardware_stage_b_runtime_for_json_gate(tmp_path: Path):
    """Bind the *real* Hardware Stage B agent/prompt/schema; use fake HTTP only."""
    from services.hardware_case_r1_runtime import HARDWARE_R1_STAGE_B_SCHEMA

    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles={
            "active_model": "offline_model",
            "models": {
                "offline_model": {
                    "provider": "openai_compatible",
                    "base_url": "http://127.0.0.1:9001/v1",
                    "model": "offline-only",
                    "max_tokens": 8192,
                }
            },
        },
        schemas={"HardwareCaseR1ReusableKnowledgeV13": HARDWARE_R1_STAGE_B_SCHEMA},
        environ={},
    )
    engine = ConfiguredAgentRuntime(
        SqliteTaskStore(tmp_path / "hardware-stage-b.db"),
        config_loader=loader,
    )
    config = engine.load_agent(
        ROOT / "config/runtime/agents/hardware_case.r1_reuse_derive.yaml"
    )
    return engine, config


def test_hardware_stage_b_inherits_runtime_json_recovery(tmp_path: Path, monkeypatch) -> None:
    """A complete fenced JSON block recovers under the unchanged Stage B schema."""
    engine, resolved = _hardware_stage_b_runtime_for_json_gate(tmp_path)
    assert resolved.execution_policy.model_policy["max_tokens"] == 3072
    assert resolved.execution_policy.validation_retry.max_attempts == 2
    seen: list[dict] = []
    field = {"value": None, "status": "MISSING",
             "derived_from_fields": [], "evidence_block_ids": []}
    expected = {"reusable_knowledge_candidate": {
        key: dict(field)
        for key in ("engineering_rule", "design_constraint", "diagnostic_clue",
                    "verification_method", "applicability", "conclusion")
    }}
    fenced = "&#96;&#96;&#96;json\n" + json.dumps(expected) + "\n&#96;&#96;&#96;"
    fenced = fenced.replace("&#96;", chr(96))

    def fake_urlopen(request, timeout):
        seen.append(json.loads(request.data))
        return _FakeResponse({"choices": [
            {"message": {"content": fenced}, "finish_reason": "stop"}
        ]})

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    result = engine.invoke(AgentRequest(
        request_id="hardware-stage-b-fenced-json",
        agent_id="hardware_case.r1_reuse_derive",
        input={"synthetic": True},
    ))
    assert result.status == RuntimeStatus.COMPLETED
    assert result.data == expected
    assert result.execution.provider_calls == 1
    assert seen[0]["max_tokens"] == 3072  # Stage config overrides model 8192.


def test_hardware_stage_b_incomplete_json_remains_blocked(
    tmp_path: Path, monkeypatch,
) -> None:
    """No repairing missing fields, no bypass of Runtime retry budget."""
    engine, _ = _hardware_stage_b_runtime_for_json_gate(tmp_path)
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data)["max_tokens"])
        return _FakeResponse({"choices": [
            {"message": {"content": '{"reusable_knowledge_candidate":'},
             "finish_reason": "stop"}
        ]})

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    result = engine.invoke(AgentRequest(
        request_id="hardware-stage-b-incomplete-json",
        agent_id="hardware_case.r1_reuse_derive",
        input={"synthetic": True},
    ))
    assert result.status != RuntimeStatus.COMPLETED
    assert result.error is not None
    assert result.error.code == "INVALID_JSON"
    assert result.error.details["finish_reason"] == "stop"
    assert result.execution.provider_calls == 2
    assert calls == [3072, 3072]
