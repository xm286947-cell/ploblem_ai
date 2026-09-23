import pytest
from pydantic import ValidationError

from quality_knowledge.quality_scenario_v1 import (
    QualityScenarioV1,
    ScenarioActor,
    ScenarioCandidateV1,
    ScenarioEvidenceReference,
    ScenarioMissingInformation,
    ScenarioProvenanceType,
    ScenarioReviewMetadata,
    ScenarioReviewStatus,
    ScenarioSourceReference,
    ScenarioStatus,
    ScenarioVersionMetadata,
    scenario_from_candidate,
    validate_status_transition,
)
from quality_knowledge.quality_scenario_v1_adapter import (
    legacy_scenario_to_v1_view,
    scenario_candidate_v1_from_reverse_quality,
)


def source():
    return ScenarioSourceReference(
        source_ref="ITR:ITR-001",
        source_type="ITR",
        source_id="ITR-001",
        canonical_itr="ITR-001",
        product_code="PLC",
    )


def evidence():
    return ScenarioEvidenceReference(
        evidence_id="cs.description",
        source_ref="ITR:ITR-001",
        evidence_type="FIELD",
        content_ref="evidence://ITR-001/cs.description",
        supports=["scenario_description"],
        source_type=ScenarioProvenanceType.FACT,
        confidence=0.9,
    )


def candidate(**overrides):
    payload = {
        "candidate_id": "QSCAND-001",
        "product_code": "PLC",
        "lifecycle_stage_code": "RUNTIME_EXECUTION",
        "lifecycle_stage_name": "运行执行",
        "business_activity_code": "POWER_LOSS_RETENTION_RECOVERY",
        "business_activity_name": "掉电数据保持与上电恢复",
        "business_goal": "掉电后关键数据正确恢复",
        "scenario_name": "掉电后关键数据恢复",
        "scenario_description": "运行中掉电后关键计数丢失",
        "quality_concern_name": "数据完整性",
        "trigger_condition": "运行中异常掉电",
        "expected_result": "重新上电后关键计数正确恢复",
        "applicability_scope": "PLC运行过程",
        "source_problem_refs": [source()],
        "evidence_refs": [evidence()],
    }
    payload.update(overrides)
    return ScenarioCandidateV1(**payload)


def test_candidate_v1_serialization_roundtrip():
    original = candidate()
    restored = ScenarioCandidateV1.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.schema_version == "quality-scenario-v1"
    assert restored.status == ScenarioStatus.CANDIDATE


def test_candidate_required_field_missing_is_rejected():
    with pytest.raises(ValidationError):
        ScenarioCandidateV1(candidate_id="X", product_code="PLC")


def test_evidence_requires_source_text_or_content_ref():
    with pytest.raises(ValidationError, match="EVIDENCE_SOURCE_TEXT_OR_CONTENT_REF_REQUIRED"):
        ScenarioEvidenceReference(
            evidence_id="E1",
            source_ref="ITR:1",
            evidence_type="FIELD",
            supports=["expected_result"],
            source_type="FACT",
        )


def test_evidence_source_reference_must_exist():
    with pytest.raises(ValidationError, match="SCENARIO_EVIDENCE_SOURCE_REF_NOT_FOUND"):
        candidate(
            evidence_refs=[
                ScenarioEvidenceReference(
                    evidence_id="E1",
                    source_ref="ITR:OTHER",
                    evidence_type="FIELD",
                    content_ref="evidence://other",
                    supports=["expected_result"],
                    source_type="FACT",
                )
            ]
        )


def test_status_machine_allows_only_minimum_mvp_transitions():
    assert validate_status_transition(
        "CANDIDATE", "CONFIRMED", actor="HUMAN"
    ) == ScenarioStatus.CONFIRMED
    assert validate_status_transition(
        "CANDIDATE", "REJECTED", actor="HUMAN"
    ) == ScenarioStatus.REJECTED
    assert validate_status_transition(
        "CONFIRMED", "PUBLISHED", actor="SYSTEM"
    ) == ScenarioStatus.PUBLISHED
    with pytest.raises(ValueError, match="SCENARIO_STATUS_TRANSITION_INVALID"):
        validate_status_transition("CANDIDATE", "PUBLISHED", actor="HUMAN")


def test_ai_cannot_confirm_or_publish():
    with pytest.raises(ValueError, match="SCENARIO_STATUS_AI_DECISION_FORBIDDEN"):
        validate_status_transition("CANDIDATE", "CONFIRMED", actor=ScenarioActor.AI)


def test_blocker_and_pending_missing_information_block_confirmation():
    with pytest.raises(ValueError, match="SCENARIO_BLOCKERS_NOT_RESOLVED"):
        validate_status_transition(
            "CANDIDATE", "CONFIRMED", actor="HUMAN", blockers=["EVIDENCE_REQUIRED"]
        )
    with pytest.raises(ValueError, match="SCENARIO_MISSING_INFORMATION_PENDING"):
        validate_status_transition(
            "CANDIDATE",
            "CONFIRMED",
            actor="HUMAN",
            missing_information=[
                ScenarioMissingInformation(question="设备规模是多少？")
            ],
        )


def test_formal_scenario_requires_review_source_and_evidence():
    base = scenario_from_candidate(candidate(), "QSV1-1", created_by="AI")
    with pytest.raises(ValidationError, match="SCENARIO_REVIEW_NOT_CONFIRMED"):
        QualityScenarioV1.model_validate(
            {**base.model_dump(mode="json"), "status": "CONFIRMED"}
        )


def reverse_result(lifecycle="运行执行", activity="掉电数据保持与上电恢复"):
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-1",
        "run_id": "RQRUN-1",
        "run_seq": 2,
        "identity": {
            "canonical_itr": "ITR-001",
            "product_code": "PLC",
            "taxonomy_version_id": "STV-1",
        },
        "fields": {
            "lifecycle_stage": {
                "value": lifecycle, "source_type": "FACT",
                "evidence_ids": ["cs.phase"], "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "business_activity_scene": {
                "value": activity, "source_type": "FACT",
                "evidence_ids": ["cs.description"], "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "customer_experience": {
                "value": "掉电后关键计数丢失", "source_type": "FACT",
                "evidence_ids": ["cs.description"], "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "quality_risk": {
                "value": "数据完整性", "source_type": "INFERRED",
                "evidence_ids": ["cs.description"], "confidence": 0.7,
                "review_status": "PENDING",
            },
            "expected_quality_state": {
                "value": "重新上电后计数正确恢复", "source_type": "INFERRED",
                "evidence_ids": ["cs.description"], "confidence": 0.8,
                "review_status": "PENDING",
            },
            "trigger_condition": {
                "value": "运行中异常掉电", "source_type": "FACT",
                "evidence_ids": ["cs.description"], "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
        },
        "missing_information": [],
    }


def taxonomy():
    return {
        "version_id": "STV-1",
        "lifecycles": [
            {
                "lifecycle_code": "RUNTIME_EXECUTION",
                "label_zh": "运行执行",
                "enabled": 1,
            }
        ],
        "activities": [
            {
                "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
                "lifecycle_code": "RUNTIME_EXECUTION",
                "label_zh": "掉电数据保持与上电恢复",
                "objective": "保证掉电后关键数据正确恢复",
                "chain_text": "运行 → 掉电 → 上电 → 恢复",
                "enabled": 1,
            }
        ],
    }


def test_reverse_quality_result_maps_to_candidate_v1_without_second_ai_pass():
    item = scenario_candidate_v1_from_reverse_quality(reverse_result(), taxonomy())
    assert item.status == ScenarioStatus.CANDIDATE
    assert item.product_code == "PLC"
    assert item.lifecycle_stage_code == "RUNTIME_EXECUTION"
    assert item.business_activity_code == "POWER_LOSS_RETENTION_RECOVERY"
    assert item.expected_result == "重新上电后计数正确恢复"
    assert item.source_problem_refs[0].canonical_itr == "ITR-001"
    assert item.evidence_refs
    assert "EVIDENCE_REQUIRED" not in item.blockers


def test_unmapped_lifecycle_activity_blockers_are_not_swallowed():
    item = scenario_candidate_v1_from_reverse_quality(
        reverse_result("不存在阶段", "不存在活动"),
        taxonomy(),
    )
    assert "ACTIVITY_NOT_MAPPED" in item.blockers
    assert "LIFECYCLE_NOT_MAPPED" in item.blockers
    assert item.lifecycle_stage_code == ""
    assert item.business_activity_code == ""


def test_legacy_scenario_mapping_is_explicit_read_only_compatibility():
    view = legacy_scenario_to_v1_view(
        {
            "scenario_id": "OLD-1",
            "version_no": 3,
            "status": "PUBLISHED",
            "product_code": "PLC",
            "lifecycle_code": "RUNTIME_EXECUTION",
            "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
            "name": "旧掉电恢复场景",
            "experience_requirement": "掉电恢复",
            "concern_points": "数据完整性",
        }
    )
    assert view["read_only"] is True
    assert view["status"] == "PUBLISHED"
    assert view["legacy_status"] == "PUBLISHED"
    assert view["source_problem_refs"] == []
    assert "SOURCE_EVIDENCE_REQUIRES_EXPLICIT_V1_MIGRATION" in view["compatibility_warnings"]
