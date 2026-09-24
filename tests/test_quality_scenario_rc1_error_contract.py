from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "quality_scenario_error_contract.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db_path)
    return TestClient(create_p0_app(db_path, stage_runner=None))


def _taxonomy() -> dict:
    return {
        "version_id": "STV-QS-ERROR-CONTRACT",
        "lifecycles": [{
            "lifecycle_code": "RUNTIME_EXECUTION",
            "label_zh": "运行执行",
            "enabled": 1,
        }],
        "activities": [{
            "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
            "lifecycle_code": "RUNTIME_EXECUTION",
            "label_zh": "掉电数据保持与上电恢复",
            "objective": "保证掉电后关键数据正确恢复",
            "chain_text": "运行 → 掉电 → 上电 → 恢复",
            "enabled": 1,
        }],
    }


def _reverse_result(*, missing: list[dict] | None = None) -> dict:
    fields = {
        "lifecycle_stage": ("运行执行", "FACT", "cs.phase", 0.9, "CONFIRMED"),
        "business_activity_scene": ("掉电数据保持与上电恢复", "FACT", "cs.description", 0.9, "CONFIRMED"),
        "customer_experience": ("掉电后关键计数丢失", "FACT", "cs.description", 0.9, "CONFIRMED"),
        "quality_risk": ("数据完整性", "INFERRED", "cs.description", 0.7, "PENDING"),
        "expected_quality_state": ("重新上电后计数正确恢复", "INFERRED", "cs.description", 0.8, "PENDING"),
        "trigger_condition": ("运行中异常掉电", "FACT", "cs.description", 0.9, "CONFIRMED"),
    }
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-QS-ERROR-CONTRACT",
        "run_id": "RQRUN-QS-ERROR-CONTRACT",
        "run_seq": 1,
        "identity": {
            "canonical_itr": "ITR-QS-ERROR-CONTRACT",
            "product_code": "PLC",
            "taxonomy_version_id": "STV-QS-ERROR-CONTRACT",
        },
        "fields": {
            key: {
                "value": value,
                "source_type": source_type,
                "evidence_ids": [evidence_id],
                "confidence": confidence,
                "review_status": review_status,
            }
            for key, (value, source_type, evidence_id, confidence, review_status) in fields.items()
        },
        "missing_information": missing or [],
    }


def _create_candidate(client: TestClient, *, missing: list[dict] | None = None) -> dict:
    response = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": _reverse_result(missing=missing),
            "taxonomy": _taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["scenario"]


def _review(client: TestClient, scenario: dict) -> dict:
    response = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/review",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "review_status": "CONFIRMED",
            "reviewer": "QUALITY_OWNER",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["scenario"]


def test_case_a_pending_missing_information_is_a_stable_domain_error(tmp_path: Path):
    client = _client(tmp_path)
    candidate = _create_candidate(client, missing=[{
        "field_name": "system_scale",
        "reason": "缺失",
        "question": "现场设备规模是多少？",
        "evidence_needed": ["现场拓扑"],
        "status": "PENDING",
        "answer": "",
    }])
    reviewed = _review(client, candidate)
    blocked = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/confirm",
        json={
            "expected_scenario_version": reviewed["scenario_version"],
            "quality_confirmed_by": "QUALITY_OWNER",
            "technical_confirmed_by": "RND_OWNER",
        },
    )
    assert blocked.status_code == 400
    assert blocked.json() == {"detail": "SCENARIO_MISSING_INFORMATION_PENDING"}
    saved = client.get(f"/api/v2/quality-scenarios/{candidate['scenario_id']}")
    assert saved.status_code == 200
    assert saved.json()["status"] == "CANDIDATE"


def test_case_b_version_conflict_is_stable_and_state_is_unchanged(tmp_path: Path):
    client = _client(tmp_path)
    candidate = _create_candidate(client)
    changed = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/review",
        json={
            "expected_scenario_version": 1,
            "patch": {"scenario_description": "人工修订"},
            "review_status": "PENDING",
        },
    )
    assert changed.status_code == 200
    stale = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/review",
        json={
            "expected_scenario_version": 1,
            "patch": {"scenario_description": "过期覆盖"},
            "review_status": "PENDING",
        },
    )
    assert stale.status_code == 409
    assert stale.json() == {"detail": "SCENARIO_VERSION_CONFLICT"}


def test_case_c_adjacent_error_contracts_never_expose_raw_exceptions(tmp_path: Path):
    client = _client(tmp_path)
    missing_result = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={"taxonomy": _taxonomy()},
    )
    assert missing_result.status_code == 400
    assert missing_result.json() == {"detail": "REVERSE_QUALITY_RESULT_REQUIRED"}

    missing_taxonomy = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={"reverse_quality_result": _reverse_result()},
    )
    assert missing_taxonomy.status_code == 400
    assert missing_taxonomy.json() == {"detail": "SCENARIO_TAXONOMY_REQUIRED"}

    not_found = client.get("/api/v2/quality-scenarios/NOT-FOUND")
    assert not_found.status_code == 404
    assert not_found.json() == {"detail": "QUALITY_SCENARIO_V1_NOT_FOUND"}

    candidate = _create_candidate(client)
    publish = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/publish",
        json={"expected_scenario_version": candidate["scenario_version"]},
    )
    assert publish.status_code == 400
    assert publish.json() == {"detail": "SCENARIO_PUBLISH_REQUIRES_CONFIRMED"}
