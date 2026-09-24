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


def test_m3_p01_is_clear_hardware_case_product_entry(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/p0/hardware-cases")
    assert response.status_code == 200
    html = response.text
    assert "HARDWARE CASE · P01" in html
    assert "硬件案例库" in html
    assert "案例首页" in html
    assert "双树导航" in html
    assert "案例搜索" in html
    assert "电路 / 特性树" in html
    assert "物料 / 器件树" in html
    assert "批量 AI 分析 · P01" not in html
    assert "案例确认" not in html
    assert "基础数据管理" not in html


def test_m3_maintainer_product_nav_exposes_review_and_p07(tmp_path: Path):
    client = _client(tmp_path)
    html = client.get("/p0/hardware-cases?role=maintainer").text
    assert "维护视图" in html
    assert "案例确认" in html
    assert "基础数据管理" in html
    review = client.get("/p0/hardware-cases/review")
    assert review.status_code == 200
    assert "HARDWARE CASE · P05" in review.text


def test_m3_p02_p03_p04_and_p07_routes_are_mounted(tmp_path: Path):
    client = _client(tmp_path)
    tree = client.get("/p0/hardware-cases/tree")
    search = client.get("/p0/hardware-cases/search")
    detail = client.get("/p0/hardware-cases/HC-SYNTHETIC")
    p07 = client.get("/p0/hardware-cases/base-data")
    assert tree.status_code == 200
    assert "HARDWARE CASE · P02" in tree.text
    assert search.status_code == 200
    assert "HARDWARE CASE · P03" in search.text
    assert detail.status_code == 200
    assert "HARDWARE CASE · P04" in detail.text
    assert "EVIDENCE · P06" in detail.text
    assert p07.status_code == 200
    assert "HARDWARE CASE · P07" in p07.text
    assert "案例首页" in p07.text
    assert "案例确认" in p07.text


def test_m3_shared_assets_are_served(tmp_path: Path):
    client = _client(tmp_path)
    css = client.get("/p0/static/hardware_case.css")
    js = client.get("/p0/static/hardware_case.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert ".hc-product-bar" in css.text
    assert "initHome" in js.text
    assert "initTree" in js.text
    assert "initSearch" in js.text
    assert "initDetail" in js.text
    assert "initReview" in js.text
    assert "source-preview" in js.text
    assert "CORE_FACTS_NOT_REVIEWED" in js.text
    assert "NO_VALID_EVIDENCE" in js.text
    assert "NO_CONFIRMED_MAPPING" in js.text


def test_m3_existing_platform_shell_has_hardware_case_primary_entry(tmp_path: Path):
    client = _client(tmp_path)
    html = client.get("/p0/issues").text
    assert 'href="/p0/hardware-cases">硬件案例库</a>' in html
    # Existing pages remain mounted.
    assert client.get("/p0/batch-analysis").status_code == 200
    assert client.get("/p0/cases").status_code == 200
