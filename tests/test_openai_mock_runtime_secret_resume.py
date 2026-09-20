from __future__ import annotations

import json
import sqlite3
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

import pytest

from runtime import AgentConfigLoader, AgentRequest, ConfiguredAgentRuntime, RuntimeStatus, SqliteTaskStore
from runtime.reliability import SimulatedCrash
from tools.openai_mock.server import create_server


SECRET = "OPENAI_MOCK_RUNTIME_SECRET_SHOULD_NOT_PERSIST"


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
    with sqlite3.connect(store.db_path) as conn:
        return "\n".join(conn.iterdump())


def write_agent_config(root: Path) -> Path:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "simple.md").write_text("Return plain text.", encoding="utf-8")
    (root / "model.yaml").write_text(
        """
active_model: primary
models:
  primary:
    provider: openai_compatible
    base_url_env: PROVIDER_BASE_URL
    api_key_env: PROVIDER_API_KEY
    model: mock-gpt
""".strip(),
        encoding="utf-8",
    )
    agent = root / "agent.yaml"
    agent.write_text(
        """
agent_id: mock-secret-agent
model_ref: primary
prompt:
  ref: prompts/simple.md
output_schema:
  ref: Result
execution:
  budget:
    max_provider_calls_per_step: 2
""".strip(),
        encoding="utf-8",
    )
    return agent


def build_runtime(
    root: Path,
    store: SqliteTaskStore,
    *,
    base_url: str,
    fault_injector=None,
):
    agent_path = write_agent_config(root)
    loader = AgentConfigLoader(
        root=root,
        model_profiles="model.yaml",
        schemas={"Result": {"type": "object"}},
        environ={
            "PROVIDER_BASE_URL": base_url,
            "PROVIDER_API_KEY": SECRET,
        },
    )
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=loader,
        fault_injector=fault_injector,
    )

    def real_http_handler(payload, context):
        provider = context["runtime"]["provider_config"]
        api_key = provider["api_key"]
        url = str(provider["base_url"]).rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": provider["model"],
                "messages": [{"role": "user", "content": str(payload)}],
            }
        ).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=2) as response:
            raw = json.loads(response.read().decode())
        return {
            "content": raw["choices"][0]["message"]["content"],
            # Deliberately return the injected secret to verify the Runtime
            # boundary redacts it before persistence.
            "provider_secret": api_key,
        }

    runtime.load_agent(agent_path, real_http_handler)
    return runtime


def test_rt_mock_014_secret_never_persists_across_runtime_and_mock(tmp_path):
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(host, port, "secret-safe")
        store = SqliteTaskStore(tmp_path / "secret.db")
        runtime = build_runtime(tmp_path / "secret-app", store, base_url=base_url)

        result = runtime.invoke(
            AgentRequest(
                request_id="rt-mock-014-secret",
                agent_id="mock-secret-agent",
                input={"case": "secret"},
                context={"provider_api_key": SECRET},
                metadata={"runtime_secret": SECRET},
            )
        )

        assert result.status == RuntimeStatus.COMPLETED
        assert result.data["content"] == "secret-safe"
        assert result.data["provider_secret"] == "[REDACTED]"
        assert SECRET not in raw_database_dump(store)

        history = request_history(host, port)
        assert len(history) == 1
        encoded = json.dumps(history)
        assert SECRET not in encoded
        assert history[0]["authorization"] == {"present": True, "scheme": "Bearer"}
        assert history[0]["headers"]["Authorization"] == "[REDACTED]"


def test_rt_mock_015_crash_resume_reresolves_secret_and_replays_uncommitted_http(
    tmp_path,
):
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(host, port, "resume-safe")
        store = SqliteTaskStore(tmp_path / "resume.db")
        crash_once = {"armed": True}

        def fault(point: str) -> None:
            if crash_once["armed"] and point == "after_provider_return_before_commit":
                crash_once["armed"] = False
                raise SimulatedCrash(point)

        runtime = build_runtime(
            tmp_path / "resume-app",
            store,
            base_url=base_url,
            fault_injector=fault,
        )
        request = AgentRequest(
            request_id="rt-mock-015-resume",
            agent_id="mock-secret-agent",
            input={"case": "resume"},
            context={"provider_api_key": SECRET},
            metadata={"runtime_secret": SECRET},
        )

        with pytest.raises(SimulatedCrash):
            runtime.invoke(request)

        task = store.get_task_by_request_id(request.request_id)
        assert task is not None
        assert counters(host, port)["default"] == 1
        assert SECRET not in raw_database_dump(store)
        assert len(store.list_committed_execution_keys(task.task_id)) == 0

        resumed = build_runtime(
            tmp_path / "resume-app",
            store,
            base_url=base_url,
        )
        handle = resumed.resume(task.task_id)

        assert handle.status == RuntimeStatus.COMPLETED
        assert counters(host, port)["default"] == 2
        assert SECRET not in raw_database_dump(store)
        assert len(store.list_committed_execution_keys(task.task_id)) == 1

        runs = store.list_runs(task.task_id)
        assert len(runs) == 2
        assert runs[1].resume_of_run_id == runs[0].run_id

        encoded_history = json.dumps(request_history(host, port))
        assert SECRET not in encoded_history
