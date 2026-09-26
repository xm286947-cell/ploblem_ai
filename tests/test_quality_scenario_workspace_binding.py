from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p04.fixtures import FixtureP04Provider
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "quality_capability_p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    return TestClient(
        create_p0_app(
            p0_db,
            stage_runner=object(),
            p04_provider=FixtureP04Provider(),
            hardware_case_db_path=tmp_path / "hardware_case.sqlite3",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
        )
    )


def test_quality_scenario_workspace_reuses_public_library_and_return_chain(tmp_path: Path) -> None:
    client = _client(tmp_path)

    shell = client.get("/p0/issues")
    assert shell.status_code == 200
    assert 'href="/p0/quality-scenario-insights">质量画像与洞察</a>' in shell.text

    workspace = client.get("/p0/quality-scenario-insights")
    assert workspace.status_code == 200
    assert 'data-quality-scenario-workspace' in workspace.text
    assert 'href="/p0/issues">返回总体工作台</a>' in workspace.text
    assert 'href="/p0/quality-scenario-insights">Scenario Library</a>' in workspace.text
    assert 'data-scenario-library' in workspace.text

    query = client.post(
        "/api/v2/quality-scenario-insights/v1/query",
        json={"view": "PRODUCT"},
    )
    assert query.status_code == 200
    item = query.json()["scenario_list"][0]
    assert item["p03_path"].startswith("/p0/issues/")
    detail_path = item["p03_path"] + "?return_to=/p0/quality-scenario-insights"

    detail = client.get(detail_path)
    assert detail.status_code == 200
    assert 'href="/p0/quality-scenario-insights"' in detail.text
    assert "返回质量场景工作区" in detail.text
    assert "证据与来源" in detail.text
    assert "技术追溯：原始数据与标准化入库数据" in detail.text


def test_quality_scenario_binding_stays_on_public_contract_boundary(tmp_path: Path) -> None:
    client = _client(tmp_path)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v2/quality-scenario-insights/v1/query" in paths
    assert "/api/v2/quality-scenario-insights/v1/drilldown" in paths
    exposed = "\n".join(paths).lower()
    assert "repository" not in exposed
    assert "sqlite" not in exposed

    source = (ROOT / "quality_knowledge/web/p0_pages.py").read_text()
    assert "quality_scenario_insights" in source
    assert "return_to" in source
    assert "quality_scenario_v1_store" not in source
    assert "sqlite" not in source.lower()
