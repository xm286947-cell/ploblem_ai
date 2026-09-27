from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p04.fixtures import FixtureP04Provider
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return TestClient(
        create_p0_app(
            db,
            stage_runner=object(),
            p04_provider=FixtureP04Provider(),
            hardware_case_db_path=tmp_path / "hardware.sqlite3",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
        )
    )


def test_p04_url_context_and_asset_identity_are_explicit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get(
        "/p0/quality-scenario-insights?view=INDUSTRY&filter_lifecycle=DESIGN"
    )
    js = (ROOT / "quality_knowledge/web/static/p04_insights.js").read_bytes()
    digest = hashlib.sha256(js).hexdigest()

    assert response.status_code == 200
    assert f"p04_insights.js?v={digest[:20]}" in response.text
    assert f'p04_insights.js?v=p04-v1' not in response.text

    asset = client.get(f"/p0/static/p04_insights.js?v={digest[:20]}")
    assert asset.status_code == 200
    assert asset.content == js
    assert asset.headers["x-content-sha256"] == digest
    assert asset.headers["etag"] == f'"{digest}"'
    assert asset.headers["cache-control"] == "no-store"


def test_p04_context_restore_return_source_and_missing_paths_fail_closed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    url_context = quote('{"view":"INDUSTRY","filters":{"lifecycle":"DESIGN"}}')
    detail = client.get(
        "/p0/quality-scenarios/QS-FIX-002?return_to=/p0/quality-scenario-insights"
        f"&p04_context={url_context}"
    )
    assert detail.status_code == 200
    assert f"p04_context={url_context}" in detail.text

    source_page = client.get(
        "/p0/quality-scenario-sources/PROBLEM-003?return_to=/p0/quality-scenario-insights"
        f"&p04_context={url_context}"
    )
    assert source_page.status_code == 200
    assert "PROBLEM-003" in source_page.text
    assert "QS-FIX-002" in source_page.text
    assert f"p04_context={url_context}" in source_page.text

    assert client.get("/p0/quality-scenario-sources/MISSING").status_code == 404


def test_all_views_share_stable_scenario_identity_and_drilldown_context(tmp_path: Path) -> None:
    client = _client(tmp_path)
    scenario_ids = {}
    for view in ("PRODUCT", "CUSTOMER", "INDUSTRY"):
        response = client.post(
            "/api/v2/quality-scenario-insights/v1/query", json={"view": view}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["view"] == view
        scenario_ids[view] = {
            item["scenario_id"] for item in payload["scenario_list"]
        }
        assert payload["matrix"]["cells"]
        drilldown = payload["matrix"]["cells"][0]["drilldown_query"]
        result = client.post(
            "/api/v2/quality-scenario-insights/v1/drilldown", json=drilldown
        )
        assert result.status_code == 200
        assert result.json()["status"] == "OK"

    assert "QS-FIX-002" in scenario_ids["INDUSTRY"]
    assert len(set.intersection(*scenario_ids.values())) >= 1

    query = client.post(
        "/api/v2/quality-scenario-insights/v1/query", json={"view": "INDUSTRY"}
    ).json()
    drilldown = query["matrix"]["cells"][0]["drilldown_query"]
    drilldown["query_context_id"] = "ctx_wrong"
    rejected = client.post(
        "/api/v2/quality-scenario-insights/v1/drilldown", json=drilldown
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "INVALID_CONTEXT"
