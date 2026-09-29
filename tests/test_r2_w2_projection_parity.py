from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.overall_shell import create_overall_shell_router


def _client(*, legacy_scenario_ready: bool):
    app = FastAPI()
    app.state.overall_shell_enabled = True
    app.state.legacy_quality_issue_status = {"ready": True, "code": "READY"}
    app.state.legacy_scenario_status = {
        "ready": legacy_scenario_ready,
        "code": "READY" if legacy_scenario_ready else "LEGACY_SCENARIO_TABLE_NOT_FOUND",
        "mode": "READ_ONLY",
    }
    app.include_router(create_overall_shell_router())
    return TestClient(app)


def test_w2_scenario_projection_parity_never_auto_retires_legacy_capability():
    client = _client(legacy_scenario_ready=True)

    payload = client.get("/api/v2/overall/scenario-parity").json()
    assert payload["contract"] == "overall-scenario-projection-parity/v1"
    assert payload["total"] == 4
    assert payload["all_parity_pass"] is False
    assert payload["retirement_allowed"] is False
    assert payload["policy"] == "KEEP_LEGACY_UNTIL_PROJECTION_PARITY"

    for item in payload["items"]:
        assert item["legacy_available"] is True
        assert item["parity_status"] == "PENDING_EVIDENCE"
        assert item["coexistence_required"] is True
        assert item["retirement_allowed"] is False
        assert item["legacy_path"].startswith("/")
        assert item["replacement_path"].startswith("/")


def test_w2_scenario_area_exposes_old_and_new_entries_with_retirement_blocked():
    client = _client(legacy_scenario_ready=True)
    page = client.get("/p0/overall/areas/scenarios-insights")

    assert page.status_code == 200
    assert 'data-scenario-parity' in page.text
    assert page.text.count("RETIREMENT_ALLOWED=NO") == 4
    assert "KEEP_LEGACY_UNTIL_PROJECTION_PARITY" in page.text
    for marker in (
        "/quality-scenarios",
        "/quality-scenario-assets",
        "/quality-scenario-assets/portrait",
        "/p0/quality-scenario-insights",
        "view=PRODUCT",
        "view=CUSTOMER",
        "view=INDUSTRY",
    ):
        assert marker in page.text


def test_w2_unbound_legacy_source_does_not_imply_parity_or_retirement():
    client = _client(legacy_scenario_ready=False)

    payload = client.get("/api/v2/overall/scenario-parity").json()
    assert payload["retirement_allowed"] is False
    assert all(item["legacy_available"] is False for item in payload["items"])
    assert all(item["retirement_allowed"] is False for item in payload["items"])

    page = client.get("/p0/overall/areas/scenarios-insights")
    assert page.status_code == 200
    assert "Legacy scenario source is not bound" in page.text
    assert page.text.count("RETIREMENT_ALLOWED=NO") == 4


def test_w2_shell_loads_one_overall_navigation_contract():
    client = _client(legacy_scenario_ready=True)
    page = client.get("/p0/overall/areas/scenarios-insights")
    assert page.status_code == 200
    assert page.text.count("/p0/static/overall_navigation.js") == 1
    assert "/p0/static/overall_navigation.js?v=" in page.text
    assert "r2-w2-v1" not in page.text
