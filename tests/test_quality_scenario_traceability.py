from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.quality_scenario_traceability_service import (
    QualityScenarioTraceabilityService,
)
from quality_knowledge.quality_scenario_v1 import QualityScenarioV1
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "quality_knowledge" / "web"


def _initializer() -> P0Initializer:
    return P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )


def _reverse_result():
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-QS06-1",
        "run_id": "RQRUN-QS06-1",
        "run_seq": 8,
        "identity": {
            "canonical_itr": "ITR-QS06-001",
            "product_code": "PLC",
            "taxonomy_version_id": "STV-QS06-1",
        },
        "fields": {
            "lifecycle_stage": {
                "value": "运行执行",
                "source_type": "FACT",
                "evidence_ids": ["cs.phase"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "business_activity_scene": {
                "value": "掉电数据保持与上电恢复",
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
                "value": "重新上电后关键计数正确恢复",
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
        "missing_information": [],
    }


def _taxonomy():
    return {
        "version_id": "STV-QS06-1",
        "lifecycles": [
            {"lifecycle_code": "RUNTIME_EXECUTION", "label_zh": "运行执行", "enabled": 1}
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


def _setup(tmp_path):
    db_path = tmp_path / "qs06.db"
    _initializer().initialize(db_path)
    client = TestClient(create_p0_app(db_path, stage_runner=None))
    response = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": _reverse_result(),
            "taxonomy": _taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
        },
    )
    assert response.status_code == 200, response.text
    return db_path, client, response.json()["scenario"]


def _publish(client, scenario):
    reviewed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/review",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "patch": {},
            "review_status": "CONFIRMED",
            "reviewer": "QUALITY_OWNER",
            "comment": "Evidence核对完成",
        },
    ).json()["scenario"]
    confirmed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/confirm",
        json={
            "expected_scenario_version": reviewed["scenario_version"],
            "quality_confirmed_by": "QUALITY_OWNER",
            "technical_confirmed_by": "RND_OWNER",
            "confirmation_note": "来源和技术判断已确认",
        },
    ).json()["scenario"]
    response = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/publish",
        json={
            "expected_scenario_version": confirmed["scenario_version"],
            "published_by": "QUALITY_OWNER",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["scenario"]


def test_golden_published_traceability_passes_without_fabricating_source_text(tmp_path):
    _, client, scenario = _setup(tmp_path)
    published = _publish(client, scenario)

    response = client.get(
        f"/api/v2/quality-scenarios/{published['scenario_id']}/traceability"
    )
    assert response.status_code == 200, response.text
    trace = response.json()

    assert trace["integrity"]["status"] == "PASS"
    assert trace["integrity"]["issues"] == []
    assert trace["sources"][0]["source_ref"] == "ITR:ITR-QS06-001"
    assert trace["sources"][0]["evidence_ids"]
    assert trace["evidence"]
    assert all(item["source_resolved"] for item in trace["evidence"])
    assert all(item["supports_valid"] for item in trace["evidence"])
    assert all(item["content_status"] == "CONTROLLED_CONTENT_REF" for item in trace["evidence"])
    assert all(item["source_text"] == "" for item in trace["evidence"])
    assert all(item["content_ref"].startswith("reverse-quality://") for item in trace["evidence"])


def test_source_problem_reverse_lookup_finds_v1_scenario(tmp_path):
    _, client, scenario = _setup(tmp_path)
    response = client.get(
        "/api/v2/quality-scenario-sources/scenarios",
        params={"source_ref": "ITR:ITR-QS06-001"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_ref"] == "ITR:ITR-QS06-001"
    assert body["total"] == 1
    assert body["items"][0]["scenario_id"] == scenario["scenario_id"]

    missing = client.get(
        "/api/v2/quality-scenario-sources/scenarios",
        params={"source_ref": "ITR:DOES-NOT-EXIST"},
    )
    assert missing.status_code == 200
    assert missing.json()["total"] == 0


def test_unknown_support_field_blocks_trace_integrity(tmp_path):
    db_path, client, scenario = _setup(tmp_path)
    repository = SQLiteQualityScenarioV1Repository(db_path)
    item = repository.get(scenario["scenario_id"])
    assert item is not None

    payload = item.model_dump(mode="json")
    payload["evidence_refs"][0]["supports"] = ["UNKNOWN_V1_FIELD"]
    changed = QualityScenarioV1.model_validate(payload)
    repository.save(changed, actor="HUMAN")

    trace = client.get(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/traceability"
    ).json()
    assert trace["integrity"]["status"] == "BLOCKED"
    assert any(
        issue["code"] == "UNKNOWN_SUPPORT_FIELD"
        and issue["detail"] == "UNKNOWN_V1_FIELD"
        for issue in trace["integrity"]["issues"]
    )
    evidence = next(
        x for x in trace["evidence"]
        if "UNKNOWN_V1_FIELD" in x["unknown_supports"]
    )
    assert evidence["supports_valid"] is False


def test_inline_source_text_is_distinguished_from_controlled_content_ref(tmp_path):
    db_path, _, scenario = _setup(tmp_path)
    repository = SQLiteQualityScenarioV1Repository(db_path)
    item = repository.get(scenario["scenario_id"])
    assert item is not None
    payload = item.model_dump(mode="json")
    payload["evidence_refs"][0]["source_text"] = "原始问题中明确记录：运行中异常掉电。"
    changed = QualityScenarioV1.model_validate(payload)
    repository.save(changed, actor="HUMAN")

    trace = QualityScenarioTraceabilityService(repository).trace(scenario["scenario_id"])
    assert trace["integrity"]["status"] == "PASS"
    assert any(
        x["content_status"] == "INLINE_SOURCE_TEXT"
        and x["source_text"].startswith("原始问题中明确记录")
        for x in trace["evidence"]
    )
    assert any(
        x["content_status"] == "CONTROLLED_CONTENT_REF"
        for x in trace["evidence"][1:]
    )


def test_traceability_api_404_and_source_ref_validation(tmp_path):
    _, client, _ = _setup(tmp_path)
    missing = client.get("/api/v2/quality-scenarios/NOT-V1/traceability")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "QUALITY_SCENARIO_V1_NOT_FOUND"

    source = client.get("/api/v2/quality-scenario-sources/scenarios")
    assert source.status_code == 400
    assert source.json()["detail"] == "QUALITY_SCENARIO_SOURCE_REF_REQUIRED"


def test_p03_consumes_traceability_without_adding_evidence_page():
    html = (WEB / "templates/p0_quality_scenario_detail.html").read_text(encoding="utf-8")
    js = (WEB / "static/p0_scenario_detail.js").read_text(encoding="utf-8")
    pages = (WEB / "p0_pages.py").read_text(encoding="utf-8")

    assert "Evidence完整性" in html
    assert "/traceability" in js
    assert "CONTROLLED_CONTENT_REF" in js
    assert "不代表页面已读取原文" in js
    assert "supports_valid" not in html  # technical check stays behind the concise status strip.
    assert "/p0/evidence" not in pages
    assert "/p0/quality-scenario-evidence" not in pages


def test_traceability_is_read_only_and_does_not_touch_reverse_runtime():
    service = (ROOT / "quality_knowledge/quality_scenario_traceability_service.py").read_text(encoding="utf-8")
    assert "AIClient" not in service
    assert "Unified" not in service
    assert "reverse_quality_store" not in service
    assert "source_text =" not in service
    assert "INSERT " not in service
    assert "UPDATE " not in service
    assert "DELETE " not in service
