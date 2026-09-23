from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.p1 import ForwardRiskService
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).parents[1]


def prepared(tmp_path):
    db = tmp_path / "p1.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    repository = P0Repository(db)
    issue = repository.save_issue(
        knowledge_id="K-RISK-1", business_issue_id="ITR-RISK-1",
        raw_json={"ITR单号": "ITR-RISK-1", "客户": "不应外发"},
        normalized_snapshot={"ISSUE_FACT": {
            "business_issue_id": "ITR-RISK-1", "title": "版本组合不一致导致通信中断",
            "description": "PLC 与平台软件版本组合不兼容，升级后接口通信中断",
            "product": "PLC", "platform": "IFA", "severity": "H",
            "impact": "客户产线停止",
        }},
        mapping_config_id="MAP-PLC-V1", mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1", source_file_sha256="risk-source",
        sheet_name="issues", row_number=2, product_id="PRODUCT-PLC",
    )
    repository.save_analysis_set({
        "analysis_set_id": "AS-RISK-1", "knowledge_id": "K-RISK-1",
        "issue_version_id": issue["issue_version_id"], "taxonomy_version_id": "QUALITY_TAXONOMY_P0_V1",
        "input_hash": "risk-input", "status": "COMPLETED",
        "tags": [
            {"stage": "occurrence", "axis": "DOMAIN", "tag_code": "EMBEDDED", "confidence": .9, "source_type": "SOURCE_DATA"},
            {"stage": "occurrence", "axis": "LIFECYCLE", "tag_code": "DESIGN", "confidence": .9, "source_type": "SOURCE_DATA"},
        ],
        "values": [
            {"stage": "occurrence", "value_path": "trigger_condition", "value": "不同产品版本组合升级", "confidence": .9, "source_type": "SOURCE_DATA"},
            {"stage": "occurrence", "value_path": "failure_mechanism", "value": "接口协议版本不兼容", "confidence": .9, "source_type": "SOURCE_DATA"},
        ],
        "mrc": [
            {"side": "OCCURRENCE", "mrc_code": "COMPATIBILITY_NOT_ANALYZED", "confidence": .9, "source_type": "SOURCE_DATA"},
            {"side": "ESCAPE", "mrc_code": "RELEASE_GATE_FAILED", "confidence": .8, "source_type": "SOURCE_DATA"},
        ],
        "capability_gaps": [{
            "capability_axis": "QUALITY_ENGINEERING", "capability_code": "EMBEDDED_HW_SW_CO_DESIGN",
            "governance_scope": "PRODUCT", "control_status": "NOT_DEFINED", "confidence": .9, "source_type": "SOURCE_DATA",
            "details": {"first_action": "建立产品版本兼容性矩阵", "validation_scenario": "覆盖所有支持版本组合的升级回归"},
        }],
        "evidence": [{"stage": "occurrence", "target_path": "failure_mechanism", "source_type": "SOURCE_DATA",
                      "source_ref": "description", "excerpt": "版本组合不兼容", "confidence": .9}],
    })
    return db, repository


def add_related_issue(repository):
    issue = repository.save_issue(
        knowledge_id="K-RISK-2", business_issue_id="ITR-RISK-2", raw_json={"ITR单号": "ITR-RISK-2"},
        normalized_snapshot={"ISSUE_FACT": {
            "business_issue_id": "ITR-RISK-2", "title": "组合升级后平台连接失败",
            "description": "机器人控制器与 IFA 版本组合不匹配，升级后连接失败",
            "product": "ROBOT", "platform": "IFA", "severity": "M", "impact": "设备无法上线",
        }},
        mapping_config_id="MAP-PLC-V1", mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1", source_file_sha256="risk-source-2",
        sheet_name="issues", row_number=3, product_id="PRODUCT-PLC",
    )
    repository.save_analysis_set({
        "analysis_set_id": "AS-RISK-2", "knowledge_id": "K-RISK-2",
        "issue_version_id": issue["issue_version_id"], "taxonomy_version_id": "QUALITY_TAXONOMY_P0_V1",
        "input_hash": "risk-input-2", "status": "COMPLETED",
        "tags": [
            {"stage": "occurrence", "axis": "DOMAIN", "tag_code": "SYSTEM_INTEGRATION", "confidence": .9, "source_type": "SOURCE_DATA"},
            {"stage": "occurrence", "axis": "LIFECYCLE", "tag_code": "TEST", "confidence": .9, "source_type": "SOURCE_DATA"},
        ],
        "values": [
            {"stage": "occurrence", "value_path": "trigger_condition", "value": "组合版本升级", "confidence": .9, "source_type": "SOURCE_DATA"},
            {"stage": "occurrence", "value_path": "failure_mechanism", "value": "接口协议版本不兼容", "confidence": .9, "source_type": "SOURCE_DATA"},
        ],
        "mrc": [{"side": "OCCURRENCE", "mrc_code": "COMPATIBILITY_NOT_ANALYZED", "confidence": .9, "source_type": "SOURCE_DATA"}],
        "capability_gaps": [{
            "capability_axis": "QUALITY_MANAGEMENT", "capability_code": "VERSION_BASELINE_CONTROL",
            "governance_scope": "CROSS_PRODUCT", "control_status": "NOT_DEFINED", "confidence": .9,
            "source_type": "SOURCE_DATA", "details": {"first_action": "建立跨产品版本发布基线"},
        }],
    })


def test_confirmed_issue_publishes_versioned_risk_case_and_is_idempotent(tmp_path):
    _, repository = prepared(tmp_path)
    service = ForwardRiskService(repository)
    first = service.publish_issue("K-RISK-1", published_by="quality-owner")
    second = service.publish_issue("K-RISK-1", published_by="quality-owner")
    assert first["outcome"] == "PUBLISHED" and first["version_no"] == 1
    assert second["outcome"] == "ALREADY_PUBLISHED"
    cases = service.list_cases()
    assert cases["total"] == 1
    case = cases["items"][0]["case"]
    assert case["failure_mechanism"] == "接口协议版本不兼容"
    assert case["preventive_controls"] == ["建立产品版本兼容性矩阵"]
    assert case["risk_level"] == "HIGH"


def test_p1_database_schema_version_is_distinct_from_analysis_contract(tmp_path):
    db, repository = prepared(tmp_path)
    with repository.connect() as connection:
        schema_version = connection.execute(
            "SELECT schema_version FROM system_database_metadata LIMIT 1"
        ).fetchone()[0]
        contract_version = connection.execute(
            "SELECT contract_version FROM analysis_set WHERE analysis_set_id='AS-RISK-1'"
        ).fetchone()[0]
    assert db.exists() and schema_version == "2.1.0"
    assert contract_version == "2.0.0"


def test_forward_assessment_matches_mechanism_and_checks_control_coverage(tmp_path):
    _, repository = prepared(tmp_path)
    service = ForwardRiskService(repository)
    service.publish_issue("K-RISK-1", published_by="quality-owner")
    report = service.create_assessment(
        project_name="新一代 PLC", product_code="PLC", assessment_stage="DESIGN",
        material_type="DESIGN_SPEC", material_name="系统设计说明",
        material_text=("系统支持 PLC 与 IFA 多版本组合升级，接口协议必须向后兼容。"
                       "设计阶段建立产品版本兼容性矩阵，并覆盖所有支持版本组合的升级回归。"),
        created_by="project-quality",
    )
    assert report["matched_risk_count"] == 1
    risk = report["risks"][0]
    assert risk["match_basis"]["matched_keywords"]
    assert "产品一致：PLC" in risk["match_basis"]["matched_dimensions"]
    assert "生命周期一致：DESIGN" in risk["match_basis"]["matched_dimensions"]
    assert not any(len(word) == 2 and any("\u4e00" <= char <= "\u9fff" for char in word)
                   for word in risk["match_basis"]["matched_keywords"])
    assert risk["coverage_status"] == "COVERED"
    assert risk["risk_level"] == "HIGH"
    stored = service.get_assessment(report["assessment_id"])
    assert stored["version"]["report"]["risks"][0]["risk_name"] == "版本组合不一致导致通信中断"


def test_negated_control_is_not_counted_as_covered(tmp_path):
    _, repository = prepared(tmp_path)
    service = ForwardRiskService(repository)
    service.publish_issue("K-RISK-1", published_by="quality-owner")
    report = service.create_assessment(
        project_name="否定语义项目", product_code="PLC", assessment_stage="DESIGN",
        material_type="DESIGN_SPEC", material_name="设计说明",
        material_text=("项目涉及 PLC 与 IFA 多版本组合升级、回退、异常恢复和接口协议兼容风险。"
                       "当前尚未定义产品版本兼容性矩阵，也缺少全部支持版本组合的升级回归方案。"),
        created_by="project-quality",
    )
    assert report["risks"][0]["coverage_status"] == "NOT_FOUND"
    assert report["risks"][0]["existing_controls"] == []


def test_short_material_returns_insufficient_info_and_supports_human_review(tmp_path):
    _, repository = prepared(tmp_path)
    service = ForwardRiskService(repository)
    service.publish_issue("K-RISK-1", published_by="quality-owner")
    report = service.create_assessment(
        project_name="试制项目", product_code="PLC", assessment_stage="REQUIREMENT",
        material_type="REQUIREMENT", material_name="需求片段",
        material_text="PLC 与 IFA 版本兼容升级", created_by="project-quality",
    )
    risk = report["risks"][0]
    assert risk["coverage_status"] == "INSUFFICIENT_INFO"
    assert risk["open_questions"]
    reviewed = service.review_result(
        risk["risk_result_id"], status="CONFIRMED", note="需补充兼容矩阵", reviewed_by="reviewer"
    )
    assert reviewed["review_status"] == "CONFIRMED"
    stored = service.get_assessment(report["assessment_id"])
    assert stored["status"] == "COMPLETED"
    stored_risk = stored["version"]["report"]["risks"][0]
    assert stored_risk["review_status"] == "CONFIRMED"
    assert stored_risk["review"]["note"] == "需补充兼容矩阵"


def test_external_pattern_does_not_expose_issue_id_or_evidence(tmp_path):
    _, repository = prepared(tmp_path)
    service = ForwardRiskService(repository)
    service.publish_issue(
        "K-RISK-1", publication_level="EXTERNAL_PATTERN", published_by="quality-owner"
    )
    exported = service.list_cases(publication_level="EXTERNAL_PATTERN")["items"][0]
    text = str(exported)
    assert "K-RISK-1" not in text and "ITR-RISK-1" not in text
    assert "版本组合不一致导致通信中断" not in text
    assert "PLC 与平台软件版本组合不兼容" not in text
    assert "客户产线停止" not in text
    assert "建立产品版本兼容性矩阵" not in text
    assert exported["case"]["related_issues"] == []
    assert exported["case"]["evidence"] == []


def test_internal_redacted_uses_opaque_issue_reference_and_removes_evidence(tmp_path):
    _, repository = prepared(tmp_path)
    service = ForwardRiskService(repository)
    service.publish_issue(
        "K-RISK-1", publication_level="INTERNAL_REDACTED", published_by="quality-owner"
    )
    exported = service.list_cases(publication_level="INTERNAL_REDACTED")["items"][0]
    text = str(exported)
    assert "K-RISK-1" not in text and "ITR-RISK-1" not in text
    assert exported["case"]["related_issues"][0].startswith("ISSUE-")
    assert exported["case"]["evidence"] == []


def test_related_issues_can_merge_into_one_versioned_risk_pattern_and_filter(tmp_path):
    _, repository = prepared(tmp_path)
    add_related_issue(repository)
    service = ForwardRiskService(repository)
    first = service.publish_issue("K-RISK-1", published_by="quality-owner")
    merged = service.publish_issue(
        "K-RISK-2", published_by="quality-owner", merge_into_risk_case_id=first["risk_case_id"]
    )
    assert merged["outcome"] == "MERGED" and merged["version_no"] == 2
    cases = service.list_cases(product="ROBOT", domain="SYSTEM_INTEGRATION", lifecycle="TEST")
    assert cases["total"] == 1
    case = cases["items"][0]["case"]
    assert case["related_issues"] == ["K-RISK-1", "K-RISK-2"]
    assert {gap["code"] for gap in case["capability_gaps"]} == {
        "EMBEDDED_HW_SW_CO_DESIGN", "VERSION_BASELINE_CONTROL",
    }
    assert service.list_cases(mrc="COMPATIBILITY_NOT_ANALYZED")["total"] == 1
    assert service.list_cases(capability="VERSION_BASELINE_CONTROL")["total"] == 1


def test_p1_api_runs_publish_assess_report_and_review(tmp_path):
    db, _ = prepared(tmp_path)
    client = TestClient(create_p0_app(db))
    published = client.post("/api/v2/risk-cases/publish", json={
        "knowledge_id": "K-RISK-1", "published_by": "quality-owner",
    })
    assert published.status_code == 201, published.text
    response = client.post("/api/v2/forward-assessments", json={
        "project_name": "新 PLC", "product_code": "PLC", "assessment_stage": "TEST",
        "material_type": "TEST_PLAN", "material_name": "测试计划",
        "material_text": "针对 PLC 与 IFA 接口协议版本不兼容风险，建立产品版本兼容性矩阵并执行全部组合升级回归。",
        "created_by": "tester",
    })
    assert response.status_code == 201, response.text
    report = response.json()
    detail = client.get(f"/api/v2/forward-assessments/{report['assessment_id']}")
    assert detail.status_code == 200 and detail.json()["version"]["report"]["matched_risk_count"] == 1
    risk_id = report["risks"][0]["risk_result_id"]
    review = client.post(f"/api/v2/forward-risk-results/{risk_id}/review", json={
        "status": "CONFIRMED", "note": "纳入项目风险清单", "reviewed_by": "reviewer",
    })
    assert review.status_code == 200


def test_reassessment_creates_version_and_reports_control_coverage_change(tmp_path):
    _, repository = prepared(tmp_path)
    service = ForwardRiskService(repository)
    service.publish_issue("K-RISK-1", published_by="quality-owner")
    first = service.create_assessment(
        project_name="版本升级项目", product_code="PLC", assessment_stage="DESIGN",
        material_type="DESIGN_SPEC", material_name="设计说明 V1",
        material_text=("本项目涉及 PLC 与 IFA 接口协议版本不兼容风险，设计范围包含升级、回退、异常恢复和多版本部署。"
                       "当前材料说明了接口边界与升级目标，但尚未给出具体兼容控制和验证方案。"),
        created_by="project-quality",
    )
    assert first["risks"][0]["coverage_status"] == "NOT_FOUND"
    second = service.reassess(
        first["assessment_id"], assessment_stage="DESIGN", material_type="DESIGN_SPEC",
        material_name="设计说明 V2",
        material_text=("本项目涉及 PLC 与 IFA 接口协议版本不兼容风险。"
                       "建立产品版本兼容性矩阵，并覆盖所有支持版本组合的升级回归。"),
        created_by="project-quality",
    )
    assert second["version_no"] == 2
    assert second["risks"][0]["coverage_status"] == "COVERED"
    change = second["version_comparison"]["changed_risks"][0]
    assert change["coverage_before"] == "NOT_FOUND"
    assert change["coverage_after"] == "COVERED"
    try:
        service.reassess(
            first["assessment_id"], assessment_stage="DESIGN", material_type="DESIGN_SPEC",
            material_name="重复材料", material_text=("本项目涉及 PLC 与 IFA 接口协议版本不兼容风险。"
            "建立产品版本兼容性矩阵，并覆盖所有支持版本组合的升级回归。"), created_by="project-quality",
        )
    except Exception as error:
        assert str(error) == "ASSESSMENT_MATERIAL_UNCHANGED"
    else:
        raise AssertionError("unchanged material must be rejected")


@pytest.mark.parametrize("stage,material_type", [
    ("REQUIREMENT", "REQUIREMENT"),
    ("DESIGN", "DESIGN_SPEC"),
    ("TEST", "TEST_PLAN"),
    ("RELEASE", "RELEASE_PLAN"),
])
def test_requirement_design_test_and_release_e2e(tmp_path, stage, material_type):
    db, repository = prepared(tmp_path)
    ForwardRiskService(repository).publish_issue("K-RISK-1", published_by="quality-owner")
    web = TestClient(create_p0_app(db))
    response = web.post("/api/v2/forward-assessments", json={
        "project_name": f"{stage} 阶段项目", "product_code": "PLC",
        "assessment_stage": stage, "material_type": material_type,
        "material_name": f"{stage} 阶段材料",
        "material_text": ("PLC 与 IFA 多版本组合存在接口协议不兼容风险。"
                          "项目建立产品版本兼容性矩阵，并覆盖所有支持版本组合的升级回归、异常恢复和回退验证。"),
        "created_by": "e2e-quality",
    })
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["assessment_stage"] == stage
    assert report["matched_risk_count"] == 1
    assert report["risks"][0]["coverage_status"] == "COVERED"
