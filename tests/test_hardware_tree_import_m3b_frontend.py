from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


def _client(tmp_path: Path) -> TestClient:
    root = Path(__file__).resolve().parents[1]
    p0_db = tmp_path / "quality_capability_p0.db"
    hardware_db = tmp_path / "hardware_case_mvp.db"
    upload_dir = tmp_path / "tree_uploads"

    initializer = P0Initializer(
        manifest_path=root / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=root / "quality_knowledge/config/plc_fields.yaml",
    )
    initializer.initialize(p0_db)

    app = create_p0_app(
        p0_db,
        stage_runner=object(),
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=upload_dir,
    )
    return TestClient(app)


def test_m3b_p07_is_mounted_on_unified_product_shell(tmp_path: Path):
    client = _client(tmp_path)

    response = client.get("/p0/hardware-cases/base-data")
    assert response.status_code == 200
    html = response.text

    assert "基础数据管理" in html
    assert "硬件案例库 / 基础数据管理" in html
    assert 'href="/p0/hardware-cases/base-data"' in html
    assert "Excel 上传" in html
    assert "Sheet / Header / Mapping" in html
    assert "Preview / Validation" in html
    assert "Change Diff / Conflict" in html
    assert "Confirm Apply" in html
    assert "Version / History" in html


def test_m3b_p07_freezes_change_type_and_result_semantics(tmp_path: Path):
    client = _client(tmp_path)

    html = client.get("/p0/hardware-cases/base-data").text
    script = client.get("/p0/static/hardware_tree_import_p07.js").text

    frozen = "ADD / UPDATE / RENAME / MOVE / DEPRECATE / NO_CHANGE / CONFLICT"
    assert frozen in html
    assert "EXCLUDE 仅是排除动作" in html
    assert "APPLIED_WITH_EXCLUSIONS" in html
    assert "APPLY_FAILED" in html
    assert "任一正式变更失败即整体失败" in html
    assert "绝不显示“部分成功”" in html

    assert 'const types = ["ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE", "NO_CHANGE", "CONFLICT"]' in script
    assert '"DELETE"' not in script
    assert "type-delete" not in script.lower()
    assert 'item.decision === "EXCLUDED"' in script
    assert "已生效，存在排除项" in script
    assert "Change Set 已全部生效" in script
    assert "上一 ACTIVE Version 保持不变" in script


def test_m3b_p07_keeps_backend_authority_for_analysis_and_apply(tmp_path: Path):
    client = _client(tmp_path)

    script = client.get("/p0/static/hardware_tree_import_p07.js")
    assert script.status_code == 200
    body = script.text

    assert '"/analyze"' in body
    assert '"/changes/"' in body
    assert '"/issues/"' in body
    assert '"/ready"' in body
    assert '"/apply"' in body
    assert '"/active-version/"' in body

    # The frontend presents backend results. It does not implement local tree
    # diff/version mutation algorithms.
    assert "diffEngine" not in body
    assert "version_id_for" not in body
    assert "BEGIN IMMEDIATE" not in body


def test_m3b_p07_static_assets_are_served_by_existing_p0_surface(tmp_path: Path):
    client = _client(tmp_path)

    css = client.get("/p0/static/hardware_tree_import_p07.css")
    js = client.get("/p0/static/hardware_tree_import_p07.js")

    assert css.status_code == 200
    assert js.status_code == 200
    assert "hc-tree-cards" in css.text
    assert "hc-change-summary" in css.text
    assert "hc-result-exclusion" in css.text
    assert "hc-result-failed" in css.text
    assert "data-start-import" in js.text
