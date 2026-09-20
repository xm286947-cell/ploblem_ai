from __future__ import annotations

from pathlib import Path

import pytest

from runtime import (
    ErrorCategory,
    LightweightExecutionEngine,
    RuntimeStatus,
    RuntimeStepError,
    SimulatedCrash,
    SqliteTaskStore,
)
from runtime.adapters import (
    LegacyProgressMapper,
    LegacyProjector,
    LegacyQualityIssueRuntimeAdapter,
    QualityIssueModelYamlAdapter,
    QUALITY_ISSUE_STAGES,
)


def write_quality_issue_runtime_config(root: Path) -> None:
    config = root / "config"
    prompts = root / "quality_knowledge" / "prompts"
    config.mkdir(parents=True)
    prompts.mkdir(parents=True)

    (config / "model.yaml").write_text(
        """
ai:
  enabled: true
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  api_key_env: TEST_API_KEY
  model: base-model
  temperature: 0
  max_tokens: 4096
  timeout_seconds: 120
  max_retries: 2
  stage_runtime:
    occurrence:
      timeout_seconds: 101
      max_retries: 1
      validation_retries: 1
      max_tokens: 1111
    escape:
      timeout_seconds: 102
      max_retries: 2
      validation_retries: 1
      max_tokens: 2222
    recurrence:
      timeout_seconds: 103
      max_retries: 1
      validation_retries: 2
      max_tokens: 3333
    capability_gap:
      timeout_seconds: 104
      max_retries: 2
      validation_retries: 2
      max_tokens: 4444
quality_issue_agents:
  quality:
    label: Quality Agent
    enabled: true
    provider: openai_compatible
    model: quality-model
parallel_ai:
  enabled: true
  max_workers: 4
""".strip(),
        encoding="utf-8",
    )
    for stage in QUALITY_ISSUE_STAGES:
        (prompts / f"{stage}.md").write_text(
            f"prompt for {stage}\n",
            encoding="utf-8",
        )


def successful_handlers(calls=None):
    calls = calls if calls is not None else {}

    def make(stage):
        def handler(payload, context):
            calls[stage] = calls.get(stage, 0) + 1
            return {
                "stage": stage,
                "knowledge_id": payload["issue"]["knowledge_id"],
                "dependencies": dict(context.get("dependencies") or {}),
            }

        return handler

    return {stage: make(stage) for stage in QUALITY_ISSUE_STAGES}


def make_adapter(
    tmp_path,
    *,
    handlers=None,
    projector=None,
    legacy_reader=None,
):
    root = tmp_path / "app"
    write_quality_issue_runtime_config(root)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    adapter = LegacyQualityIssueRuntimeAdapter(
        runtime,
        store,
        root,
        agent_id="quality",
        stage_handlers=handlers or successful_handlers(),
        projector=projector,
        legacy_reader=legacy_reader,
    )
    return root, store, runtime, adapter


def issue(version="ISSUE-V1"):
    return {
        "knowledge_id": "QK-DEMO-1",
        "issue_version_id": version,
        "business_type": "ITR",
        "business_issue_id": "ITR-001",
        "issue_fact": {"title": "PLC restart"},
    }


def test_lq01_model_yaml_maps_to_runtime_agents_workflow_and_retry_policy(tmp_path):
    root = tmp_path / "app"
    write_quality_issue_runtime_config(root)

    definitions, workflow, workflow_policy = QualityIssueModelYamlAdapter(
        root,
        agent_id="quality",
    ).build()

    assert tuple(definitions) == QUALITY_ISSUE_STAGES
    assert [step.step_id for step in workflow.steps] == list(QUALITY_ISSUE_STAGES)
    assert [step.depends_on for step in workflow.steps] == [
        [],
        ["occurrence"],
        ["escape"],
        ["recurrence"],
    ]

    occurrence = definitions["occurrence"]
    assert occurrence.model == "quality-model"
    assert occurrence.metadata["legacy_stage"] == "occurrence"
    assert occurrence.metadata["prompt_hash"]

    occurrence_policy = workflow.steps[0].execution_policy
    assert occurrence_policy.timeout_seconds == 101
    assert occurrence_policy.model_policy["max_tokens"] == 1111
    assert occurrence_policy.transport_retry.max_attempts == 2
    assert occurrence_policy.validation_retry.max_attempts == 2
    assert occurrence_policy.retry_budget.max_provider_calls_per_step == 4

    capability_policy = workflow.steps[-1].execution_policy
    assert capability_policy.timeout_seconds == 104
    assert capability_policy.model_policy["max_tokens"] == 4444
    assert capability_policy.transport_retry.max_attempts == 3
    assert capability_policy.validation_retry.max_attempts == 3
    assert capability_policy.retry_budget.max_provider_calls_per_step == 9

    assert workflow_policy.mode.value == "SEQUENTIAL"
    assert workflow.metadata["legacy_agent_id"] == "quality"
    assert workflow.metadata["model_config_hash"]


def test_lq02_runtime_run_maps_one_to_one_analysis_set_and_steps_to_stages(tmp_path):
    _, store, _, adapter = make_adapter(tmp_path)

    result = adapter.execute_issue(
        "QK-DEMO-1",
        issue_input=issue(),
        issue_version_id="ISSUE-V1",
    )

    assert result.status == RuntimeStatus.COMPLETED
    projected = adapter.get_projected_analysis_set(result.run_id)
    assert projected["run_id"] == result.run_id
    assert projected["runtime_task_id"] == result.task_id
    assert projected["knowledge_id"] == "QK-DEMO-1"
    assert projected["issue_version_id"] == "ISSUE-V1"
    assert projected["status"] == "COMPLETED"
    assert projected["execution_state_source"] == "RUNTIME"

    stages = {item["legacy_stage"]: item for item in projected["stages"]}
    assert set(stages) == set(QUALITY_ISSUE_STAGES)
    for stage in QUALITY_ISSUE_STAGES:
        assert stages[stage]["status"] == "COMPLETED"
        assert stages[stage]["result"]["stage"] == stage
        assert stages[stage]["commit_id"]

    binding = store.get_legacy_binding_by_run(result.run_id)
    assert binding is not None
    assert binding.legacy_analysis_set_id == projected["legacy_analysis_set_id"]


def test_lq03_execution_commit_and_projection_outbox_are_atomic(tmp_path):
    root = tmp_path / "app"
    write_quality_issue_runtime_config(root)
    crash_once = {"armed": True}

    def store_fault(point):
        if point == "after_atomic_writes_before_commit" and crash_once["armed"]:
            crash_once["armed"] = False
            raise SimulatedCrash(point)

    store = SqliteTaskStore(
        tmp_path / "runtime.db",
        fault_injector=store_fault,
    )
    runtime = LightweightExecutionEngine(store)
    adapter = LegacyQualityIssueRuntimeAdapter(
        runtime,
        store,
        root,
        agent_id="quality",
        stage_handlers=successful_handlers(),
    )

    with pytest.raises(SimulatedCrash):
        adapter.execute_issue(
            "QK-DEMO-1",
            issue_input=issue(),
            issue_version_id="ISSUE-V1",
        )

    task = store.get_task_by_request_id(
        "quality-issue:QK-DEMO-1:ISSUE-V1:quality"
    )
    assert task is not None
    assert len(store.list_committed_execution_keys(task.task_id)) == 0
    assert store.list_projection_events(
        task_id=task.task_id,
        status="PENDING",
    ) == []


def test_lq04_projection_failure_does_not_rollback_runtime_and_can_replay(tmp_path):
    root = tmp_path / "app"
    write_quality_issue_runtime_config(root)
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    fail_once = {"armed": True}

    def fail_hook(event):
        if fail_once["armed"]:
            fail_once["armed"] = False
            raise RuntimeError(f"projection unavailable for {event.event_id}")

    projector = LegacyProjector(store, fail_hook=fail_hook)
    adapter = LegacyQualityIssueRuntimeAdapter(
        runtime,
        store,
        root,
        agent_id="quality",
        stage_handlers=successful_handlers(),
        projector=projector,
    )

    result = adapter.execute_issue(
        "QK-DEMO-1",
        issue_input=issue(),
        issue_version_id="ISSUE-V1",
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert len(store.list_committed_execution_keys(result.task_id)) == 4
    pending = store.list_projection_events(
        task_id=result.task_id,
        run_id=result.run_id,
        status="PENDING",
    )
    assert len(pending) == 1
    assert "projection unavailable" in (pending[0].last_error or "")

    projected = adapter.get_projected_analysis_set(result.run_id)
    projected_stages = {item["legacy_stage"] for item in projected["stages"]}
    assert len(projected_stages) == 3
    assert projected["metadata"]["projection_pending"] is True

    projector.fail_hook = None
    replay = projector.replay_pending(
        task_id=result.task_id,
        run_id=result.run_id,
    )
    projector.sync_run(result.run_id)

    assert replay == {"applied": 1, "failed": 0}
    assert store.list_projection_events(
        task_id=result.task_id,
        run_id=result.run_id,
        status="PENDING",
    ) == []
    recovered = adapter.get_projected_analysis_set(result.run_id)
    assert {item["legacy_stage"] for item in recovered["stages"]} == set(
        QUALITY_ISSUE_STAGES
    )
    assert recovered["metadata"]["projection_pending"] is False


def test_lq05_resume_creates_new_analysis_set_and_reuses_committed_stage(tmp_path):
    calls = {}
    fail_once = {"escape": True}
    handlers = successful_handlers(calls)

    original_escape = handlers["escape"]

    def escape(payload, context):
        calls["escape"] = calls.get("escape", 0) + 1
        if fail_once["escape"]:
            fail_once["escape"] = False
            raise RuntimeStepError(
                "temporary escape failure",
                code="TEMP_ESCAPE",
                category=ErrorCategory.EXECUTION,
                retryable=True,
            )
        # Avoid double increment from the wrapped successful handler.
        return {
            "stage": "escape",
            "knowledge_id": payload["issue"]["knowledge_id"],
            "dependencies": dict(context.get("dependencies") or {}),
        }

    handlers["escape"] = escape
    _, store, _, adapter = make_adapter(tmp_path, handlers=handlers)

    first = adapter.execute_issue(
        "QK-DEMO-1",
        issue_input=issue(),
        issue_version_id="ISSUE-V1",
    )
    assert first.status == RuntimeStatus.PARTIAL
    first_projection = adapter.get_projected_analysis_set(first.run_id)
    assert {item["legacy_stage"] for item in first_projection["stages"]} == {
        "occurrence"
    }

    snapshot = adapter.resume(first.task_id)
    assert snapshot.status == RuntimeStatus.COMPLETED
    assert snapshot.current_run_id != first.run_id

    second_projection = adapter.get_projected_analysis_set(
        snapshot.current_run_id
    )
    assert second_projection["legacy_analysis_set_id"] != first_projection[
        "legacy_analysis_set_id"
    ]
    assert {item["legacy_stage"] for item in second_projection["stages"]} == set(
        QUALITY_ISSUE_STAGES
    )
    assert calls["occurrence"] == 1
    assert calls["escape"] == 2
    assert calls["recurrence"] == 1
    assert calls["capability_gap"] == 1

    runs = store.list_runs(first.task_id)
    assert len(runs) == 2
    assert runs[1].resume_of_run_id == runs[0].run_id


def test_lq06_progress_mapper_uses_runtime_state_and_marks_partial_resumable(tmp_path):
    calls = {"occurrence": 0}

    def occurrence(_payload, _context):
        calls["occurrence"] += 1
        raise RuntimeStepError(
            "temporary",
            code="TEMP",
            category=ErrorCategory.EXECUTION,
            retryable=True,
        )

    handlers = successful_handlers()
    handlers["occurrence"] = occurrence
    _, _, runtime, adapter = make_adapter(tmp_path, handlers=handlers)

    result = adapter.execute_issue(
        "QK-DEMO-1",
        issue_input=issue(),
        issue_version_id="ISSUE-V1",
    )
    assert result.status == RuntimeStatus.PARTIAL

    progress = adapter.get_progress(result.task_id)
    assert progress["execution_state_source"] == "RUNTIME"
    assert progress["runtime_status"] == "PARTIAL"
    assert progress["legacy_status"] == "PARTIAL_FAILED"
    assert progress["can_resume"] is True
    assert progress["current_run_id"] == result.run_id

    completed = LegacyProgressMapper.map(
        runtime.get_task(result.task_id).model_copy(
            update={"status": RuntimeStatus.COMPLETED}
        )
    )
    assert completed["legacy_status"] == "COMPLETED"
    assert completed["can_resume"] is False


def test_lq07_runtime_projection_preferred_while_historical_legacy_remains_readable(tmp_path):
    historical = {
        "legacy_analysis_set_id": "LAS-HISTORICAL",
        "status": "COMPLETED",
        "knowledge_id": "QK-OLD",
        "stages": [{"legacy_stage": "occurrence", "status": "COMPLETED"}],
    }

    def legacy_reader(analysis_set_id):
        if analysis_set_id == "LAS-HISTORICAL":
            return dict(historical)
        return None

    _, _, _, adapter = make_adapter(
        tmp_path,
        legacy_reader=legacy_reader,
    )

    result = adapter.execute_issue(
        "QK-DEMO-1",
        issue_input=issue(),
        issue_version_id="ISSUE-V1",
    )
    current = adapter.get_projected_analysis_set(result.run_id)
    current_read = adapter.read_analysis_set(
        current["legacy_analysis_set_id"]
    )
    old_read = adapter.read_analysis_set("LAS-HISTORICAL")

    assert current_read["execution_state_source"] == "RUNTIME"
    assert current_read["runtime_task_id"] == result.task_id
    assert old_read["execution_state_source"] == "LEGACY_HISTORY"
    assert old_read["runtime_task_id"] is None
    assert old_read["can_resume"] is False
    assert adapter.read_analysis_set("LAS-NOT-FOUND") is None
