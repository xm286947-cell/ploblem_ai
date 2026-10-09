from __future__ import annotations

import re
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


def test_evidence_drawer_layers_above_sticky_header():
    hardware_css = (ROOT / "quality_knowledge/web/static/hardware_case.css").read_text(encoding="utf-8")
    app_css = (ROOT / "quality_knowledge/web/static/app.css").read_text(encoding="utf-8")

    def z_indexes(css: str, selector: str) -> list[int]:
        rules = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        return [int(value.group(1)) for rule in rules if (value := re.search(r"z-index\s*:\s*(\d+)", rule))]

    header_z = z_indexes(app_css, ".top-bar")
    backdrop_z = z_indexes(hardware_css, ".hc-drawer-backdrop")
    drawer_z = z_indexes(hardware_css, ".hc-evidence-drawer")

    assert header_z and backdrop_z and drawer_z
    assert min(backdrop_z) > max(header_z)
    assert min(drawer_z) > min(backdrop_z)


def test_evidence_labels_are_chinese_and_keep_full_source_identity_accessible():
    js = (ROOT / "quality_knowledge/web/static/hardware_case.js").read_text(encoding="utf-8")
    assert "WORD:'Word 原文'" in js
    assert "AVAILABLE:'原文可用'" in js
    assert "PARAGRAPH:'段落'" in js
    assert "<details class=\"hc-source-identity\"><summary>技术来源标识</summary>" in js
    assert "title=\"'+esc(ev.source_ref||'')+'\"" in js


def test_m3_existing_platform_shell_has_hardware_case_primary_entry(tmp_path: Path):
    client = _client(tmp_path)
    html = client.get("/p0/issues").text
    assert 'href="/p0/hardware-cases">硬件案例库</a>' in html
    # Existing pages remain mounted.
    assert client.get("/p0/batch-analysis").status_code == 200
    assert client.get("/p0/cases").status_code == 200
