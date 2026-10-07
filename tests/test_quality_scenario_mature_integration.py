from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app


def test_mature_quality_scenario_pages_have_no_p0_product_navigation(tmp_path):
    client = TestClient(create_app(tmp_path / "mature.db"))

    issues = client.get("/issues")
    assert issues.status_code == 200
    assert 'href="/quality-scenarios/workbench"' in issues.text
    assert 'href="/quality-scenarios/library"' in issues.text
    assert 'href="/p0/quality-scenarios/workbench"' not in issues.text

    for path in (
        "/quality-scenarios/workbench",
        "/quality-scenarios/library",
        "/quality-scenarios/library/QSV1-NOT-FOUND",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "/p0/static/" not in response.text, path
        assert 'href="/p0/quality-scenarios/workbench"' not in response.text, path


def test_software_assessment_is_still_qsv1_production_entry(tmp_path):
    client = TestClient(create_app(tmp_path / "mature.db"))
    page = client.get("/software-assessment")
    assert page.status_code == 200
    assert "data-sa-qsv1" in page.text
    assert "quality-scenario-production" in page.text

# Package candidate sync marker v2
