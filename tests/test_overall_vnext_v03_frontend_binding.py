from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path, enabled_domains=None) -> TestClient:
    db = tmp_path / "overall-vnext-binding.db"
    if enabled_domains is None:
        P0Initializer(
            manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
            plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
        ).initialize(db)
    app = create_p0_app(
        db,
        stage_runner=object(),
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains=enabled_domains,
    )
    return TestClient(app)


def test_overall_root_uses_issue_workbench_as_default_route(tmp_path):
    client = _client(tmp_path)
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/p0/issues"
    assert client.get(response.headers["location"]).status_code == 200


def test_hardware_only_root_binding_remains_hardware_home(tmp_path):
    client = _client(tmp_path, enabled_domains={"HARDWARE_CASE"})

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/p0/hardware-cases"


def test_existing_capability_routes_remain_available(tmp_path):
    client = _client(tmp_path)

    for path in (
        "/p0/batch-analysis",
        "/p0/hardware-cases/base-data",
        "/p1/risk-assessment",
        "/p0/quality-scenario-insights",
    ):
        assert client.get(path).status_code == 200, path


def test_issue_workbench_restores_and_writes_query_filter_page_and_selected_id():
    source = (ROOT / "quality_knowledge/web/static/p0_issues.js").read_text(encoding="utf-8")

    for marker in (
        "function readUrlState()",
        "function urlState(selectedId = state.selectedId)",
        "params.get(name)",
        "params.get('page')",
        "params.get('selected_id')",
        "history[mode === 'replace' ? 'replaceState' : 'pushState']",
        "window.addEventListener('popstate'",
        "queryString(id)",
        'data-selected="true"',
    ):
        assert marker in source
