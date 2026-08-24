"""G2-B2 production-runner tests with a local OpenAI-compatible fake client."""

from __future__ import annotations

import json
from types import SimpleNamespace

from builder.ai_client import AIResponse
from quality_knowledge.p0.stage_runner import ProductionV2StageRunner
from quality_knowledge.services.v2_analysis_service import V2AnalysisService

from test_quality_capability_v2_analysis_p0 import FakeStageRunner, make_repository


class FakeAIClient:
    def __init__(self, *, failures: dict[str, int] | None = None, legacy_stage: str | None = None):
        self.failures = dict(failures or {})
        self.legacy_stage = legacy_stage
        self.messages: list[list[dict[str, str]]] = []
        self.stage_payloads = FakeStageRunner()

    def complete(self, messages):
        self.messages.append(messages)
        context = json.loads(messages[1]["content"])
        stage = context["stage"]
        if self.failures.get(stage, 0):
            self.failures[stage] -= 1
            raise RuntimeError(f"transient {stage} failure")
        if stage == self.legacy_stage:
            payload = {"root_cause": "legacy output"}
        else:
            payload = self.stage_payloads.run_stage(stage=stage, context=SimpleNamespace(analysis_set_id="fake"))
        return AIResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model="fake-openai-compatible",
            raw={"stage": stage, "request_count": len(self.messages)},
        )


def test_production_runner_uses_active_prompt_and_passes_previous_stage_context(tmp_path):
    repository = make_repository(tmp_path)
    client = FakeAIClient()
    runner = ProductionV2StageRunner(repository, ai_client=client, max_attempts=2)
    result = V2AnalysisService(repository, runner).run("K-V2-1")
    assert result.status == "COMPLETED"
    assert len(client.messages) == 4
    stages = [json.loads(messages[1]["content"])["stage"] for messages in client.messages]
    assert stages == ["occurrence", "escape", "recurrence", "capability_gap"]
    for messages, stage in zip(client.messages, stages):
        active = repository.get_active_prompt(stage)
        assert messages[0]["content"] == active["prompt_text"]
        assert json.loads(messages[1]["content"])["output_contract"] == active["output_contract_json"]
    escape_context = json.loads(client.messages[1][1]["content"])
    recurrence_context = json.loads(client.messages[2][1]["content"])
    assert "occurrence" in escape_context["completed_stages"]
    assert {"occurrence", "escape"} <= set(recurrence_context["completed_stages"])
    saved = repository.get_analysis_set(result.analysis_set_id)
    assert {row["model_name"] for row in saved["stage_runs"]} == {"fake-openai-compatible"}
    assert all(row["raw_response"] for row in saved["stage_runs"])
    assert all(row["debug_json"]["prompt_version_id"] for row in saved["stage_runs"])


def test_production_runner_retries_client_error_and_persists_debug(tmp_path):
    repository = make_repository(tmp_path)
    client = FakeAIClient(failures={"escape": 1})
    result = V2AnalysisService(
        repository, ProductionV2StageRunner(repository, ai_client=client, max_attempts=2)
    ).run("K-V2-1")
    assert result.status == "COMPLETED"
    stage = next(item for item in repository.get_analysis_set(result.analysis_set_id)["stage_runs"] if item["stage"] == "escape")
    assert stage["debug_json"]["retry_errors"] == ["transient escape failure"]


def test_production_runner_failed_stage_is_partial_and_strict_legacy_output_is_rejected(tmp_path):
    repository = make_repository(tmp_path)
    failed = V2AnalysisService(
        repository,
        ProductionV2StageRunner(repository, ai_client=FakeAIClient(failures={"escape": 2}), max_attempts=2),
    ).run("K-V2-1")
    assert failed.status == "PARTIAL_FAILED"
    saved_failed = repository.get_analysis_set(failed.analysis_set_id)
    escape = next(row for row in saved_failed["stage_runs"] if row["stage"] == "escape")
    assert escape["status"] == "FAILED"
    assert escape["debug_json"]["retry_errors"] == ["transient escape failure", "transient escape failure"]

    strict = V2AnalysisService(
        repository,
        ProductionV2StageRunner(repository, ai_client=FakeAIClient(legacy_stage="occurrence"), max_attempts=1),
    ).run("K-V2-1", {"force": True, "force_nonce": "strict-old-key"})
    assert strict.status == "PARTIAL_FAILED"
    occurrence = next(row for row in repository.get_analysis_set(strict.analysis_set_id)["stage_runs"] if row["stage"] == "occurrence")
    assert occurrence["status"] == "FAILED"
    assert "V2_STAGE_KEYS_INVALID" in occurrence["debug_json"]["retry_errors"][0]
