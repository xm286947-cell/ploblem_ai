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

from runtime import (
    AgentConfigLoader,
    AgentRequest,
    ConfiguredAgentRuntime,
    ErrorCategory,
    ProviderEndpointResolver,
    RuntimeStatus,
    SqliteTaskStore,
)
from runtime.config.errors import ConfigValidationError, SecretEnvNotFoundError
from runtime.providers import OpenAICompatibleProviderAdapter
from runtime.reliability import RuntimeStepError
from tools.openai_mock.server import create_server


SECRET = "RPC001_SECRET_MUST_NEVER_PERSIST"


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


def configure(host: str, port: int, *, behavior: dict | None = None) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": '{"ok":true}',
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


def request_history(host: str, port: int) -> list[dict]:
    with urlopen(f"http://{host}:{port}/__mock__/requests", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def raw_database_dump(store: SqliteTaskStore) -> str:
    with sqlite3.connect(store.db_path) as connection:
        return "\n".join(connection.iterdump())


def write_config(root: Path, *, auth: bool = True) -> Path:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "simple.md").write_text(
        "Return only strict JSON with boolean field ok.",
        encoding="utf-8",
    )
    auth_line = "    api_key_env: PROVIDER_API_KEY\n" if auth else ""
    (root / "model.yaml").write_text(
        (
            "active_model: primary\n"
            "models:\n"
            "  primary:\n"
            "    provider: openai_compatible\n"
            "    base_url_env: PROVIDER_BASE_URL\n"
            f"{auth_line}"
            "    model: contract-model\n"
            "    temperature: 0.25\n"
            "    max_tokens: 321\n"
        ),
        encoding="utf-8",
    )
    agent = root / "agent.yaml"
    agent.write_text(
        (
            "agent_id: provider.contract\n"
            "model_ref: primary\n"
            "prompt:\n"
            "  ref: prompts/simple.md\n"
            "output_schema:\n"
            "  ref: SimpleResult\n"
            "execution:\n"
            "  retry:\n"
            "    transport_attempts: 2\n"
            "    validation_attempts: 1\n"
            "    step_attempts: 1\n"
            "  budget:\n"
            "    max_provider_calls_per_step: 2\n"
            "metadata:\n"
            "  provider_response_shape: json_object\n"
        ),
        encoding="utf-8",
    )
    return agent


def build_runtime(
    root: Path,
    store: SqliteTaskStore,
    *,
    base_url: str,
    auth: bool = True,
) -> ConfiguredAgentRuntime:
    agent = write_config(root, auth=auth)
    environ = {"PROVIDER_BASE_URL": base_url}
    if auth:
        environ["PROVIDER_API_KEY"] = SECRET
    loader = AgentConfigLoader(
        root=root,
        model_profiles="model.yaml",
        schemas={"SimpleResult": SimpleResult},
        environ=environ,
    )
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    runtime.load_agent(agent)
    return runtime


def invoke(runtime: ConfiguredAgentRuntime, request_id: str):
    return runtime.invoke(
        AgentRequest(
            request_id=request_id,
            agent_id="provider.contract",
            input={"case": request_id},
        )
    )


def load_with_base_url(tmp_path: Path, base_url: str):
    agent = write_config(tmp_path)
    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"SimpleResult": SimpleResult},
        environ={
            "PROVIDER_BASE_URL": base_url,
            "PROVIDER_API_KEY": SECRET,
        },
    )
    return loader.load(agent)


def test_pc01_valid_https_base_url_resolves_correctly() -> None:
    assert (
        ProviderEndpointResolver.chat_completions_url("https://host.example/v1")
        == "https://host.example/v1/chat/completions"
    )


def test_pc02_valid_local_http_base_url_resolves_correctly() -> None:
    assert (
        ProviderEndpointResolver.chat_completions_url("http://127.0.0.1:11434/v1")
        == "http://127.0.0.1:11434/v1/chat/completions"
    )


def test_pc03_trailing_slash_is_canonicalized() -> None:
    assert (
        ProviderEndpointResolver.chat_completions_url("https://host.example/v1/")
        == "https://host.example/v1/chat/completions"
    )


def test_pc04_full_chat_completions_endpoint_is_not_duplicated() -> None:
    url = "https://host.example/v1/chat/completions"
    assert ProviderEndpointResolver.chat_completions_url(url) == url


def test_pc05_empty_base_url_fails_as_config_before_provider_call(tmp_path: Path) -> None:
    with pytest.raises(SecretEnvNotFoundError) as exc_info:
        load_with_base_url(tmp_path, "")
    assert exc_info.value.category == ErrorCategory.CONFIG


def test_pc06_missing_scheme_fails_as_config_before_provider_call(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError) as exc_info:
        load_with_base_url(tmp_path, "host.example/v1")
    assert exc_info.value.category == ErrorCategory.CONFIG
    assert exc_info.value.details["reason"] == "missing_scheme"


def test_pc07_unsupported_scheme_fails_as_config_before_provider_call(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError) as exc_info:
        load_with_base_url(tmp_path, "ftp://host.example/v1")
    assert exc_info.value.category == ErrorCategory.CONFIG
    assert exc_info.value.details["reason"] == "unsupported_scheme"


def test_pc08_malformed_host_fails_as_config_before_provider_call(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError) as exc_info:
        load_with_base_url(tmp_path, "https://:443/v1")
    assert exc_info.value.category == ErrorCategory.CONFIG
    assert exc_info.value.details["reason"] == "missing_host"


def test_pc09_query_and_fragment_are_rejected_before_provider_call(tmp_path: Path) -> None:
    for index, bad in enumerate(
        ("https://host.example/v1?x=1", "https://host.example/v1#frag")
    ):
        root = tmp_path / str(index)
        with pytest.raises(ConfigValidationError) as exc_info:
            load_with_base_url(root, bad)
        assert exc_info.value.category == ErrorCategory.CONFIG
        assert exc_info.value.details["reason"] in {
            "query_not_allowed",
            "fragment_not_allowed",
        }


def test_pc10_mock_proves_post_and_final_path(tmp_path: Path) -> None:
    with running_server() as (host, port):
        configure(host, port)
        runtime = build_runtime(
            tmp_path / "app",
            SqliteTaskStore(tmp_path / "runtime.db"),
            base_url=f"http://{host}:{port}/v1/",
        )
        result = invoke(runtime, "pc10")
        history = request_history(host, port)
        assert result.status == RuntimeStatus.COMPLETED
        assert len(history) == 1
        assert history[0]["method"] == "POST"
        assert history[0]["path"] == "/v1/chat/completions"


def test_pc11_mock_proves_content_type_json(tmp_path: Path) -> None:
    with running_server() as (host, port):
        configure(host, port)
        runtime = build_runtime(
            tmp_path / "app",
            SqliteTaskStore(tmp_path / "runtime.db"),
            base_url=f"http://{host}:{port}/v1",
        )
        assert invoke(runtime, "pc11").status == RuntimeStatus.COMPLETED
        headers = {
            key.lower(): value
            for key, value in request_history(host, port)[0]["headers"].items()
        }
        assert headers["content-type"] == "application/json"


def test_pc12_api_key_mode_proves_bearer_authorization(tmp_path: Path) -> None:
    with running_server() as (host, port):
        configure(host, port)
        runtime = build_runtime(
            tmp_path / "app",
            SqliteTaskStore(tmp_path / "runtime.db"),
            base_url=f"http://{host}:{port}/v1",
            auth=True,
        )
        assert invoke(runtime, "pc12").status == RuntimeStatus.COMPLETED
        history = request_history(host, port)
        assert history[0]["authorization"] == {"present": True, "scheme": "Bearer"}
        assert history[0]["headers"]["Authorization"] == "[REDACTED]"
        assert SECRET not in json.dumps(history)


def test_pc13_auth_none_proves_authorization_absent(tmp_path: Path) -> None:
    with running_server() as (host, port):
        configure(host, port, behavior={"require_auth": False})
        runtime = build_runtime(
            tmp_path / "app",
            SqliteTaskStore(tmp_path / "runtime.db"),
            base_url=f"http://{host}:{port}/v1",
            auth=False,
        )
        assert invoke(runtime, "pc13").status == RuntimeStatus.COMPLETED
        history = request_history(host, port)
        assert history[0]["authorization"] == {"present": False, "scheme": None}
        assert "authorization" not in history[0]["header_names"]


def test_pc14_mock_proves_openai_compatible_body_from_resolved_config(tmp_path: Path) -> None:
    with running_server() as (host, port):
        configure(host, port)
        runtime = build_runtime(
            tmp_path / "app",
            SqliteTaskStore(tmp_path / "runtime.db"),
            base_url=f"http://{host}:{port}/v1",
        )
        assert invoke(runtime, "pc14").status == RuntimeStatus.COMPLETED
        body = request_history(host, port)[0]["body"]
        assert body["model"] == "contract-model"
        assert body["temperature"] == 0.25
        assert body["max_tokens"] == 321
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        assert body["messages"][0]["content"]
        assert json.loads(body["messages"][1]["content"]) == {"case": "pc14"}


def test_pc15_runtime_provider_calls_equals_actual_mock_request_count(tmp_path: Path) -> None:
    with running_server() as (host, port):
        configure(
            host,
            port,
            behavior={"fail_first_n": 1, "fail_status": 429, "retry_after": "0"},
        )
        runtime = build_runtime(
            tmp_path / "app",
            SqliteTaskStore(tmp_path / "runtime.db"),
            base_url=f"http://{host}:{port}/v1",
        )
        result = invoke(runtime, "pc15")
        assert result.status == RuntimeStatus.COMPLETED
        assert result.execution.provider_calls == 2
        assert counters(host, port)["default"] == 2
        assert len(request_history(host, port)) == 2


def test_pc16_secret_persistence_regression_has_zero_raw_hits(tmp_path: Path) -> None:
    with running_server() as (host, port):
        configure(host, port)
        store = SqliteTaskStore(tmp_path / "runtime.db")
        runtime = build_runtime(
            tmp_path / "app",
            store,
            base_url=f"http://{host}:{port}/v1",
        )
        result = invoke(runtime, "pc16")
        assert result.status == RuntimeStatus.COMPLETED
        assert SECRET not in result.model_dump_json()
        assert SECRET not in raw_database_dump(store)
        assert SECRET not in json.dumps(request_history(host, port))


def test_provider_adapter_defense_in_depth_rejects_invalid_url_as_config(monkeypatch) -> None:
    called = {"http": False}

    def fake_urlopen(*_args, **_kwargs):
        called["http"] = True
        raise AssertionError("HTTP must not execute")

    monkeypatch.setattr("runtime.providers.openai_compatible.urlopen", fake_urlopen)
    adapter = OpenAICompatibleProviderAdapter(
        system_prompt="Return JSON.",
        output_schema=SimpleResult,
        response_shape="json_object",
    )
    with pytest.raises(RuntimeStepError) as exc_info:
        adapter(
            {"case": "defense"},
            {
                "runtime": {
                    "provider_config": {
                        "base_url": "missing-scheme.example/v1",
                        "model": "contract-model",
                    }
                }
            },
        )
    assert exc_info.value.category == ErrorCategory.CONFIG
    assert called["http"] is False
