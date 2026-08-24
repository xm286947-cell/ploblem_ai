from pathlib import Path

from fastapi.testclient import TestClient
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.web.p0_app import create_p0_app
from quality_knowledge.web.p0_pages import create_p0_insights_router

ROOT = Path(__file__).parents[1]
WEB = ROOT / "quality_knowledge" / "web"


def test_p0_router_is_isolated_factory_and_does_not_auto_include():
    text = (WEB / "p0_pages.py").read_text(encoding="utf-8")
    assert "create_p0_insights_router" in text
    assert "include_router" not in text
    assert '"/p0/insights"' in text


def test_p0_page_has_two_quality_axes_and_semantic_drilldown():
    text = (WEB / "templates" / "p0_insights.html").read_text(encoding="utf-8")
    js = (WEB / "static" / "p0_insights.js").read_text(encoding="utf-8")
    assert "质量工程关键矛盾" in text and "质量管理关键矛盾" in text
    assert "MRC × 质量能力" in text and "生命周期 × 质量能力" in text
    assert "data-drill-key" in js
    assert "analysis_scope_hash" in js
    assert "<pre" not in text.lower()


def test_p0_page_uses_dynamic_products_and_frozen_api_contract():
    html = (WEB / "templates" / "p0_insights.html").read_text(encoding="utf-8")
    js = (WEB / "static" / "p0_insights.js").read_text(encoding="utf-8")
    assert "data-products" in html
    assert "/products" in js and "/taxonomies" in js
    assert "/insights/business-contradictions" in js
    assert "business-contradictions/" in js and "/issues" in js
    assert "product_code" in js and "product_name" in js
    assert "taxonomy_type" in js and "label_zh" in js
    assert "HMI" not in html and "PLC" not in html and "IFA" not in html


def test_p0_assets_define_loading_empty_partial_and_error_states():
    html = (WEB / "templates" / "p0_insights.html").read_text(encoding="utf-8")
    js = (WEB / "static" / "p0_insights.js").read_text(encoding="utf-8")
    css = (WEB / "static" / "p0_insights.css").read_text(encoding="utf-8")
    for marker in ('data-state="empty"', 'data-state="error"', 'data-state="partial"', 'p0-loading'):
        assert marker in html or marker in css or marker in js
    assert "409" in js
    assert "1366" not in css  # responsive layout is fluid, not tied to one viewport


def test_p0_page_is_chinese_first_and_has_drawer_pagination():
    html = (WEB / "templates" / "p0_insights.html").read_text(encoding="utf-8")
    base = (WEB / "templates" / "p0_base.html").read_text(encoding="utf-8")
    assert 'lang="zh-CN"' in base
    assert 'class="side-nav"' in base and "INOVANCE" in base
    assert "data-drawer" in html and "data-page-prev" in html and "data-page-next" in html


def _initializer() -> P0Initializer:
    return P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )


def _seed_issue(repository: P0Repository) -> None:
    issue = repository.save_issue(
        knowledge_id="K-P0-UED-1",
        business_issue_id="B-P0-UED-1",
        raw_json={"问题编号": "B-P0-UED-1"},
        normalized_snapshot={"ISSUE_FACT": {"business_issue_id": "B-P0-UED-1", "month": "2026-08", "severity": "H"}},
        mapping_config_id="MAP-PLC-V1",
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="p0-ued-source",
        sheet_name="issues",
        row_number=1,
    )
    repository.save_analysis_set({
        "analysis_set_id": "AS-P0-UED-1",
        "knowledge_id": "K-P0-UED-1",
        "issue_version_id": issue["issue_version_id"],
        "taxonomy_version_id": "QUALITY_TAXONOMY_P0_V1",
        "input_hash": "p0-ued-input",
        "status": "COMPLETED",
        "tags": [{"stage": "occurrence", "axis": "DOMAIN", "tag_code": "SOFTWARE", "confidence": 0.6}],
        "mrc": [{"side": "OCCURRENCE", "mrc_code": "CHANGE_IMPACT_NOT_ASSESSED", "confidence": 0.6}],
        "capability_gaps": [{
            "capability_axis": "QUALITY_ENGINEERING", "capability_code": "TEST_VERIFICATION",
            "governance_scope": "PRODUCT", "control_status": "NOT_DEFINED", "details": {"priority": "P0"},
            "confidence": 0.6,
        }],
    })


def test_p0_insights_page_and_api_contract_with_initialized_seeded_app(tmp_path):
    db_path = tmp_path / "p0-ued.db"
    _initializer().initialize(db_path)
    _seed_issue(P0Repository(db_path))
    app = create_p0_app(db_path, stage_runner=None)
    app.include_router(create_p0_insights_router())
    client = TestClient(app)

    page = client.get("/p0/insights")
    assert page.status_code == 200
    assert "质量工程关键矛盾" in page.text
    assert client.get("/p0/static/p0_insights.css").status_code == 200
    assert client.get("/p0/static/p0_insights.js").status_code == 200

    products = client.get("/api/v2/products").json()
    assert products["items"][0]["product_code"] == "PLC"
    assert products["items"][0]["product_name"]
    taxonomies = client.get("/api/v2/taxonomies").json()
    assert all("taxonomy_type" in item and "code" in item and "label_zh" in item for item in taxonomies["items"])
    overview = client.get("/api/v2/insights/business-contradictions").json()
    assert isinstance(overview["scoring_version"], dict)
    assert "version_no" in overview["scoring_version"]
    assert overview["analysis_scope_hash"]
    assert {"total_issues", "completed", "partial_failed", "unanalysed", "classification_coverage"}.issubset(overview["coverage"])
    assert isinstance(overview["matrices"]["mrc_x_capability"], list)
