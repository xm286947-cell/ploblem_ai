from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.quality_scenario_candidate_v1_service import CandidateV1Service
from quality_knowledge.quality_scenario_v1 import ScenarioStatus, ScenarioTriggerSource
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.web.p0_app import create_p0_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def reverse_result(lifecycle="运行执行", activity="掉电数据保持与上电恢复", *, missing=None):
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-QS03-1",
        "run_id": "RQRUN-QS03-1",
        "run_seq": 3,
        "identity": {
            "canonical_itr": "ITR-QS03-001",
            "product_code": "PLC",
            "taxonomy_version_id": "STV-QS03-1",
        },
        "fields": {
            "lifecycle_stage": {
                "value": lifecycle,
                "source_type": "FACT",
                "evidence_ids": ["cs.phase"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "business_activity_scene": {
                "value": activity,
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "customer_experience": {
                "value": "掉电后关键计数丢失",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "quality_risk": {
                "value": "数据完整性",
                "source_type": "INFERRED",
                "evidence_ids": ["cs.description"],
                "confidence": 0.7,
                "review_status": "PENDING",
            },
            "expected_quality_state": {
                "value": "重新上电后计数正确恢复",
                "source_type": "INFERRED",
                "evidence_ids": ["cs.description"],
                "confidence": 0.8,
                "review_status": "PENDING",
            },
            "trigger_condition": {
                "value": "运行中异常掉电",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
        },
        "missing_information": missing or [],
    }


def taxonomy():
    return {
        "version_id": "STV-QS03-1",
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


def service(tmp_path):
    return CandidateV1Service(
        SQLiteQualityScenarioV1Repository(tmp_path / "quality_scenario_v1.db")
    )


def test_high_perception_produces_and_persists_candidate_v1(tmp_path):
    svc = service(tmp_path)
    result = svc.create_from_reverse(
        reverse_result(),
        taxonomy(),
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户生产中断，进入高感知问题深挖",
    )

    assert result.created is True
    assert result.candidate.trigger_source == ScenarioTriggerSource.HIGH_PERCEPTION
    assert result.scenario.status == ScenarioStatus.CANDIDATE
    assert result.scenario.trigger_reason == "客户生产中断，进入高感知问题深挖"
    assert result.scenario.source_problem_refs[0].canonical_itr == "ITR-QS03-001"
    assert result.scenario.evidence_refs
    assert svc.get_candidate(result.scenario.scenario_id) == result.scenario


def test_rnd_value_uses_same_candidate_path(tmp_path):
    svc = service(tmp_path)
    result = svc.create_from_reverse(
        reverse_result(),
        taxonomy(),
        trigger_source="RND_VALUE",
        trigger_reason="研发判断该问题具有跨产品复用价值",
    )
    assert result.candidate.trigger_source == ScenarioTriggerSource.RND_VALUE
    assert result.scenario.status == ScenarioStatus.CANDIDATE
    assert "WAIT_APPROVAL" not in result.scenario.model_dump_json()


def test_missing_trigger_context_is_not_guessed_and_blockers_survive(tmp_path):
    svc = service(tmp_path)
    result = svc.create_from_reverse(reverse_result(), taxonomy())
    assert result.candidate.trigger_source is None
    assert result.scenario.trigger_source is None
    assert "TRIGGER_SOURCE_REQUIRED" in result.scenario.blockers
    assert "TRIGGER_REASON_REQUIRED" in result.scenario.blockers


def test_taxonomy_mapping_blockers_are_not_swallowed(tmp_path):
    svc = service(tmp_path)
    result = svc.create_from_reverse(
        reverse_result("不存在阶段", "不存在活动"),
        taxonomy(),
        trigger_source="RND_VALUE",
        trigger_reason="研发价值触发",
    )
    assert "LIFECYCLE_NOT_MAPPED" in result.scenario.blockers
    assert "ACTIVITY_NOT_MAPPED" in result.scenario.blockers
    assert result.scenario.lifecycle_stage_code == ""
    assert result.scenario.business_activity_code == ""


def test_missing_information_source_and_evidence_are_preserved(tmp_path):
    svc = service(tmp_path)
    result = svc.create_from_reverse(
        reverse_result(
            missing=[
                {
                    "field_name": "system_scale",
                    "reason": "原问题未给出规模",
                    "question": "现场设备规模是多少？",
                    "evidence_needed": ["现场拓扑"],
                    "status": "PENDING",
                    "answer": "",
                }
            ]
        ),
        taxonomy(),
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户高感知问题",
    )
    assert result.scenario.missing_information[0].question == "现场设备规模是多少？"
    assert result.scenario.missing_information[0].status == "PENDING"
    assert result.scenario.source_problem_refs
    assert result.scenario.evidence_refs
    assert all(item.content_ref.startswith("reverse-quality://") for item in result.scenario.evidence_refs)


def test_same_reverse_run_and_trigger_context_is_idempotent(tmp_path):
    svc = service(tmp_path)
    first = svc.create_from_reverse(
        reverse_result(),
        taxonomy(),
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户高感知问题",
    )
    second = svc.create_from_reverse(
        reverse_result(),
        taxonomy(),
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户高感知问题",
    )

    assert first.created is True
    assert second.created is False
    assert first.idempotency_key == second.idempotency_key
    assert first.scenario.scenario_id == second.scenario.scenario_id
    assert len(svc.list_candidates(product_code="PLC")) == 1


def test_different_trigger_context_has_distinct_idempotency_scope(tmp_path):
    svc = service(tmp_path)
    high = svc.create_from_reverse(
        reverse_result(),
        taxonomy(),
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户高感知问题",
    )
    rnd = svc.create_from_reverse(
        reverse_result(),
        taxonomy(),
        trigger_source="RND_VALUE",
        trigger_reason="研发复用价值",
    )
    assert high.scenario.scenario_id != rnd.scenario.scenario_id
    assert len(svc.list_candidates(product_code="PLC")) == 2


def _initializer():
    return P0Initializer(
        manifest_path=PROJECT_ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT / "quality_knowledge/config/plc_fields.yaml",
    )


def test_api_v2_exposes_candidate_create_read_and_list_on_existing_web_chain(tmp_path):
    db_path = tmp_path / "p0.db"
    _initializer().initialize(db_path)
    client = TestClient(create_p0_app(db_path, stage_runner=None))

    created = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": reverse_result(),
            "taxonomy": taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["created"] is True
    scenario_id = payload["scenario"]["scenario_id"]

    repeated = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": reverse_result(),
            "taxonomy": taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["scenario"]["scenario_id"] == scenario_id

    detail = client.get(f"/api/v2/quality-scenarios/candidates/{scenario_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "CANDIDATE"

    listing = client.get(
        "/api/v2/quality-scenarios/candidates",
        params={"product_code": "PLC"},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["scenario_id"] == scenario_id
