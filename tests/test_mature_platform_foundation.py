from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_mature_host_is_product_runtime_root(tmp_path):
    app = create_app(tmp_path / "quality_issue_v1.db")
    client = TestClient(app)

    assert app.state.mature_quality_host is True

    root = client.get("/", follow_redirects=False)
    assert root.status_code in {302, 307}
    assert root.headers["location"] == "/issues"

    for path in (
        "/issues",
        "/analysis",
        "/import",
        "/itr/recovery-workbench",
        "/itr/resolution-workbench",
        "/software-assessment",
        "/missed-test-analysis",
        "/product-reports",
        "/quality-scenarios",
        "/quality-scenario-assets",
        "/quality-scenarios/insights",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


def test_current_platform_launchers_cannot_drift_back_to_p0_root():
    launchers = {
        "mac": ROOT / "START_OVERALL_CURRENT_PLATFORM_MAC.command",
        "windows": ROOT / "START_OVERALL_CURRENT_PLATFORM_WINDOWS.bat",
    }

    for platform, path in launchers.items():
        text = path.read_text(encoding="utf-8")
        assert "DEFAULT_ENTRY=http://127.0.0.1:18080/issues" in text, platform
        assert "MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app" in text, platform
        assert "/p0/overall" not in text, platform
        assert "knowledge-p1-start" not in text, platform
        assert "knowledge-p0-web" not in text, platform
        assert "knowledge-p1-web" not in text, platform
        assert "P0_DB" not in text, platform
        assert "overall_current_platform_p0.db" not in text, platform
        assert "start_quality_capability_p1" in text, platform


def test_delegated_mature_launchers_use_existing_knowledge_web_host():
    mac = (ROOT / "start_quality_capability_p1.command").read_text(encoding="utf-8")
    windows = (ROOT / "start_quality_capability_p1.bat").read_text(encoding="utf-8")

    for platform, text in (("mac", mac), ("windows", windows)):
        assert "knowledge-web" in text, platform
        assert "knowledge-p1-start" not in text, platform
        assert "/p0/overall" not in text, platform
