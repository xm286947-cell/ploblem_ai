from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.quality_scenario_candidate_v1_service import CandidateV1Service
from quality_knowledge.quality_scenario_v1 import ScenarioStatus
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.quality_scenario_v1_workflow_service import QualityScenarioV1WorkflowService
from quality_knowledge.web.p0_app import create_p0_app


PROJECT_ROOT=Path(__file__).resolve().parents[1]


def reverse_result(*, missing=None):
    return {
        "result_version":"reverse-quality-v0.1",
        "analysis_id":"RQA-QS04-1",
        "run_id":"RQRUN-QS04-1",
        "run_seq":4,
        "identity":{
            "canonical_itr":"ITR-QS04-001",
            "product_code":"PLC",
            "taxonomy_version_id":"STV-QS04-1",
        },
        "fields":{
            "lifecycle_stage":{
                "value":"运行执行","source_type":"FACT","evidence_ids":["cs.phase"],
                "confidence":0.9,"review_status":"CONFIRMED",
            },
            "business_activity_scene":{
                "value":"掉电数据保持与上电恢复","source_type":"FACT","evidence_ids":["cs.description"],
                "confidence":0.9,"review_status":"CONFIRMED",
            },
            "customer_experience":{
                "value":"掉电后关键计数丢失","source_type":"FACT","evidence_ids":["cs.description"],
                "confidence":0.9,"review_status":"CONFIRMED",
            },
            "quality_risk":{
                "value":"数据完整性","source_type":"INFERRED","evidence_ids":["cs.description"],
                "confidence":0.7,"review_status":"PENDING",
            },
            "expected_quality_state":{
                "value":"重新上电后计数正确恢复","source_type":"INFERRED","evidence_ids":["cs.description"],
                "confidence":0.8,"review_status":"PENDING",
            },
            "trigger_condition":{
                "value":"运行中异常掉电","source_type":"FACT","evidence_ids":["cs.description"],
                "confidence":0.9,"review_status":"CONFIRMED",
            },
        },
        "missing_information":missing or [],
    }


def taxonomy():
    return {
        "version_id":"STV-QS04-1",
        "lifecycles":[{
            "lifecycle_code":"RUNTIME_EXECUTION","label_zh":"运行执行","enabled":1,
        }],
        "activities":[{
            "activity_code":"POWER_LOSS_RETENTION_RECOVERY",
            "lifecycle_code":"RUNTIME_EXECUTION",
            "label_zh":"掉电数据保持与上电恢复",
            "objective":"保证掉电后关键数据正确恢复",
            "chain_text":"运行 → 掉电 → 上电 → 恢复",
            "enabled":1,
        }],
    }


def make_services(tmp_path, *, missing=None):
    repo=SQLiteQualityScenarioV1Repository(tmp_path/"qsv1.db")
    candidate=CandidateV1Service(repo).create_from_reverse(
        reverse_result(missing=missing),
        taxonomy(),
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户高感知问题",
    ).scenario
    return repo, QualityScenarioV1WorkflowService(repo), candidate


def confirm_ready(svc, item):
    reviewed=svc.review_candidate(
        item.scenario_id,
        expected_scenario_version=item.scenario_version,
        patch={},
        review_status="CONFIRMED",
        reviewer="QUALITY_OWNER",
        comment="场景事实完成Review",
    ).scenario
    return svc.confirm(
        reviewed.scenario_id,
        expected_scenario_version=reviewed.scenario_version,
        quality_confirmed_by="QUALITY_OWNER",
        technical_confirmed_by="RND_OWNER",
        confirmation_note="质量确认场景事实，研发确认技术判断",
    ).scenario


def test_candidate_review_update_roundtrip_preserves_source_evidence_and_audit(tmp_path):
    repo,svc,item=make_services(tmp_path)
    source=item.source_problem_refs
    evidence=item.evidence_refs

    result=svc.review_candidate(
        item.scenario_id,
        expected_scenario_version=1,
        patch={"scenario_description":"人工修订后的场景描述"},
        review_status="CONFIRMED",
        reviewer="QUALITY_OWNER",
        comment="修订并确认",
    )
    saved=result.scenario
    assert result.changed is True
    assert saved.scenario_version == 2
    assert saved.scenario_description == "人工修订后的场景描述"
    assert saved.source_problem_refs == source
    assert saved.evidence_refs == evidence
    assert saved.review.review_status.value == "CONFIRMED"

    with repo.connect() as connection:
        versions=connection.execute(
            "SELECT scenario_version FROM quality_scenario_v1_version WHERE scenario_id=? ORDER BY scenario_version",
            (saved.scenario_id,),
        ).fetchall()
    assert [row[0] for row in versions] == [1,2]


def test_identical_review_is_idempotent(tmp_path):
    _,svc,item=make_services(tmp_path)
    first=svc.review_candidate(
        item.scenario_id,
        expected_scenario_version=1,
        patch={"scenario_description":item.scenario_description},
        review_status="PENDING",
    )
    assert first.changed is False
    assert first.scenario.scenario_version == 1


def test_confirm_requires_blockers_to_be_resolved_and_preserves_candidate(tmp_path):
    _,svc,item=make_services(tmp_path)
    reviewed=svc.review_candidate(
        item.scenario_id,
        expected_scenario_version=1,
        patch={"blockers":["MANUAL_CHECK_REQUIRED"]},
        review_status="CONFIRMED",
        reviewer="QUALITY_OWNER",
    ).scenario
    with pytest.raises(ValueError, match="SCENARIO_BLOCKERS_NOT_RESOLVED"):
        svc.confirm(
            reviewed.scenario_id,
            expected_scenario_version=reviewed.scenario_version,
            quality_confirmed_by="QUALITY_OWNER",
            technical_confirmed_by="RND_OWNER",
        )
    assert svc.get(item.scenario_id).status == ScenarioStatus.CANDIDATE


def test_confirm_requires_pending_missing_information_to_be_resolved(tmp_path):
    _,svc,item=make_services(
        tmp_path,
        missing=[{
            "field_name":"system_scale",
            "reason":"缺失",
            "question":"设备规模是多少？",
            "evidence_needed":["现场拓扑"],
            "status":"PENDING",
            "answer":"",
        }],
    )
    reviewed=svc.review_candidate(
        item.scenario_id,
        expected_scenario_version=1,
        review_status="CONFIRMED",
        reviewer="QUALITY_OWNER",
    ).scenario
    with pytest.raises(ValueError, match="SCENARIO_MISSING_INFORMATION_PENDING"):
        svc.confirm(
            reviewed.scenario_id,
            expected_scenario_version=reviewed.scenario_version,
            quality_confirmed_by="QUALITY_OWNER",
            technical_confirmed_by="RND_OWNER",
        )


def test_confirm_requires_both_quality_and_technical_confirmation(tmp_path):
    _,svc,item=make_services(tmp_path)
    reviewed=svc.review_candidate(
        item.scenario_id,
        expected_scenario_version=1,
        review_status="CONFIRMED",
        reviewer="QUALITY_OWNER",
    ).scenario
    with pytest.raises(ValueError, match="SCENARIO_TECHNICAL_CONFIRMATION_REQUIRED"):
        svc.confirm(
            reviewed.scenario_id,
            expected_scenario_version=reviewed.scenario_version,
            quality_confirmed_by="QUALITY_OWNER",
            technical_confirmed_by="",
        )


def test_normal_confirm_and_repeat_confirm_are_idempotent(tmp_path):
    _,svc,item=make_services(tmp_path)
    confirmed=confirm_ready(svc,item)
    assert confirmed.status == ScenarioStatus.CONFIRMED
    assert confirmed.scenario_version == 3
    assert confirmed.confirmation.quality_confirmed_by == "QUALITY_OWNER"
    assert confirmed.confirmation.technical_confirmed_by == "RND_OWNER"

    repeated=svc.confirm(
        confirmed.scenario_id,
        expected_scenario_version=2,
        quality_confirmed_by="QUALITY_OWNER",
        technical_confirmed_by="RND_OWNER",
        confirmation_note="质量确认场景事实，研发确认技术判断",
    )
    assert repeated.changed is False
    assert repeated.scenario.scenario_version == 3


def test_reject_is_human_terminal_and_audited(tmp_path):
    _,svc,item=make_services(tmp_path)
    rejected=svc.reject(
        item.scenario_id,
        expected_scenario_version=1,
        reviewer="QUALITY_OWNER",
        comment="不具备场景沉淀价值",
    ).scenario
    assert rejected.status == ScenarioStatus.REJECTED
    assert rejected.scenario_version == 2
    assert rejected.review.review_status.value == "REJECTED"
    assert rejected.review.reviewer == "QUALITY_OWNER"


def test_stale_review_is_rejected_by_optimistic_concurrency(tmp_path):
    _,svc,item=make_services(tmp_path)
    saved=svc.review_candidate(
        item.scenario_id,
        expected_scenario_version=1,
        patch={"scenario_description":"V2"},
        review_status="PENDING",
    ).scenario
    assert saved.scenario_version == 2
    with pytest.raises(ValueError, match="SCENARIO_VERSION_CONFLICT"):
        svc.review_candidate(
            item.scenario_id,
            expected_scenario_version=1,
            patch={"scenario_description":"stale overwrite"},
            review_status="PENDING",
        )
    assert svc.get(item.scenario_id).scenario_description == "V2"


def test_publish_creates_version_and_repeat_publish_is_idempotent(tmp_path):
    _,svc,item=make_services(tmp_path)
    confirmed=confirm_ready(svc,item)
    first=svc.publish(
        confirmed.scenario_id,
        expected_scenario_version=confirmed.scenario_version,
        published_by="QUALITY_OWNER",
    )
    assert first.changed is True
    assert first.scenario.status == ScenarioStatus.PUBLISHED
    assert first.scenario.scenario_version == 4
    assert first.scenario.version.published_at

    repeat=svc.publish(
        confirmed.scenario_id,
        expected_scenario_version=confirmed.scenario_version,
        published_by="QUALITY_OWNER",
    )
    assert repeat.changed is False
    assert repeat.scenario.scenario_version == 4


class PublishFailRepository(SQLiteQualityScenarioV1Repository):
    def save(self, scenario, *, actor="HUMAN"):
        if scenario.status == ScenarioStatus.PUBLISHED:
            raise RuntimeError("SIMULATED_PUBLISH_FAILURE")
        return super().save(scenario, actor=actor)


def test_publish_failure_leaves_original_confirmed(tmp_path):
    repo=PublishFailRepository(tmp_path/"qsv1.db")
    item=CandidateV1Service(repo).create_from_reverse(
        reverse_result(),
        taxonomy(),
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户高感知问题",
    ).scenario
    svc=QualityScenarioV1WorkflowService(repo)
    confirmed=confirm_ready(svc,item)
    with pytest.raises(RuntimeError, match="SIMULATED_PUBLISH_FAILURE"):
        svc.publish(
            confirmed.scenario_id,
            expected_scenario_version=confirmed.scenario_version,
        )
    stored=repo.get(confirmed.scenario_id)
    assert stored is not None
    assert stored.status == ScenarioStatus.CONFIRMED
    assert stored.scenario_version == confirmed.scenario_version


def _initializer():
    return P0Initializer(
        manifest_path=PROJECT_ROOT/"quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT/"quality_knowledge/config/plc_fields.yaml",
    )


def test_api_v2_review_confirm_publish_flow(tmp_path):
    db_path=tmp_path/"p0.db"
    _initializer().initialize(db_path)
    client=TestClient(create_p0_app(db_path, stage_runner=None))

    created=client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result":reverse_result(),
            "taxonomy":taxonomy(),
            "trigger_source":"HIGH_PERCEPTION",
            "trigger_reason":"客户高感知问题",
        },
    )
    assert created.status_code == 200, created.text
    scenario=created.json()["scenario"]
    scenario_id=scenario["scenario_id"]

    reviewed=client.post(
        f"/api/v2/quality-scenarios/{scenario_id}/review",
        json={
            "expected_scenario_version":scenario["scenario_version"],
            "patch":{"scenario_description":"API人工修订"},
            "review_status":"CONFIRMED",
            "reviewer":"QUALITY_OWNER",
            "comment":"Review完成",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    reviewed_scenario=reviewed.json()["scenario"]

    confirmed=client.post(
        f"/api/v2/quality-scenarios/{scenario_id}/confirm",
        json={
            "expected_scenario_version":reviewed_scenario["scenario_version"],
            "quality_confirmed_by":"QUALITY_OWNER",
            "technical_confirmed_by":"RND_OWNER",
            "confirmation_note":"共同确认",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_scenario=confirmed.json()["scenario"]
    assert confirmed_scenario["status"] == "CONFIRMED"

    published=client.post(
        f"/api/v2/quality-scenarios/{scenario_id}/publish",
        json={
            "expected_scenario_version":confirmed_scenario["scenario_version"],
            "published_by":"QUALITY_OWNER",
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["scenario"]["status"] == "PUBLISHED"

    detail=client.get(f"/api/v2/quality-scenarios/{scenario_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "PUBLISHED"

    repeated=client.post(
        f"/api/v2/quality-scenarios/{scenario_id}/publish",
        json={
            "expected_scenario_version":confirmed_scenario["scenario_version"],
            "published_by":"QUALITY_OWNER",
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["changed"] is False


def test_api_v2_version_conflict_returns_409(tmp_path):
    db_path=tmp_path/"p0.db"
    _initializer().initialize(db_path)
    client=TestClient(create_p0_app(db_path, stage_runner=None))
    created=client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result":reverse_result(),
            "taxonomy":taxonomy(),
            "trigger_source":"RND_VALUE",
            "trigger_reason":"研发价值触发",
        },
    ).json()["scenario"]
    first=client.post(
        f"/api/v2/quality-scenarios/{created['scenario_id']}/review",
        json={
            "expected_scenario_version":1,
            "patch":{"scenario_description":"first"},
            "review_status":"PENDING",
        },
    )
    assert first.status_code == 200
    stale=client.post(
        f"/api/v2/quality-scenarios/{created['scenario_id']}/review",
        json={
            "expected_scenario_version":1,
            "patch":{"scenario_description":"stale"},
            "review_status":"PENDING",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "SCENARIO_VERSION_CONFLICT"
