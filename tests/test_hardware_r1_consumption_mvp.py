from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "quality_capability_p0.db"
    hardware_db = tmp_path / "hardware_case_mvp.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    return TestClient(
        create_p0_app(
            p0_db,
            stage_runner=object(),
            hardware_case_db_path=hardware_db,
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
        )
    )


def test_wave3b_page_reuses_hardware_shell_and_exposes_three_views(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/p0/hardware-cases/knowledge")

    assert response.status_code == 200
    html = response.text
    assert "正式硬件知识消费" in html
    assert "研发设计复用" in html
    assert "器件与电路风险" in html
    assert "市场与应用问题检索" in html
    assert 'data-api="/api/public/hardware-knowledge/v1"' in html
    assert "正式知识消费" in html
    assert "案例搜索" in html


def test_wave3b_assets_are_served_by_existing_p0_static_route(tmp_path: Path):
    client = _client(tmp_path)
    css = client.get("/p0/static/hardware_case_knowledge_consumption.css")
    script = client.get("/p0/static/hardware_case_knowledge_consumption.js")

    assert css.status_code == 200
    assert script.status_code == 200
    assert "hc-knowledge-scenarios" in css.text
    assert "/api/public/hardware-knowledge/v1" in script.text
    assert "/search" in script.text
    assert "/objects/" in script.text
    assert "正式知识消费索引当前不可用，请先重建 Consumption Projection。" in script.text


def test_wave3b_page_is_read_only_and_has_no_fallback_sources(tmp_path: Path):
    client = _client(tmp_path)
    script = client.get("/p0/static/hardware_case_knowledge_consumption.js").text

    assert client.post("/p0/hardware-cases/knowledge").status_code == 405
    assert "hardware-public-consumer" not in script
    assert "candidate" not in script.lower()
    assert "preview" not in script.lower()
    assert "unified knowledge" not in script.lower()
    assert "fetchJson('/search'" in script
    assert "fetchJson('/objects/'" in script


def test_wave3b_scenario_switch_is_presentation_only(tmp_path: Path):
    client = _client(tmp_path)
    script = client.get("/p0/static/hardware_case_knowledge_consumption.js").text

    assert "let activeScenario = 'research'" in script
    assert "renderResults();" in script
    assert "runSearch();" in script
    assert script.count("/search") == 1
    assert "match_reasons" in script
    assert "evidence_refs" in script
