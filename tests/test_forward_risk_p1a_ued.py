from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).parents[1]


def client(tmp_path) -> TestClient:
    db = tmp_path / "p1-ued.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return TestClient(create_p0_app(db))


def test_p1_page_and_assets_are_reachable(tmp_path):
    web = client(tmp_path)
    page = web.get("/p1/risk-assessment")
    assert page.status_code == 200
    assert "正向质量风险评估" in page.text
    assert "风险案例库" in page.text and "风险评估报告" in page.text
    assert web.get("/p0/static/p1_risk_assessment.css").status_code == 200
    assert web.get("/p0/static/p1_risk_assessment.js").status_code == 200


def test_p1_ui_uses_dynamic_products_and_fixed_coverage_contract(tmp_path):
    web = client(tmp_path)
    script = web.get("/p0/static/p1_risk_assessment.js").text
    assert "人工复核" in script
    assert 'request("/products")' in script
    assert "product_code" in script and "product_name" in script
    for status in ("COVERED", "PARTIAL", "NOT_FOUND", "INSUFFICIENT_INFO", "NOT_APPLICABLE"):
        assert status in script
    assert "JSON.stringify(x.match_basis.applicability_boundary" not in script
    assert "boundary(risk.match_basis.applicability_boundary)" in script
    assert "/versions" in script and "version_comparison" in script
    assert "merge_into_risk_case_id" in web.get("/p1/risk-assessment").text


def test_primary_p0_pages_link_to_forward_risk_assessment(tmp_path):
    web = client(tmp_path)
    assert "/p1/risk-assessment" in web.get("/p0/insights").text
    assert "/p1/risk-assessment" in web.get("/p0/issues").text
    assert "/p1/risk-assessment" in web.get("/p0/settings").text
