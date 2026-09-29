from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from products.storage_rc1.storage_life import product_api, runtime_bridge
from quality_knowledge.web.overall_runtime_control import (
    ConfigRevisionRequest,
    OverallRuntimeControlPlane,
    create_overall_runtime_control_router,
)
from quality_knowledge.web.overall_shell import PRODUCT_AREAS
from scripts.overall_r2_windows_start import _active_runtime_model_config


ROOT = Path(__file__).resolve().parents[1]

_RUNTIME_CONFIG_ENV_KEYS = (
    "MAJOR_MODEL_CONFIG",
    "HARDWARE_CASE_MODEL_CONFIG",
    "STORAGE_MODEL_CONFIG",
    "OVERALL_RUNTIME_MODEL_CONFIG",
    "STORAGE_MODEL_CONFIG_SOURCE",
)


@pytest.fixture(autouse=True)
def _isolate_w5_runtime_config_environment():
    snapshot = {
        name: os.environ.get(name)
        for name in _RUNTIME_CONFIG_ENV_KEYS
    }
    yield
    for name, value in snapshot.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class FakeResponse:
    status = 200
    headers = {
        "Content-Type": "application/json",
        "x-request-id": "r2-w5-connectivity",
    }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"ok":true}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 3,
                    "total_tokens": 6,
                },
            }
        ).encode("utf-8")


def _control(
    tmp_path: Path,
    monkeypatch,
) -> OverallRuntimeControlPlane:
    for name in (
        "MAJOR_MODEL_CONFIG",
        "HARDWARE_CASE_MODEL_CONFIG",
        "STORAGE_MODEL_CONFIG",
        "OVERALL_RUNTIME_MODEL_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)
    return OverallRuntimeControlPlane(
        project_root=ROOT,
        state_root=tmp_path / "runtime-control",
        runtime_dbs={
            "QUALITY_ISSUE": tmp_path / "existing-runtime.sqlite3",
        },
    )


def test_w5_effective_config_reuses_agent_config_and_hides_secret_values(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "http://provider.invalid/v1",
    )
    monkeypatch.setenv(
        "DASHSCOPE_API_KEY",
        "r2-w5-secret",
    )
    control = _control(tmp_path, monkeypatch)
    effective = control.effective_config()

    assert effective["contract"] == "AGENT-CONFIG-001"
    assert effective["active_model"] == "qwen_prod"
    assert effective["models"]["qwen_prod"]["api_key_env"] == (
        "DASHSCOPE_API_KEY"
    )
    assert effective["models"]["qwen_prod"]["api_key_present"] is True
    serialized = json.dumps(effective, ensure_ascii=False)
    assert "r2-w5-secret" not in serialized
    assert "__REPLACE_LOCALLY_FOR_TEST_ONLY__" not in serialized

    bindings = effective["formal_bindings"]
    assert bindings["boundary"]["provider_execution"] == "RUNTIME"
    assert bindings["boundary"]["retry_policy"] == "RUNTIME"
    assert bindings["boundary"]["secret_resolution"] == "RUNTIME"
    assert bindings["boundary"]["execution_snapshot"] == "RUNTIME"
    assert bindings["boundary"]["direct_provider_calls"] is False
    assert bindings["boundary"]["domain_retry_loops"] is False
    assert bindings["boundary"]["secret_persistence"] is False
    assert {
        item["agent_id"]
        for item in bindings["items"]
    } == {
        "major_issue.v2.occurrence",
        "hardware_case.structure",
        "reverse_quality.analysis",
        "storage.emmc.parameter_extract",
        "knowledge.production.extract",
    }


def test_w5_revision_activate_audit_and_rollback_use_external_state_root(
    tmp_path: Path,
    monkeypatch,
):
    control = _control(tmp_path, monkeypatch)
    revision = control.create_revision(
        ConfigRevisionRequest(
            active_model="local",
            models={
                "local": {
                    "provider": "openai_compatible",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "local-model",
                    "temperature": 0,
                    "max_tokens": 512,
                }
            },
            note="R2 W5 local smoke",
        ),
        actor="pytest",
    )

    revision_path = Path(revision["path"])
    assert revision_path.is_file()
    assert control.state_root in revision_path.parents
    assert ROOT not in revision_path.parents

    active = control.activate_revision(
        revision["revision_id"],
        actor="pytest",
    )
    assert active["apply_mode"] == "NEXT_RUNTIME_BUILD_OR_RESTART"
    assert active["environment_activation"] == "NEXT_OVERALL_START"
    assert control.effective_model_config_path() == revision_path
    for name in (
        "MAJOR_MODEL_CONFIG",
        "HARDWARE_CASE_MODEL_CONFIG",
        "STORAGE_MODEL_CONFIG",
    ):
        assert not __import__("os").environ.get(name)

    rolled = control.rollback(
        "CANONICAL",
        actor="pytest",
    )
    assert rolled["revision_id"] == "CANONICAL"
    assert control.effective_model_config_path() == (
        control.canonical_model_config
    )
    for name in (
        "MAJOR_MODEL_CONFIG",
        "HARDWARE_CASE_MODEL_CONFIG",
        "STORAGE_MODEL_CONFIG",
    ):
        assert not __import__("os").environ.get(name)

    history = control.list_revisions()
    assert history["items"][0]["revision_id"] == revision["revision_id"]
    assert [
        item["action"]
        for item in history["audit"]
    ] == [
        "CREATE_REVISION",
        "ACTIVATE",
        "ROLLBACK",
    ]


def test_w5_revision_rejects_literal_secret(
    tmp_path: Path,
    monkeypatch,
):
    control = _control(tmp_path, monkeypatch)
    with pytest.raises(
        ValueError,
        match="LITERAL_SECRET_FORBIDDEN",
    ):
        control.create_revision(
            ConfigRevisionRequest(
                active_model="bad",
                models={
                    "bad": {
                        "provider": "openai_compatible",
                        "base_url": "http://provider.invalid/v1",
                        "api_key": "must-not-persist",
                        "model": "bad-model",
                    }
                },
            ),
            actor="pytest",
        )
    assert not (control.state_root / "active.json").exists()
    assert "must-not-persist" not in "".join(
        path.read_text(encoding="utf-8")
        for path in control.state_root.rglob("*")
        if path.is_file()
    )


def test_w5_formal_agent_smoke_is_binding_based_and_no_legacy_fallback(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "http://provider.invalid/v1",
    )
    monkeypatch.setenv(
        "DASHSCOPE_API_KEY",
        "test-only",
    )
    control = _control(tmp_path, monkeypatch)
    result = control.binding_smoke(
        "storage.emmc.parameter_extract"
    )

    assert result["status"] == "PASS", result
    assert result["execution_owner"] == "UNIFIED_RUNTIME"
    assert result["silent_legacy_fallback"] is False
    assert result["secret_ref"] == "DASHSCOPE_API_KEY"
    assert result["endpoint_ref"] == "DASHSCOPE_BASE_URL"

    with pytest.raises(
        KeyError,
        match="FORMAL_AGENT_NOT_BOUND",
    ):
        control.binding_smoke("legacy.not.formally.bound")


def test_w5_connectivity_test_executes_through_unified_runtime_only(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "http://provider.invalid/v1",
    )
    monkeypatch.setenv(
        "DASHSCOPE_API_KEY",
        "test-only",
    )
    monkeypatch.setattr(
        "runtime.providers.openai_compatible.urlopen",
        lambda _request, timeout: FakeResponse(),
    )
    control = _control(tmp_path, monkeypatch)

    result = control.connectivity_test()

    assert result["status"] == "PASS"
    assert result["provider_calls"] == 1
    assert result["runtime_owned"] is True
    assert result["probe_stack"] == "UNIFIED_RUNTIME"
    assert Path(result["runtime_db"]) == (
        tmp_path / "existing-runtime.sqlite3"
    ).resolve()
    assert result["second_trace_store"] is False
    assert (
        tmp_path / "existing-runtime.sqlite3"
    ).is_file()
    assert not (
        control.diagnostic_root / "runtime.sqlite3"
    ).exists()


def _seed_runtime_trace(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE runtime_task(
                task_id TEXT PRIMARY KEY,
                request_id TEXT,
                status TEXT,
                record_json TEXT,
                error_json TEXT
            );
            CREATE TABLE runtime_run(
                run_id TEXT PRIMARY KEY,
                task_id TEXT,
                status TEXT,
                record_json TEXT
            );
            CREATE TABLE runtime_step_run(
                step_run_id TEXT PRIMARY KEY,
                run_id TEXT,
                step_id TEXT,
                status TEXT,
                record_json TEXT
            );
            CREATE TABLE runtime_attempt(
                attempt_id TEXT PRIMARY KEY,
                step_run_id TEXT,
                record_json TEXT
            );
            """
        )
        task = {
            "task_id": "task-kp-1",
            "request_id": "knowledge-extract:source:v1",
            "business_domain": "KNOWLEDGE_PRODUCTION",
            "status": "FAILED",
            "metadata": {
                "business_domain": "KNOWLEDGE_PRODUCTION",
            },
        }
        error = {
            "code": "PROVIDER_TIMEOUT",
            "category": "TRANSPORT",
            "message": "timeout",
            "retryable": True,
        }
        run = {
            "run_id": "run-kp-1",
            "task_id": "task-kp-1",
            "status": "FAILED",
            "resume_of_run_id": None,
        }
        step = {
            "step_run_id": "step-kp-1",
            "run_id": "run-kp-1",
            "step_id": "extract",
            "agent_id": "knowledge.production.extract",
            "status": "FAILED",
            "attempt_count": 2,
            "error": error,
        }
        attempt = {
            "attempt_id": "attempt-kp-1",
            "step_run_id": "step-kp-1",
            "status": "FAILED",
            "step_attempt_no": 1,
            "validation_cycle_no": 1,
            "transport_attempt_no": 2,
            "provider_call_seq": 2,
            "error": error,
            "execution_metrics": {
                "provider_call_seq": 2,
                "retry_budget_limit": 2,
                "retry_budget_consumed": 2,
                "retry_budget_exhausted": True,
            },
        }
        con.execute(
            "INSERT INTO runtime_task VALUES (?,?,?,?,?)",
            (
                task["task_id"],
                task["request_id"],
                task["status"],
                json.dumps(task),
                json.dumps(error),
            ),
        )
        con.execute(
            "INSERT INTO runtime_run VALUES (?,?,?,?)",
            (
                run["run_id"],
                run["task_id"],
                run["status"],
                json.dumps(run),
            ),
        )
        con.execute(
            "INSERT INTO runtime_step_run VALUES (?,?,?,?,?)",
            (
                step["step_run_id"],
                step["run_id"],
                step["step_id"],
                step["status"],
                json.dumps(step),
            ),
        )
        con.execute(
            "INSERT INTO runtime_attempt VALUES (?,?,?)",
            (
                attempt["attempt_id"],
                attempt["step_run_id"],
                json.dumps(attempt),
            ),
        )


def test_w5_runtime_diagnostics_projects_existing_trace_and_kp_failure(
    tmp_path: Path,
    monkeypatch,
):
    control = _control(tmp_path, monkeypatch)
    runtime_db = tmp_path / "existing-runtime.sqlite3"
    _seed_runtime_trace(runtime_db)
    control.register_runtime_db(
        "KNOWLEDGE",
        runtime_db,
    )

    diagnostics = control.diagnostics()
    store = diagnostics["stores"][0]
    assert diagnostics["trace_owner"] == "UNIFIED_RUNTIME"
    assert store["status"] == "READY"
    attempt = (
        store["tasks"][0]["runs"][0]["steps"][0][
            "attempts"
        ][0]
    )
    assert attempt["provider_call_seq"] == 2
    assert (
        attempt["execution_metrics"][
            "retry_budget_exhausted"
        ]
        is True
    )

    failures = control.knowledge_failures()
    assert len(failures["items"]) == 1
    assert failures["items"][0]["task"]["status"] == "FAILED"
    assert (
        failures["items"][0]["error"]["code"]
        == "PROVIDER_TIMEOUT"
    )



def test_w5_windows_restart_reuses_active_external_revision(
    tmp_path: Path,
    monkeypatch,
):
    control = _control(tmp_path, monkeypatch)
    revision = control.create_revision(
        ConfigRevisionRequest(
            active_model="local",
            models={
                "local": {
                    "provider": "openai_compatible",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "local-model",
                    "temperature": 0,
                    "max_tokens": 512,
                }
            },
            note="restart-persistence",
        ),
        actor="pytest",
    )
    control.activate_revision(
        revision["revision_id"],
        actor="pytest",
    )

    resolved = _active_runtime_model_config(
        ROOT,
        control.state_root,
    )
    assert resolved == Path(revision["path"]).resolve()

    outside = tmp_path / "outside.yaml"
    outside.write_text(
        "active_model: x\nmodels: {}\n",
        encoding="utf-8",
    )
    control.active_path.write_text(
        json.dumps(
            {
                "revision_id": "tampered",
                "path": str(outside),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match="OVERALL_RUNTIME_ACTIVE_REVISION_INVALID",
    ):
        _active_runtime_model_config(
            ROOT,
            control.state_root,
        )


def test_w5_common_routes_pages_navigation_and_storage_deep_links(
    tmp_path: Path,
    monkeypatch,
):
    control = _control(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(
        create_overall_runtime_control_router(control)
    )
    client = TestClient(app)

    assert (
        client.get("/api/v2/system/agent-config").status_code
        == 200
    )
    assert (
        client.get(
            "/api/v2/system/runtime-diagnostics"
        ).status_code
        == 200
    )
    agent_page = client.get("/p0/system/agent-config")
    runtime_page = client.get(
        "/p0/system/runtime-diagnostics"
    )
    assert agent_page.status_code == 200
    assert "Agent 配置" in agent_page.text
    assert runtime_page.status_code == 200
    assert "Runtime Diagnostics" in runtime_page.text

    management = next(
        item
        for item in PRODUCT_AREAS
        if item["area_id"] == "management"
    )
    paths = {
        item["path"]
        for item in management["capabilities"]
    }
    assert "/p0/system/agent-config" in paths
    assert "/p0/system/runtime-diagnostics" in paths

    monkeypatch.setattr(
        runtime_bridge,
        "status",
        lambda: {
            "configured": False,
            "runtime_expected_commit": "runtime-sha",
        },
    )
    monkeypatch.setattr(
        runtime_bridge,
        "last_executions",
        lambda: [],
    )
    storage = product_api.provider_operability()
    assert storage["management_deep_links"] == {
        "agent_config": {
            "status": "READY",
            "href": "/p0/system/agent-config",
            "owner": "OVERALL_COMMON_CAPABILITY",
        },
        "runtime_diagnostics": {
            "status": "READY",
            "href": "/p0/system/runtime-diagnostics",
            "owner": "OVERALL_COMMON_CAPABILITY",
        },
    }
