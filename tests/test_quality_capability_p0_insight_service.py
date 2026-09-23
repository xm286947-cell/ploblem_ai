"""G2-C1 P0 insight tests, including a >300 issue exact-drill regression."""

from __future__ import annotations

from pathlib import Path

import pytest

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.insight_service import InsightService, P0InsightError, P0InsightService
from quality_knowledge.p0.repository import P0Repository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_repository(tmp_path) -> P0Repository:
    db_path = tmp_path / "p0-insight.db"
    P0Initializer(
        manifest_path=PROJECT_ROOT / "quality_knowledge" / "config" / "p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT / "quality_knowledge" / "config" / "plc_fields.yaml",
    ).initialize(db_path)
    return P0Repository(db_path)


def add_issue(repository, number: int, *, product_id=None, business_type="PLC", month="2026-08", domain="SOFTWARE", lifecycle="IMPLEMENTATION", axis="QUALITY_ENGINEERING", code="TEST_VERIFICATION", status="COMPLETED"):
    if business_type != "PLC":
        with repository.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO mapping_config(
                        config_id, business_type, version, status, source_type, content_hash, created_by
                    ) VALUES (?, ?, 1, 'ACTIVE', 'P0_TEST', ?, 'test')""",
                (f"MAP-{business_type}-V1", business_type, f"hash-{business_type}"),
            )
            connection.commit()
    mapping_id = "MAP-PLC-V1" if business_type == "PLC" else f"MAP-{business_type}-V1"
    knowledge_id = f"K-INSIGHT-{number}"
    issue = repository.save_issue(
        knowledge_id=knowledge_id,
        business_issue_id=f"BIZ-{number}",
        raw_json={"问题描述": f"问题 {number}", "SourceID": f"row-{number}"},
        normalized_snapshot={"ISSUE_FACT": {"business_issue_id": f"BIZ-{number}", "month": month, "severity": "H"}},
        mapping_config_id=mapping_id,
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="source-hash",
        sheet_name="issues",
        row_number=number + 1,
        product_id=product_id,
    )
    if status == "UNANALYSED":
        return knowledge_id, None
    analysis_id = f"AS-INSIGHT-{number}"
    repository.save_analysis_set({
        "analysis_set_id": analysis_id,
        "knowledge_id": knowledge_id,
        "issue_version_id": issue["issue_version_id"],
        "taxonomy_version_id": "QUALITY_TAXONOMY_P0_V1",
        "input_hash": f"input-{number}",
        "status": status,
        "classification_consistency": "PENDING_CONFIRMATION",
        "tags": [
            {"stage": "occurrence", "axis": "DOMAIN", "tag_code": domain, "confidence": 0.6},
            {"stage": "occurrence", "axis": "LIFECYCLE", "tag_code": lifecycle, "confidence": 0.6},
        ],
        "mrc": [{"side": "OCCURRENCE", "mrc_code": "CHANGE_IMPACT_NOT_ASSESSED", "confidence": 0.6}],
        "capability_gaps": [{
            "capability_axis": axis,
            "capability_code": code,
            "governance_scope": "PRODUCT",
            "control_status": "NOT_DEFINED",
            "details": {
                "priority": "P0",
                "related_occurrence_mrc_codes": ["CHANGE_IMPACT_NOT_ASSESSED"],
                "related_escape_mrc_codes": [],
                "gap_description": "需要建立受控能力",
            },
            "confidence": 0.6,
        }],
    })
    return knowledge_id, analysis_id


def test_300_plus_issues_have_exact_total_paging_and_no_duplicate_drill_rows(tmp_path):
    repository = make_repository(tmp_path)
    for number in range(1, 251):
        add_issue(repository, number, axis="QUALITY_ENGINEERING", code="TEST_VERIFICATION")
    for number in range(251, 306):
        add_issue(repository, number, axis="QUALITY_MANAGEMENT", code="CHANGE_IMPACT")
    service = P0InsightService(repository)
    overview = service.overview()
    engineering = overview["quality_engineering_top3"][0]
    management = overview["quality_management_top3"][0]
    assert overview["coverage"]["total_issues"] == 305
    assert engineering["issue_count"] == 250
    assert management["issue_count"] == 55
    page_three = service.drill_down(
        engineering["contradiction_key"], overview["analysis_scope_hash"], page=3, page_size=100
    )
    assert page_three["total"] == 250
    assert len(page_three["items"]) == 50
    assert len({item["knowledge_id"] for item in page_three["items"]}) == 50
    first_page = service.drill_down(
        engineering["contradiction_key"], overview["analysis_scope_hash"], page=1, page_size=100
    )
    assert {item["knowledge_id"] for item in first_page["items"]}.isdisjoint(
        {item["knowledge_id"] for item in page_three["items"]}
    )


def test_dynamic_filters_matrices_coverage_and_scope_conflict(tmp_path):
    repository = make_repository(tmp_path)
    with repository.connect() as connection:
        connection.execute(
            """INSERT INTO product_config(product_id, product_code, product_name, product_type)
                 VALUES ('PRODUCT-ROBOT', 'ROBOT', '机器人', 'MECHATRONIC')"""
        )
        connection.commit()
    _, analysis_id = add_issue(
        repository, 1, product_id="PRODUCT-ROBOT", business_type="ROBOT", month="2026-02",
        domain="EMBEDDED", lifecycle="SYSTEM_INTEGRATION", axis="QUALITY_ENGINEERING",
        code="EMBEDDED_HW_SW_CO_DESIGN",
    )
    add_issue(
        repository, 2, product_id="PRODUCT-ROBOT", business_type="ROBOT", month="2026-02",
        domain="EMBEDDED", lifecycle="SYSTEM_INTEGRATION", axis="QUALITY_MANAGEMENT",
        code="CHANGE_IMPACT", status="PARTIAL_FAILED",
    )
    add_issue(repository, 3, product_id="PRODUCT-ROBOT", business_type="ROBOT", month="2026-03", status="UNANALYSED")
    repository.save_human_revision(
        base_analysis_set_id=analysis_id,
        base_input_hash="input-1",
        confirmed_by="quality-owner",
        answers=[{"target_path": "occurrence.mrc.primary", "confirmed_value": "CHANGE_IMPACT_NOT_ASSESSED"}],
    )
    service = P0InsightService(repository)
    overview = service.overview({
        "product": "ROBOT", "business_type": "ROBOT", "month": "2026-02",
        "issue_domain": "EMBEDDED", "lifecycle_phase": "SYSTEM_INTEGRATION",
    })
    coverage = overview["coverage"]
    assert coverage["total_issues"] == 2
    assert coverage["completed"] == 1
    assert coverage["partial_failed"] == 1
    assert coverage["classification_coverage"] == 1.0
    assert coverage["source_type_counts"]["HUMAN_CONFIRMED"] == 1
    assert overview["quality_management_top3"] == []
    assert overview["matrices"]["mrc_x_capability"][0]["mrc_code"] == "CHANGE_IMPACT_NOT_ASSESSED"
    assert overview["matrices"]["lifecycle_x_capability"][0]["lifecycle_code"] == "SYSTEM_INTEGRATION"
    assert len(overview["scoring_methodology"]["components"]) == 7

    add_issue(repository, 4, product_id="PRODUCT-ROBOT", business_type="ROBOT", month="2026-02", domain="EMBEDDED", lifecycle="SYSTEM_INTEGRATION")
    contradiction = overview["quality_engineering_top3"][0]["contradiction_key"]
    with pytest.raises(P0InsightError, match="INSIGHT_SCOPE_CHANGED"):
        service.drill_down(
            contradiction,
            overview["analysis_scope_hash"],
            filters={"product": "ROBOT", "business_type": "ROBOT", "month": "2026-02", "issue_domain": "EMBEDDED", "lifecycle_phase": "SYSTEM_INTEGRATION"},
        )


def test_hu_02_confirmed_mrc_and_gap_revisions_change_aggregation_and_matrix(tmp_path):
    repository = make_repository(tmp_path)
    _, first_analysis = add_issue(repository, 1, code="TEST_VERIFICATION")
    add_issue(repository, 2, code="TEST_VERIFICATION")
    with repository.connect() as connection:
        connection.execute(
            """INSERT INTO issue_mrc(
                    issue_mrc_id, analysis_set_id, side, mrc_code, role,
                    control_status, source_type, confidence
                ) VALUES ('MRC-ESCAPE-1', ?, 'ESCAPE', 'RELEASE_GATE_FAILED', 'PRIMARY',
                          'NOT_DEFINED', 'AI_INFERRED', 0.6)""",
            (first_analysis,),
        )
        connection.commit()
    repository.save_human_revision(
        base_analysis_set_id=first_analysis,
        base_input_hash="input-1",
        confirmed_by="quality-owner",
        answers=[
            {"target_path": "occurrence.mrc.primary", "confirmed_value": "DESIGN_REVIEW_INEFFECTIVE"},
            {"target_path": "escape.mrc.primary", "confirmed_value": "RELEASE_GATE_FAILED"},
            {
                "target_path": "capability_gaps.QUALITY_ENGINEERING.TEST_VERIFICATION.PRODUCT",
                "confirmed_value": {
                    "capability_axis": "QUALITY_ENGINEERING",
                    "capability_code": "SOFTWARE_IMPLEMENTATION",
                    "governance_scope": "PRODUCT",
                    "control_status": "DEFINED_NOT_EXECUTED",
                    "details": {"priority": "P0", "gap_description": "人工确认后的实施能力缺口"},
                },
            },
        ],
    )
    assert repository.get_analysis_set(first_analysis)["capability_gaps"][0]["capability_code"] == "TEST_VERIFICATION"
    assert repository.get_analysis_set(first_analysis)["mrc"][1]["mrc_code"] == "CHANGE_IMPACT_NOT_ASSESSED"
    service = InsightService(repository)
    overview = service.business_contradictions({})
    engineering = {item["capability_code"]: item for item in overview["quality_engineering_top3"]}
    assert engineering["TEST_VERIFICATION"]["issue_count"] == 1
    assert engineering["SOFTWARE_IMPLEMENTATION"]["issue_count"] == 1
    revised = engineering["SOFTWARE_IMPLEMENTATION"]
    assert revised["capability_label_zh"] == "软件实现能力"
    assert revised["total_sample_count"] == 2
    assert revised["completed_analysis_count"] == 2
    assert revised["human_confirmation_rate"] == 1.0
    assert revised["control_status_distribution"] == {"DEFINED_NOT_EXECUTED": 1}
    cells = overview["matrices"]["mrc_x_capability"]
    software_mrc = {
        cell["mrc_code"] for cell in cells if cell["capability_code"] == "SOFTWARE_IMPLEMENTATION"
    }
    assert software_mrc == {"DESIGN_REVIEW_INEFFECTIVE", "RELEASE_GATE_FAILED"}
    drill = service.drilldown(
        revised["contradiction_key"], overview["analysis_scope_hash"], {}, 1, 50
    )
    assert drill["total"] == 1
    assert drill["items"][0]["gap"]["capability_code"] == "SOFTWARE_IMPLEMENTATION"


def test_stable_api_facing_insight_service_method_names(tmp_path):
    repository = make_repository(tmp_path)
    add_issue(repository, 1)
    service = InsightService(repository)
    overview = service.business_contradictions({})
    key = overview["quality_engineering_top3"][0]["contradiction_key"]
    result = service.drilldown(key, overview["analysis_scope_hash"], {}, 1, 200)
    assert result["total"] == 1
    with pytest.raises(P0InsightError, match="INSIGHT_PAGINATION_INVALID"):
        service.drilldown(key, overview["analysis_scope_hash"], {}, 1, 201)
