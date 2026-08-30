from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.web.p0_app import create_p0_app
from quality_knowledge.web.p0_pages import create_p0_insights_router


ROOT = Path(__file__).parents[1]
WEB = ROOT / "quality_knowledge" / "web"


def _init(db_path):
    return P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db_path)


def _seed(repository: P0Repository):
    issue = repository.save_issue(
        knowledge_id="K-WB-1",
        business_issue_id="B-WB-1",
        raw_json={"问题编号": "B-WB-1", "原始流出分类": ""},
        normalized_snapshot={"ISSUE_FACT": {"business_issue_id": "B-WB-1", "title": "测试问题", "product": "PLC", "month": "2026-08", "severity": "H"}},
        mapping_config_id="MAP-PLC-V1", mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1", source_file_sha256="workbench-source",
        sheet_name="issues", row_number=1,
    )
    repository.save_analysis_set({
        "analysis_set_id": "AS-WB-1", "knowledge_id": "K-WB-1",
        "issue_version_id": issue["issue_version_id"], "taxonomy_version_id": "QUALITY_TAXONOMY_P0_V1",
        "input_hash": "workbench-input", "status": "COMPLETED",
        "tags": [{"stage": "occurrence", "axis": "DOMAIN", "tag_code": "SOFTWARE", "confidence": 0.6}],
        "mrc": [{"side": "OCCURRENCE", "mrc_code": "CHANGE_IMPACT_NOT_ASSESSED", "confidence": 0.6}],
        "capability_gaps": [{"capability_axis": "QUALITY_ENGINEERING", "capability_code": "TEST_VERIFICATION", "governance_scope": "PRODUCT", "control_status": "NOT_DEFINED", "details": {"priority": "P0"}, "confidence": 0.6}],
        "open_questions": [{"stage": "occurrence", "question_key": "q1", "target_path": "occurrence.mrc", "question_text": "发生 MRC 是否已确认?"}],
        "evidence": [{"stage": "occurrence", "target_path": "occurrence.mrc", "source_type": "AI_INFERRED", "source_ref": "issue_description", "excerpt": "来自问题描述", "confidence": 0.6}],
    })


def _client(tmp_path):
    db = tmp_path / "p0-workbench.db"
    _init(db)
    _seed(P0Repository(db))
    app = create_p0_app(db, stage_runner=None)
    app.include_router(create_p0_insights_router())
    return TestClient(app)


def test_workbench_and_detail_routes_and_static_assets_are_real_pages(tmp_path):
    client = _client(tmp_path)
    page = client.get("/p0/issues?business_type=PLC&month=2026-08&page=1")
    detail = client.get("/p0/issues/K-WB-1?business_type=PLC&month=2026-08")
    assert page.status_code == 200 and "问题工作台" in page.text and "月份" in page.text
    assert detail.status_code == 200 and "技术追溯" in detail.text
    assert "原始数据未提供" in (WEB / "static/p0_issue_detail.js").read_text(encoding="utf-8")
    assert "<pre" not in detail.text.lower()
    for asset in ("p0_issues.css", "p0_issues.js", "p0_issue_detail.css", "p0_issue_detail.js"):
        assert client.get("/p0/static/" + asset).status_code == 200


def test_issue_api_contract_has_pagination_detail_and_navigation(tmp_path):
    client = _client(tmp_path)
    listing = client.get("/api/v2/issues", params={"business_type": "PLC", "month": "2026-08", "page": 1, "page_size": 20})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1 and listing.json()["items"][0]["knowledge_id"] == "K-WB-1"
    detail = client.get("/api/v2/issues/K-WB-1")
    assert detail.status_code == 200 and detail.json()["analysis"]["analysis_set_id"] == "AS-WB-1"
    navigation = client.get("/api/v2/issues/K-WB-1/navigation", params={"business_type": "PLC", "month": "2026-08"})
    assert navigation.status_code == 200 and navigation.json()["total"] == 1


def test_human_confirmation_applies_typed_value_and_preserves_review_status(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/v2/issues/K-WB-1/human-confirmations", json={
        "base_analysis_set_id": "AS-WB-1",
        "base_input_hash": "workbench-input",
        "confirmed_by": "quality-owner",
        "answers": [{
            "question_key": "q1",
            "target_path": "occurrence.mrc.primary",
            "confirmation_status": "CORRECTED",
            "confirmed_value": "DESIGN_REVIEW_INEFFECTIVE",
            "changes_insight": True,
        }],
    })
    assert response.status_code == 201, response.text
    detail = client.get("/api/v2/issues/K-WB-1").json()
    assert detail["effective_analysis"]["values"]["occurrence.mrc.primary"] == "DESIGN_REVIEW_INEFFECTIVE"
    revision = detail["human_revisions"][0]["answers"][0]
    assert revision["question_key"] == "q1"
    assert revision["confirmation_status"] == "CORRECTED"
    assert revision["changes_insight"] == 1


def test_pending_human_answer_does_not_override_effective_analysis(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/v2/issues/K-WB-1/human-confirmations", json={
        "base_analysis_set_id": "AS-WB-1",
        "base_input_hash": "workbench-input",
        "confirmed_by": "quality-owner",
        "answers": [{
            "question_key": "q1",
            "target_path": "occurrence.mrc.primary",
            "confirmation_status": "PENDING",
            "confirmed_value": None,
            "changes_insight": True,
        }],
    })
    assert response.status_code == 201, response.text
    detail = client.get("/api/v2/issues/K-WB-1").json()
    assert detail["effective_analysis"]["values"]["occurrence.mrc.primary"] == "CHANGE_IMPACT_NOT_ASSESSED"
    answer = detail["human_revisions"][0]["answers"][0]
    assert answer["confirmation_status"] == "PENDING"
    assert answer["changes_insight"] == 0


def test_workbench_static_contract_preserves_filters_and_human_confirmation_payload():
    list_js = (WEB / "static/p0_issues.js").read_text(encoding="utf-8")
    detail_js = (WEB / "static/p0_issue_detail.js").read_text(encoding="utf-8")
    detail_html = (WEB / "templates/p0_issue_detail.html").read_text(encoding="utf-8")
    assert "product_code" in list_js and "product_name" in list_js
    assert "page_size" in list_js and "queryString" in list_js
    assert "/navigation" in detail_js and "previous_id" in detail_js and "next_id" in detail_js
    assert "human-confirmations" in detail_js and "target_path" in detail_js and "base_analysis_set_id" in detail_js
    for marker in ("confirmed_value", "confirmation_status", "gap_description", "excerpt", "question_text", "human_revision_id"):
        assert marker in detail_js
    assert "SOURCE_DATA" in detail_js and "AI_STANDARDIZED" in detail_js and "AI_INFERRED" in detail_js and "HUMAN_CONFIRMED" in detail_js
    assert "原始数据未提供" in detail_js and "raw_json" in detail_js
    assert "标准化入库数据" in detail_html and "data-normalized" in detail_html
    assert "data-analysis-diagnostic" in detail_html and "AI 分析过程与结果" in detail_js
    assert "人工分析与确认" in detail_html and "renderHumanAnalysis" in detail_js
    assert "p0_issue_detail_restore.js" not in detail_html and "p0_manual_analysis_restore.js" not in detail_html
    assert "p0-detail-parity01" in detail_html
    assert "loading" in detail_html.lower() and "data-state=\"stale\"" in detail_html
