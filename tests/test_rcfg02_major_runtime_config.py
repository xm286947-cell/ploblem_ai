from __future__ import annotations

import inspect
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from urllib.request import Request, urlopen

import pytest

from quality_knowledge.models.analysis_v2 import OccurrenceAnalysisV2DTO
from quality_knowledge.p0.stage_runner import RuntimeConfiguredV2StageRunner
from quality_knowledge.services.v2_analysis_service import V2AnalysisService
from quality_knowledge.web.p0_app import create_p0_app
from runtime import AgentConfigLoader, ConfiguredAgentRuntime, SqliteTaskStore
from tools.openai_mock.server import create_server

from test_quality_capability_v2_analysis_p0 import FakeStageRunner, make_repository


ROOT = Path(__file__).resolve().parents[1]
SECRET = "RCFG02_SECRET_MUST_NOT_PERSIST"


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


def request_history(host: str, port: int) -> list[dict]:
    with urlopen(f"http://{host}:{port}/__mock__/requests", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def occurrence_payload() -> dict:
    return FakeStageRunner().run_stage(
        stage="occurrence",
        context=SimpleNamespace(analysis_set_id="mock"),
    )


def runtime_for(tmp_path: Path, base_url: str) -> tuple[SqliteTaskStore, ConfiguredAgentRuntime]:
    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles={
            "active_model": "qwen_prod",
            "models": {
                "qwen_prod": {
                    "provider": "openai_compatible",
                    "base_url": base_url,
                    "api_key": SECRET,
                    "model": "qwen3.8-max",
                    "temperature": 0,
                    "max_tokens": 8192,
                }
            },
        },
        schemas={"OccurrenceAnalysisV2DTO": OccurrenceAnalysisV2DTO},
        environ={},
    )
    store = SqliteTaskStore(tmp_path / "runtime.db")
    return store, ConfiguredAgentRuntime(store, config_loader=loader)


def raw_database_dump(store: SqliteTaskStore) -> str:
    with sqlite3.connect(store.db_path) as connection:
        return "\n".join(connection.iterdump())


def test_rcfg02_major_occurrence_mock_e2e_uses_canonical_runtime(tmp_path):
    repository = make_repository(tmp_path)
    fallback = FakeStageRunner()

    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(host, port, occurrence_payload())
        store, runtime = runtime_for(tmp_path, base_url)
        runner = RuntimeConfiguredV2StageRunner(
            repository,
            runtime=runtime,
            fallback_runner=fallback,
        )

        result = V2AnalysisService(repository, runner).run("K-V2-1")

        assert result.status == "COMPLETED"
        assert result.occurrence is not None
        assert [stage for stage, _ in fallback.calls] == [
            "escape",
            "recurrence",
            "capability_gap",
        ]
        assert counters(host, port)["default"] == 1

        request_id = (
            f"major-issue-occurrence:{result.analysis_set_id}:{result.input_hash}"
        )
        task = store.get_task_by_request_id(request_id)
        assert task is not None
        assert store.count_task_provider_calls(task.task_id) == 1

        snapshot = store.get_execution_snapshot(task.execution_snapshot_id)
        serialized = snapshot.model_dump_json()
        assert "major_issue.v2.occurrence" in serialized
        assert "qwen3.8-max" in serialized
        assert "PROMPT-P0-V2-OCCURRENCE-V1" in serialized
        assert runner.resolved.config_hash in serialized
        assert SECRET not in serialized
        assert SECRET not in raw_database_dump(store)

        assert snapshot.model_policy["max_tokens"] == 4096
        assert snapshot.model_policy["temperature"] == 0
        assert snapshot.prompt_hash == runner.resolved.prompt.content_hash
        assert snapshot.output_schema_ref == "OccurrenceAnalysisV2DTO"
        assert snapshot.output_schema_version == "2.0.0"

        history = request_history(host, port)
        assert len(history) == 1
        assert history[0]["model"] == "qwen3.8-max"
        assert history[0]["path"] == "/v1/chat/completions"
        assert history[0]["authorization"] == {
            "present": True,
            "scheme": "Bearer",
        }
        assert history[0]["headers"]["Authorization"] == "[REDACTED]"


def test_rcfg02_major_occurrence_retry_is_runtime_owned(tmp_path):
    repository = make_repository(tmp_path)
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        configure(
            host,
            port,
            occurrence_payload(),
            {"fail_first_n": 1, "fail_status": 429, "retry_after": "0"},
        )
        store, runtime = runtime_for(tmp_path, base_url)
        runner = RuntimeConfiguredV2StageRunner(
            repository,
            runtime=runtime,
            fallback_runner=FakeStageRunner(),
        )

        result = V2AnalysisService(repository, runner).run("K-V2-1")

        assert result.status == "COMPLETED"
        assert counters(host, port)["default"] == 2
        request_id = (
            f"major-issue-occurrence:{result.analysis_set_id}:{result.input_hash}"
        )
        task = store.get_task_by_request_id(request_id)
        assert store.count_task_provider_calls(task.task_id) == 2


def test_rcfg02_default_p0_app_wires_occurrence_to_unified_runtime(
    tmp_path,
    monkeypatch,
):
    repository = make_repository(tmp_path)
    monkeypatch.setenv("acca", "legacy-fallback-secret")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)

    app = create_p0_app(repository.db_path)

    assert isinstance(app.state.v2_stage_runner, RuntimeConfiguredV2StageRunner)
    assert app.state.analysis_runtime_status["ready"] is True
    assert app.state.analysis_runtime_status["source"] == (
        "UNIFIED_RUNTIME+MODEL_CONFIG"
    )
    assert app.state.analysis_runtime_status["migrated_agent"] == (
        "major_issue.v2.occurrence"
    )
    resolved = app.state.v2_stage_runner.resolved
    assert resolved.definition.agent_id == "major_issue.v2.occurrence"
    assert resolved.provider.profile_ref == "qwen_prod"
    assert resolved.provider.api_key_env == "DASHSCOPE_API_KEY"
    assert SECRET not in resolved.model_dump_json()


def test_rcfg02_external_model_config_works_without_provider_env(
    tmp_path,
    monkeypatch,
):
    repository = make_repository(tmp_path)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("MAJOR_MODEL_CONFIG", raising=False)

    with running_server() as (host, port):
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: RCFG02_LOCAL_MODEL_SECRET
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )
        configure(host, port, occurrence_payload())

        runner = RuntimeConfiguredV2StageRunner.from_project(
            repository,
            root=ROOT,
            runtime_db_path=tmp_path / "runtime-local.db",
            fallback_runner=FakeStageRunner(),
            model_config_path=model_config,
        )
        result = V2AnalysisService(repository, runner).run("K-V2-1")

        assert result.status == "COMPLETED"
        assert counters(host, port)["default"] == 1
        assert runner.model_config_path == model_config.resolve()
        assert runner.resolved.provider.mode == "direct"
        assert runner.resolved.provider.base_url == (
            f"http://{host}:{port}/v1"
        )
        assert runner.resolved.provider.api_key_env is None
        assert "RCFG02_LOCAL_MODEL_SECRET" not in (
            runner.resolved.model_dump_json()
        )

        store = runner.runtime.store
        assert "RCFG02_LOCAL_MODEL_SECRET" not in raw_database_dump(store)


def test_rcfg02_migrated_runner_has_no_business_provider_or_retry_construction():
    source = inspect.getsource(RuntimeConfiguredV2StageRunner)
    assert "OpenAICompatibleClient(" not in source
    assert "RetryPolicy(" not in source
    assert "RetryBudget(" not in source
    assert "ExecutionPolicy(" not in source


@pytest.mark.skipif(
    os.environ.get("MAJOR_ISSUE_REAL_E2E", "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="MAJOR_ISSUE_REAL_E2E opt-in disabled",
)
def test_rcfg02_major_occurrence_real_provider_golden_smoke(tmp_path):
    explicit = os.environ.get("MAJOR_MODEL_CONFIG", "").strip()
    local_config = ROOT / "config/model.local.yaml"
    uses_external_config = bool(explicit) or local_config.is_file()

    if not uses_external_config:
        missing = [
            name
            for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY")
            if not os.environ.get(name)
        ]
        if missing:
            pytest.skip(
                "NOT_RUN_NO_RUNTIME_MODEL_CONFIG: provide "
                "MAJOR_MODEL_CONFIG/config/model.local.yaml or configure "
                + ",".join(missing)
            )

    repository = make_repository(tmp_path)
    model_config = RuntimeConfiguredV2StageRunner.resolve_model_config(ROOT)
    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=model_config,
        schemas={"OccurrenceAnalysisV2DTO": OccurrenceAnalysisV2DTO},
        environ=os.environ,
    )
    store = SqliteTaskStore(tmp_path / "runtime-real.db")
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    runner = RuntimeConfiguredV2StageRunner(
        repository,
        runtime=runtime,
        fallback_runner=FakeStageRunner(),
    )
    runner.model_config_path = model_config

    result = V2AnalysisService(repository, runner).run(
        "K-V2-1",
        {"force": True, "force_nonce": "rcfg02-real-golden"},
    )

    assert result.status == "COMPLETED"
    assert result.occurrence is not None
    assert result.occurrence.mrc.side == "OCCURRENCE"
    request_id = (
        f"major-issue-occurrence:{result.analysis_set_id}:{result.input_hash}"
    )
    task = store.get_task_by_request_id(request_id)
    assert task is not None
    assert 1 <= store.count_task_provider_calls(task.task_id) <= 2
    secret = loader.get_runtime_api_key(
        runner.resolved.config_hash,
        api_key_env=runner.resolved.provider.api_key_env,
    )
    if secret:
        assert secret not in store.get_execution_snapshot(
            task.execution_snapshot_id
        ).model_dump_json()
        assert secret not in raw_database_dump(store)
