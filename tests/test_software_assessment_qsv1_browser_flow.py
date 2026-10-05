from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.materials import MaterialRepository
from quality_knowledge.repositories.v1_repository import IssueKnowledgeRepository
from quality_knowledge.scenario_generation import ScenarioGenerationService
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.web.app import create_app
from quality_knowledge.web.software_assessment_qsv1 import SoftwareAssessmentQSV1Flow


def _reverse_result(bundle):
    provenance = {
        **bundle,
        "snapshot_id": (bundle.get("snapshot_metadata") or {}).get("snapshot_id") or "",
        "source_refs": bundle.get("sources") or [],
    }
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-W4-GOLDEN",
        "run_id": "RQRUN-W4-GOLDEN",
        "run_seq": 1,
        "identity": {
            "canonical_itr": bundle["selected_issue"]["business_issue_id"],
            "product_code": bundle["selected_issue"]["product_code"],
            "taxonomy_version_id": "TAX-W4",
            "bundle_id": bundle["bundle_id"],
            "bundle_revision": bundle["bundle_revision"],
            "source_snapshot_id": provenance["snapshot_id"],
        },
        "bundle_provenance": provenance,
        "fields": {
            "lifecycle_stage": {"value": "运行执行", "source_type": "FACT", "evidence_ids": ["w4.phase"]},
            "business_activity_scene": {"value": "掉电数据保持与上电恢复", "source_type": "FACT", "evidence_ids": ["w4.activity"]},
            "customer_experience": {"value": "掉电后关键数据丢失", "source_type": "FACT", "evidence_ids": ["w4.symptom"]},
            "quality_risk": {"value": "数据完整性", "source_type": "INFERRED", "evidence_ids": ["w4.symptom"]},
            "expected_quality_state": {"value": "重新上电后关键数据正确恢复", "source_type": "INFERRED", "evidence_ids": ["w4.solution"]},
            "trigger_condition": {"value": "运行中异常掉电", "source_type": "FACT", "evidence_ids": ["w4.trigger"]},
        },
        "missing_information": [],
    }


def _setup_flow(tmp_path):
    db = tmp_path / "mature.db"
    materials = MaterialRepository(db)
    assessment_id = materials.add_material(
        materials.group("SW-OPS"), "ITR20261041001",
        {"问题信息_问题描述": "软件考核记录中的掉电恢复问题", "问题信息_产品型号": "PLC-X"},
        "source.xlsx", "assessment", 3,
    )[0]
    materials.add_material(
        materials.group("ITR-CS"), "ITR20261041001CS",
        {"问题信息_问题原因定位": "保存时序未保证", "问题处理结果_问题解决方案": "增加掉电保护与恢复校验"},
        "source.xlsx", "resolution", 4,
    )
    IssueKnowledgeRepository(db)
    ScenarioRepository(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id,status) VALUES(?,?,?,?)",
            ("QK-W4-41001", "PLC", "ITR20261041001", "ACTIVE"),
        )

    class IssueService:
        repository = None

        @staticmethod
        def get_latest_analysis(_knowledge_id, _stage):
            return None

        @staticmethod
        def get_issue(_knowledge_id):
            return {"normalized_json": json.dumps({"issue_fact": {"description": "测试问题"}})}

    IssueService.materials = materials
    scenarios = ScenarioRepository(db)
    generation = ScenarioGenerationService(IssueService(), scenarios, tmp_path)
    taxonomy = {
        "version_id": "TAX-W4",
        "lifecycles": [{"lifecycle_code": "RUNTIME_EXECUTION", "label_zh": "运行执行", "enabled": 1}],
        "activities": [{"activity_code": "POWER_LOSS_RETENTION_RECOVERY", "lifecycle_code": "RUNTIME_EXECUTION", "label_zh": "掉电数据保持与上电恢复", "objective": "恢复数据", "chain_text": "运行 → 掉电 → 恢复", "enabled": 1}],
    }
    scenarios.taxonomy_active = lambda _product: taxonomy
    calls = {"count": 0}

    def reverse_from_frozen_bundle(bundle, *, taxonomy=None, retry_failed=False):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("TEST_PROVIDER_TEMPORARILY_UNAVAILABLE")
        return _reverse_result(bundle)

    generation.reverse_quality_from_bundle = reverse_from_frozen_bundle
    flow = SoftwareAssessmentQSV1Flow(generation, scenarios, str(tmp_path / "qsv1.db"))
    return flow, assessment_id


def test_browser_flow_preserves_bundle_for_retry_and_renders_persisted_duplicate(tmp_path):
    flow, assessment_id = _setup_flow(tmp_path)
    preview = flow.preview([assessment_id], "HIGH_PERCEPTION", "客户问题影响关键数据")
    assert preview["items"][0]["state"] == "READY", preview["items"][0]["bundle"]["missing_information"]
    assert preview["items"][0]["bundle"]["source_status"]["MISSED_TEST"] == "MISSING"

    task = flow.start([assessment_id], "HIGH_PERCEPTION", "客户问题影响关键数据")
    frozen_key = task["items"][0]["bundle_key"]
    flow.run_task(task["task_id"])
    failed = flow.get_task(task["task_id"])
    assert failed["items"][0]["state"] == "PROVIDER_FAILED"
    assert flow.bundle_store.get(frozen_key["bundle_id"], frozen_key["bundle_revision"]) is not None

    retry = flow.retry(task["task_id"])
    assert retry["items"][0]["bundle_key"] == frozen_key
    flow.run_task(retry["task_id"])
    completed = flow.get_task(retry["task_id"])
    assert completed["items"][0]["state"] == "CANDIDATE_CREATED"
    persisted = completed["items"][0]["scenario"]
    assert persisted["status"] == "CANDIDATE"

    duplicate = flow.start([assessment_id], "HIGH_PERCEPTION", "客户问题影响关键数据")
    assert duplicate["items"][0]["state"] == "EXISTING_CANDIDATE"
    assert duplicate["items"][0]["scenario"]["scenario_id"] == persisted["scenario_id"]
    assert any(
        ref["evidence_type"] == "QSV1_REVERSE_QUALITY_RESULT"
        for ref in duplicate["items"][0]["scenario"]["evidence_refs"]
    )


def test_software_assessment_page_mounts_w4_controls_and_reuses_qsv1_routes(tmp_path, monkeypatch):
    monkeypatch.delenv("QUALITY_SCENARIO_V1_DB_PATH", raising=False)
    client = TestClient(create_app(tmp_path / "mature.db"))
    page = client.get("/software-assessment")
    assert page.status_code == 200
    assert "data-sa-qsv1" in page.text
    assert "/p0/static/software_assessment_qsv1.js" in page.text or "/static/software_assessment_qsv1.js" in page.text
    assert "QSV1 Candidate" in page.text
    template = (Path(__file__).resolve().parents[1] / "quality_knowledge/web/templates/software_assessment_workbench.html").read_text(encoding="utf-8")
    assert "data-assessment-select" in template
    rejected = client.post(
        "/api/v2/software-assessment/quality-scenario/preview",
        json={"material_ids": [], "trigger_source": "HIGH_PERCEPTION", "trigger_reason": "test"},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "SOFTWARE_ASSESSMENT_SELECTION_REQUIRED"
    assert client.get("/p0/quality-scenarios/workbench").status_code == 200
    assert client.get("/p0/quality-scenarios").status_code == 200
    assert client.get("/p0/quality-scenarios/library/QSV1-NOT-FOUND").status_code == 200
