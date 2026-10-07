from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app
from tools.build_quality_scenario_test_fixture import (
    FIXTURE_VERSION,
    advance_g5,
    build_fixture,
)


def _case(manifest, case_id):
    return next(item for item in manifest["cases"] if item["case_id"] == case_id)


def _preview(client, material_id):
    response = client.post(
        "/api/v2/software-assessment/quality-scenario/preview",
        json={
            "material_ids": [material_id],
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "W4_MAC_FUNCTIONAL_GOLDEN",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def test_fixture_builds_only_source_side_and_exposes_five_assessment_cases(tmp_path, monkeypatch):
    db = tmp_path / "w4-fixture.db"
    manifest = build_fixture(db)
    assert manifest["contract"] == FIXTURE_VERSION
    assert manifest["synthetic_business_data"] is True
    assert manifest["direct_qsv1_candidate_write"] is False
    assert manifest["direct_qsv1_publish_write"] is False
    assert len(manifest["cases"]) == 5

    monkeypatch.setenv("QUALITY_SCENARIO_V1_DB_PATH", str(db))
    client = TestClient(create_app(db))

    page = client.get("/software-assessment")
    assert page.status_code == 200
    for text in (
        "异常掉电后关键配方参数丢失",
        "通信链路波动后设备重连超时",
        "升级后部分历史工程无法正常加载",
        "高频写入后诊断日志占满存储空间",
        "长时间运行后任务调度抖动导致周期超时",
    ):
        assert text in page.text

    workflow = client.get("/api/v2/quality-scenario-workflow/v1/quality-scenarios")
    assert workflow.status_code == 200
    assert workflow.json() == {"items": [], "total": 0}


def test_fixture_drives_g1_g2_g3_preview_states_without_writing_candidates(tmp_path, monkeypatch):
    db = tmp_path / "w4-fixture.db"
    manifest = build_fixture(db)
    monkeypatch.setenv("QUALITY_SCENARIO_V1_DB_PATH", str(db))
    client = TestClient(create_app(db))

    g1 = _preview(client, _case(manifest, "G1_COMPLETE")["software_assessment_material_id"])
    assert g1["state"] == "READY"
    assert g1["bundle"]["source_status"]["RESOLUTION"] == "PRESENT"
    assert g1["bundle"]["source_status"]["ITR"] == "PRESENT"
    assert g1["bundle"]["source_status"]["MISSED_TEST"] == "PRESENT"

    g2 = _preview(client, _case(manifest, "G2_NO_MISSED_TEST")["software_assessment_material_id"])
    assert g2["state"] == "READY"
    assert g2["bundle"]["source_status"]["RESOLUTION"] == "PRESENT"
    assert g2["bundle"]["source_status"]["ITR"] == "PRESENT"
    assert g2["bundle"]["source_status"]["MISSED_TEST"] == "MISSING"

    g3 = _preview(client, _case(manifest, "G3_CONFLICT")["software_assessment_material_id"])
    assert g3["state"] == "INFORMATION_REQUIRED"
    assert g3["bundle"]["source_status"]["RESOLUTION"] == "CONFLICT"
    assert g3["bundle"]["source_status"]["ITR"] == "PRESENT"

    g4 = _preview(client, _case(manifest, "G4_DUPLICATE_GENERATE")["software_assessment_material_id"])
    assert g4["state"] == "READY"
    assert g4["bundle"]["source_status"]["RESOLUTION"] == "PRESENT"
    assert g4["bundle"]["source_status"]["ITR"] == "PRESENT"
    assert g4["bundle"]["source_status"]["MISSED_TEST"] == "PRESENT"

    workflow = client.get("/api/v2/quality-scenario-workflow/v1/quality-scenarios")
    assert workflow.json()["total"] == 0


def test_g5_source_revision_changes_bundle_revision_and_preserves_source_only_contract(tmp_path, monkeypatch):
    db = tmp_path / "w4-fixture.db"
    manifest = build_fixture(db)
    monkeypatch.setenv("QUALITY_SCENARIO_V1_DB_PATH", str(db))
    client = TestClient(create_app(db))
    g5_id = _case(manifest, "G5_SOURCE_REVISION")["software_assessment_material_id"]

    before = _preview(client, g5_id)
    assert before["state"] == "READY"
    assert before["bundle"]["source_status"]["ITR"] == "PRESENT"
    before_revision = before["bundle"]["bundle_revision"]

    advance_g5(db)
    after = _preview(client, g5_id)
    assert after["state"] == "READY"
    assert after["bundle"]["source_status"]["ITR"] == "PRESENT"
    assert after["bundle"]["bundle_revision"] != before_revision

    workflow = client.get("/api/v2/quality-scenario-workflow/v1/quality-scenarios")
    assert workflow.json()["total"] == 0
