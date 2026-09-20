from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from runtime import (
    AgentConfigLoader,
    AgentRequest,
    ConfiguredAgentRuntime,
    ErrorCategory,
    RuntimeStatus,
    RuntimeStepError,
    SqliteTaskStore,
)
from runtime.contracts import (
    StepDefinition,
    TaskRecord,
    TaskType,
    WorkflowDefinition,
    WorkflowRequest,
)
from runtime.reliability import SimulatedCrash


RUNTIME_SECRET = "RUNTIME_SECRET_REQUEST_FIX_TEST"
BUSINESS_INPUT = {
    "password": "business-password",
    "api_key": "business-api-key",
    "token": "business-token",
    "secret": "business-secret",
}


def _task(
    task_id: str,
    request_id: str,
    task_type: TaskType,
) -> TaskRecord:
    now = datetime.now(timezone.utc)
    return TaskRecord(
        task_id=task_id,
        request_id=request_id,
        request_fingerprint=f"fp-{task_id}",
        task_type=task_type,
        status=RuntimeStatus.QUEUED,
        input_hash=f"input-{task_id}",
        created_at=now,
        updated_at=now,
        metadata={
            "runtime_secret": RUNTIME_SECRET,
            "password": "business-task-password",
        },
    )


def _request_json(store: SqliteTaskStore, task_id: str) -> str:
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT request_json FROM runtime_task WHERE task_id=?",
            (task_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _raw_database_hit_count(store: SqliteTaskStore, value: str) -> int:
    with sqlite3.connect(store.db_path) as conn:
        raw_dump = "\n".join(conn.iterdump())
    return raw_dump.count(value)


def test_t01_to_t04_canonical_request_persistence_omits_runtime_credentials(
    tmp_path,
):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    agent = AgentRequest(
        request_id="request-secret-agent",
        agent_id="storage.test",
        input=BUSINESS_INPUT,
        context={
            "provider_api_key": RUNTIME_SECRET,
            "api_key": "business-context-api-key",
        },
        metadata={
            "runtime_secret": RUNTIME_SECRET,
            "token": "business-metadata-token",
        },
    )
    agent_task, created = store.create_or_get_task(
        _task("task-request-secret-agent", agent.request_id, TaskType.AGENT),
        request=agent,
    )
    assert created is True
    agent_request_json = _request_json(store, agent_task.task_id)

    workflow = WorkflowRequest(
        request_id="request-secret-workflow",
        workflow_id="storage.workflow",
        input=BUSINESS_INPUT,
        context={
            "provider_bearer_token": RUNTIME_SECRET,
            "secret": "business-context-secret",
        },
        metadata={
            "runtime_credential": RUNTIME_SECRET,
            "password": "business-metadata-password",
        },
        workflow=WorkflowDefinition(
            workflow_id="storage.workflow",
            version="1",
            steps=[StepDefinition(step_id="extract", agent_id="storage.test")],
            metadata={
                "runtime_credentials": {"provider_api_key": RUNTIME_SECRET},
                "api_key": "business-workflow-api-key",
            },
        ),
    )
    workflow_task, created = store.create_or_get_task(
        _task(
            "task-request-secret-workflow",
            workflow.request_id,
            TaskType.WORKFLOW,
        ),
        request=workflow,
    )
    assert created is True
    workflow_request_json = _request_json(store, workflow_task.task_id)

    assert RUNTIME_SECRET not in agent_request_json
    assert RUNTIME_SECRET not in workflow_request_json
    assert _raw_database_hit_count(store, RUNTIME_SECRET) == 0

    persisted_agent = store.load_request(agent_task.task_id)
    persisted_workflow = store.load_request(workflow_task.task_id)
    assert persisted_agent is not None
    assert persisted_workflow is not None
    assert persisted_agent.input == BUSINESS_INPUT
    assert persisted_workflow.input == BUSINESS_INPUT
    for value in BUSINESS_INPUT.values():
        assert value in agent_request_json
        assert value in workflow_request_json
    assert "business-context-api-key" in agent_request_json
    assert "business-metadata-token" in agent_request_json
    assert "business-context-secret" in workflow_request_json
    assert "business-metadata-password" in workflow_request_json
    assert "business-workflow-api-key" in workflow_request_json


def _configured_runtime(tmp_path, store, *, fault_injector=None):
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "prompts" / "simple.md").write_text(
        "Return JSON.", encoding="utf-8"
    )
    (tmp_path / "model.yaml").write_text(
        """
active_model: primary
models:
  primary:
    provider: openai_compatible
    base_url_env: PROVIDER_BASE_URL
    api_key_env: PROVIDER_API_KEY
    model: test-model
""".strip(),
        encoding="utf-8",
    )
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        """
agent_id: configured-agent
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
    loader = AgentConfigLoader(
        root=tmp_path,
        model_profiles="model.yaml",
        schemas={"Result": {"type": "object"}},
        environ={
            "PROVIDER_BASE_URL": "http://provider.invalid/v1",
            "PROVIDER_API_KEY": RUNTIME_SECRET,
        },
    )
    return (
        ConfiguredAgentRuntime(
            store,
            config_loader=loader,
            fault_injector=fault_injector,
        ),
        config_path,
    )


def test_t05_t06_resume_reresolves_secret_without_persisting_it(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    should_crash = {"value": True}
    observed_secrets: list[str | None] = []

    def fault(point: str) -> None:
        if should_crash["value"] and point == "after_provider_return_before_commit":
            raise SimulatedCrash(point)

    def successful_handler(payload, context):
        observed_secrets.append(context["runtime"]["provider_config"]["api_key"])
        return {"business": payload}

    runtime, config_path = _configured_runtime(
        tmp_path,
        store,
        fault_injector=fault,
    )
    runtime.load_agent(config_path, successful_handler)
    request = AgentRequest(
        request_id="request-secret-resume",
        agent_id="configured-agent",
        input=BUSINESS_INPUT,
        context={"provider_api_key": RUNTIME_SECRET},
        metadata={"runtime_secret": RUNTIME_SECRET},
    )

    with pytest.raises(SimulatedCrash):
        runtime.invoke(request)

    task = store.get_task_by_request_id(request.request_id)
    assert task is not None
    assert _raw_database_hit_count(store, RUNTIME_SECRET) == 0

    should_crash["value"] = False
    resumed, _ = _configured_runtime(tmp_path, store)
    resumed.load_agent(config_path, successful_handler)
    handle = resumed.resume(task.task_id)

    assert handle.status == RuntimeStatus.COMPLETED
    assert observed_secrets == [RUNTIME_SECRET, RUNTIME_SECRET]
    assert store.load_request(task.task_id).input == BUSINESS_INPUT

    def failing_handler(_payload, context):
        secret = context["runtime"]["provider_config"]["api_key"]
        raise RuntimeStepError(
            f"provider rejected {secret}",
            category=ErrorCategory.EXECUTION,
            details={"provider_message": secret},
        )

    resumed.load_agent(config_path, failing_handler)
    failed = resumed.invoke(
        AgentRequest(
            request_id="request-secret-error",
            agent_id="configured-agent",
            input=BUSINESS_INPUT,
            context={"provider_api_key": RUNTIME_SECRET},
            metadata={"runtime_secret": RUNTIME_SECRET},
        )
    )
    assert failed.status == RuntimeStatus.FAILED
    assert _raw_database_hit_count(store, RUNTIME_SECRET) == 0
