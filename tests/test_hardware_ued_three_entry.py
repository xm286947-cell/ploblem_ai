"""Mock-safe UED navigation contract. No Provider or formal data writes."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app

ROOT = Path(__file__).resolve().parents[1]


def _isolated_client(tmp_path: Path) -> TestClient:
    db = tmp_path / "p0-ued.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return TestClient(create_p0_app(
        db,
        stage_runner=object(),
        hardware_case_db_path=tmp_path / "hardware-ued.db",
        hardware_tree_upload_dir=tmp_path / "trees",
        hardware_case_source_root=tmp_path / "sources",
    ))


def test_three_primary_entries_on_real_product_templates(tmp_path: Path):
    client = _isolated_client(tmp_path)
    routes = [
        "/p0/hardware-cases",
        "/p0/hardware-cases/search",
        "/p0/hardware-cases/knowledge",
        "/p0/hardware-cases/tree",
        "/p0/hardware-cases/intake",
        "/p0/hardware-cases/knowledge-production",
        "/p0/hardware-cases/review",
        "/p0/hardware-cases/word-import",
        "/p0/hardware-cases/base-data",
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, (route, response.text[:300])
        html = response.text
        assert html.count('data-ued-primary=') == 3, route
        assert 'data-ued-primary="find"' in html, route
        assert 'data-ued-primary="assets"' in html, route
        assert 'data-ued-primary="maintain"' in html, route
        assert 'href="/p0/hardware-cases/search' in html, route
        assert 'href="/p0/hardware-cases/tree' in html, route
        assert 'href="/p0/hardware-cases/intake"' in html, route
        assert 'hc-platform-drawer' in html, route
        assert 'hardware_case_ued.css' in html, route
        assert 'hardware_case_ued.js' in html, route


def test_existing_capabilities_remain_linked_in_context(tmp_path: Path):
    client = _isolated_client(tmp_path)
    find = client.get("/p0/hardware-cases/search?q=MCU").text
    assert 'data-hc-ued-forward-query' in find
    assert 'data-search-form' in find
    assert 'data-search-results' in find
    formal = client.get("/p0/hardware-cases/knowledge").text
    assert 'data-knowledge-form' in formal
    assert 'data-knowledge-detail' in formal
    asset = client.get("/p0/hardware-cases/tree").text
    assert 'data-tree-type="CIRCUIT_FEATURE"' in asset
    assert 'data-tree-type="MATERIAL_DEVICE"' in asset
    intake = client.get("/p0/hardware-cases/intake").text
    assert 'data-intake-upload' in intake
    assert 'data-intake-detail' in intake
    assert 'data-intake-review' in intake
    review = client.get("/p0/hardware-cases/review").text
    assert 'data-review-queue' in review
    assert 'data-review-evidence' in review
    assert 'data-publish disabled' in review


def test_legacy_urls_still_exist_and_other_platform_nav_unchanged(tmp_path: Path):
    client = _isolated_client(tmp_path)
    for path in (
        "/p0/hardware-cases/e2e",
        "/p0/hardware-cases/word-import",
        "/p0/hardware-cases/base-data",
        "/p0/hardware-cases/knowledge-production",
        "/p0/hardware-cases/review",
    ):
        assert client.get(path).status_code == 200, path
    platform = client.get("/p0/issues").text
    assert 'href="/p0/hardware-cases">硬件案例库</a>' in platform
    assert 'hc-platform-drawer' not in platform
    assert 'href="/p0/batch-analysis"' in platform


def test_ued_assets_and_query_handoff(tmp_path: Path):
    client = _isolated_client(tmp_path)
    css = client.get("/p0/static/hardware_case_ued.css")
    js = client.get("/p0/static/hardware_case_ued.js")
    formal_js = client.get("/p0/static/hardware_case_knowledge_consumption.js")
    assert css.status_code == js.status_code == formal_js.status_code == 200
    assert "@media(max-width:700px)" in css.text
    assert "encodeURIComponent" in js.text
    assert "initialText = new URLSearchParams(location.search).get('q')" in formal_js.text
    # No second Provider API, review action, or publish side effect in the UED helper.
    for forbidden in ("fetch(", "POST", "publish", "localStorage", "HARDWARE_ANALYSIS_INTERNAL_TOKEN"):
        assert forbidden not in js.text
