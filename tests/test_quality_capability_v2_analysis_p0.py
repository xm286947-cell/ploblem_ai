"""G2-B: native V2 orchestration and human-confirmation contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.p0.repository import P0RepositoryError
from quality_knowledge.response_normalizer_v2 import V2StageNormalizationError, normalize_stage_v2
from quality_knowledge.services.v2_analysis_service import V2AnalysisError, V2AnalysisService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_repository(tmp_path) -> P0Repository:
    db_path = tmp_path / "p0-v2.db"
    P0Initializer(
        manifest_path=PROJECT_ROOT / "quality_knowledge" / "config" / "p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT / "quality_knowledge" / "config" / "plc_fields.yaml",
    ).initialize(db_path)
    repository = P0Repository(db_path)
    repository.save_issue(
        knowledge_id="K-V2-1",
        business_issue_id="PLC-V2-1",
        raw_json={"问题描述": "切换后状态机未复位", "SourceID": "feishu-source-1"},
        normalized_snapshot={"ISSUE_FACT": {"business_issue_id": "PLC-V2-1", "description": "切换后状态机未复位"}},
        mapping_config_id="MAP-PLC-V1",
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="input-file-hash",
        sheet_name="PLC",
        row_number=2,
    )
    return repository


def evidence() -> dict:
    return {
        "source_type": "SOURCE_DATA",
        "source_ref": "issue_source_raw",
        "field_path": "问题描述",
        "excerpt": "切换后状态机未复位",
        "confidence": 1.0,
    }


def inferred_value(value: str) -> dict:
    return {"value": value, "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()]}


class FakeStageRunner:
    def __init__(self, failed_stage: str | None = None):
        self.failed_stage = failed_stage
        self.calls: list[tuple[str, str]] = []

    def run_stage(self, *, stage, context):
        self.calls.append((stage, context.analysis_set_id))
        if stage == self.failed_stage:
            raise RuntimeError(f"fake {stage} failure")
        if stage == "occurrence":
            return {
                "engineering_root_cause": inferred_value("状态转换逻辑遗漏"),
                "management_contributing_factors": [inferred_value("变更影响未确认")],
                "introduced_stage": "IMPLEMENTATION",
                "domain_tags": [{"axis": "DOMAIN", "code": "EMBEDDED", "role": "PRIMARY", "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()]}],
                "lifecycle_tags": [{"axis": "LIFECYCLE", "code": "IMPLEMENTATION", "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()]}],
                "trigger_tags": [],
                "failure_mechanism_tags": [{"axis": "FAILURE_MECHANISM", "code": "STATE_MACHINE", "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()]}],
                "mrc": {"side": "OCCURRENCE", "primary": {"axis": "MRC", "code": "CHANGE_IMPACT_NOT_ASSESSED", "role": "PRIMARY", "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()]}, "control_status": "EXECUTED_INSUFFICIENT"},
                "open_questions": [{"question_key": "occurrence.change", "target_path": "occurrence.mrc.primary", "question": "变更前是否完成影响评审？", "priority": "HIGH"}],
                "confidence": 0.6,
                "evidence": [evidence()],
            }
        if stage == "escape":
            return {
                "expected_detection_stage": "TEST_VALIDATION",
                "actual_detection_stage": "OPERATION_MAINTENANCE",
                "escape_mechanism": inferred_value("边界条件未覆盖"),
                "missing_or_failed_control": inferred_value("回归准入项缺失"),
                "control_status": "NOT_DEFINED",
                "known_issue_leakage": {"status": "UNKNOWN", "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()]},
                "mrc": {"side": "ESCAPE", "primary": {"axis": "MRC", "code": "RELEASE_GATE_FAILED", "role": "PRIMARY", "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()]}},
                "open_questions": [], "confidence": 0.6, "evidence": [evidence()],
            }
        if stage == "recurrence":
            return {
                "recurrence_risk_level": "HIGH",
                "existing_control_coverage": inferred_value("仅覆盖正常流程"),
                "residual_risk": inferred_value("版本组合仍可能复现"),
                "potential_affected_products": ["PLC"],
                "potential_affected_versions": ["V1"],
                "horizontal_action_needed": True,
                "customer_impact": inferred_value("现场停机风险"),
                "open_questions": [],
            }
        return {
            "capability_gaps": [{
                "gap_id": "GAP-1", "capability_axis": "QUALITY_ENGINEERING",
                "capability_code": "EMBEDDED_HW_SW_CO_DESIGN", "governance_scope": "PRODUCT",
                "related_occurrence_mrc_codes": ["CHANGE_IMPACT_NOT_ASSESSED"],
                "related_escape_mrc_codes": ["RELEASE_GATE_FAILED"],
                "control_status": "NOT_DEFINED", "gap_description": "缺少变更后状态机回归设计",
                "build_target": "状态机变更回归包", "technical_measure": "维护状态迁移覆盖矩阵",
                "first_action": "补齐状态迁移用例", "owner_role": "系统测试负责人",
                "implementation_stage": "TEST_VALIDATION", "prevention_effect": "降低同类流出",
                "verification_metric": "状态迁移覆盖率", "priority": "P0",
                "source_type": "AI_INFERRED", "confidence": 0.9, "evidence": [evidence()],
            }]
        }


def test_four_stages_share_one_analysis_set_and_persist_projections(tmp_path):
    repository = make_repository(tmp_path)
    runner = FakeStageRunner()
    result = V2AnalysisService(repository, runner).run("K-V2-1", {"analysis_profile": {"issue_domains": ["EMBEDDED"]}})
    assert result.status == "COMPLETED"
    assert [stage for stage, _ in runner.calls] == ["occurrence", "escape", "recurrence", "capability_gap"]
    assert {analysis_set_id for _, analysis_set_id in runner.calls} == {result.analysis_set_id}
    assert result.occurrence.engineering_root_cause.confidence == 0.60
    saved = repository.get_analysis_set(result.analysis_set_id)
    assert len(saved["stage_runs"]) == 4
    assert {stage["status"] for stage in saved["stage_runs"]} == {"COMPLETED"}
    assert saved["mrc"][0]["mrc_code"] == "RELEASE_GATE_FAILED"
    assert saved["capability_gaps"][0]["capability_axis"] == "QUALITY_ENGINEERING"
    assert saved["evidence"]


def test_single_stage_failure_is_partial_and_never_becomes_empty_completed(tmp_path):
    repository = make_repository(tmp_path)
    result = V2AnalysisService(repository, FakeStageRunner("escape")).run("K-V2-1")
    assert result.status == "PARTIAL_FAILED"
    assert result.escape is None
    saved = repository.get_analysis_set(result.analysis_set_id)
    assert next(row for row in saved["stage_runs"] if row["stage"] == "escape")["status"] == "FAILED"


def test_no_runner_and_no_analysis_have_stable_errors(tmp_path):
    repository = make_repository(tmp_path)
    service = V2AnalysisService(repository)
    with pytest.raises(V2AnalysisError, match="ANALYSIS_RUNNER_NOT_CONFIGURED"):
        service.run("K-V2-1")
    with pytest.raises(V2AnalysisError, match="V2_ANALYSIS_NOT_AVAILABLE"):
        service.get("K-V2-1")


def test_old_keys_and_extra_v2_keys_are_rejected_with_stage_path():
    with pytest.raises(V2StageNormalizationError) as old_key:
        normalize_stage_v2("occurrence", {"root_cause": "legacy"})
    assert "stage=occurrence" in str(old_key.value)
    assert "path=$.root_cause" in str(old_key.value)
    with pytest.raises(V2StageNormalizationError, match="V2_STAGE_KEYS_INVALID"):
        normalize_stage_v2("capability_gap", {"capability_gaps": [], "why_escaped": "legacy"})
    with pytest.raises(V2StageNormalizationError, match="V2_STAGE_REQUIRED_KEY_MISSING"):
        normalize_stage_v2("occurrence", {})


def test_hash_idempotence_and_force_create_a_new_analysis_version(tmp_path):
    repository = make_repository(tmp_path)
    runner = FakeStageRunner()
    service = V2AnalysisService(repository, runner)
    first = service.run("K-V2-1")
    second = service.run("K-V2-1")
    forced = service.run("K-V2-1", {"force": True, "force_nonce": "run-2"})
    assert second.analysis_set_id == first.analysis_set_id
    assert forced.analysis_set_id != first.analysis_set_id
    assert forced.input_hash != first.input_hash
    assert len(runner.calls) == 8


def test_human_revision_is_auditable_effective_value_without_overwrite(tmp_path):
    repository = make_repository(tmp_path)
    result = V2AnalysisService(repository, FakeStageRunner()).run("K-V2-1")
    revision = repository.save_human_revision(
        base_analysis_set_id=result.analysis_set_id,
        base_input_hash=result.input_hash,
        confirmed_by="quality-owner",
        answers=[{
            "target_path": "occurrence.mrc.primary",
            "original_value": "CHANGE_IMPACT_NOT_ASSESSED",
            "confirmed_value": "DESIGN_REVIEW_INEFFECTIVE",
            "evidence": [{"note": "review record"}],
            "changes_insight": True,
        }, {
            "target_path": "capability_gaps.QUALITY_ENGINEERING.EMBEDDED_HW_SW_CO_DESIGN.PRODUCT",
            "original_value": {"gap_description": "缺少变更后状态机回归设计"},
            "confirmed_value": {"gap_description": "已纳入版本变更回归包"},
            "evidence": [{"note": "owner confirmed"}],
            "changes_insight": True,
        }],
    )
    assert revision["revision_no"] == 1
    assert repository.get_analysis_set(result.analysis_set_id)["mrc"][1]["mrc_code"] == "CHANGE_IMPACT_NOT_ASSESSED"
    assert repository.get_issue("K-V2-1")["raw_json"]["问题描述"] == "切换后状态机未复位"
    effective = repository.get_effective_analysis(result.analysis_set_id)["values"]
    assert effective["occurrence.mrc.primary"] == "DESIGN_REVIEW_INEFFECTIVE"
    assert effective["capability_gaps.QUALITY_ENGINEERING.EMBEDDED_HW_SW_CO_DESIGN.PRODUCT"] == {
        "gap_description": "已纳入版本变更回归包"
    }


def test_stale_or_parallel_human_revision_returns_conflict(tmp_path):
    repository = make_repository(tmp_path)
    result = V2AnalysisService(repository, FakeStageRunner()).run("K-V2-1")
    repository.save_human_revision(
        base_analysis_set_id=result.analysis_set_id, base_input_hash=result.input_hash,
        confirmed_by="first", answers=[],
    )
    with pytest.raises(P0RepositoryError, match="HUMAN_CONFIRMATION_VERSION_CONFLICT"):
        repository.save_human_revision(
            base_analysis_set_id=result.analysis_set_id, base_input_hash=result.input_hash,
            confirmed_by="second", answers=[], expected_revision_no=0,
        )
    with pytest.raises(P0RepositoryError, match="HUMAN_CONFIRMATION_STALE"):
        repository.save_human_revision(
            base_analysis_set_id=result.analysis_set_id, base_input_hash="old-hash",
            confirmed_by="second", answers=[],
        )
