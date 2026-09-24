from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "quality_capability_p0.db"
    hardware_db = tmp_path / "hardware_case_mvp.db"
    P0Initializer(
        manifest_path=PROJECT_ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    app = create_p0_app(
        p0_db,
        stage_runner=object(),
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=tmp_path / "tree_uploads",
    )
    return TestClient(app)


def test_m3b_p07_is_mounted_on_existing_p0_shell(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/p0/hardware-cases/base-data")
    assert response.status_code == 200
    html = response.text
    assert "P07" in html
    assert "基础数据管理" in html
    assert "电路 / 特性树" in html
    assert "物料 / 器件树" in html
    assert "结构映射" in html
    assert "Preview / Validation" in html
    assert "Change Diff" in html
    assert "确认生效" in html
    assert "APPLIED_WITH_EXCLUSIONS" in html
    assert "APPLY_FAILED" in html
    assert "EXCLUDE" in html
    assert "DEPRECATE" in html
    assert "DELETE" not in html
    assert "/p0/hardware-cases/base-data" in html


def test_m3b_static_assets_are_served_by_existing_p0_router(tmp_path: Path):
    client = _client(tmp_path)
    css = client.get("/p0/static/hardware_tree_import.css")
    js = client.get("/p0/static/hardware_tree_import.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "hti-page" in css.text
    assert "workbook-preview" in js.text
    assert "APPLIED_WITH_EXCLUSIONS" in js.text
    assert "APPLY_FAILED" in js.text
    assert "change_type==='DELETE'" not in js.text
    assert 'change_type==="DELETE"' not in js.text
    assert "EXCLUDE 只排除" not in js.text  # product copy stays in HTML, not hidden JS logic


def test_m3b_frozen_change_types_are_visible_without_delete_semantics(tmp_path: Path):
    client = _client(tmp_path)
    html = client.get("/p0/hardware-cases/base-data").text
    for change_type in [
        "ADD",
        "UPDATE",
        "RENAME",
        "MOVE",
        "DEPRECATE",
        "NO_CHANGE",
        "CONFLICT",
    ]:
        assert f"<option>{change_type}</option>" in html
    assert "<option>DELETE</option>" not in html
    assert "EXCLUDE 只表示本次 Change Set 排除" in html


def test_m3b_apply_result_copy_preserves_atomic_semantics(tmp_path: Path):
    client = _client(tmp_path)
    html = client.get("/p0/hardware-cases/base-data").text
    assert "Atomic Apply 任一正式变更失败时，整体失败" in html
    assert "当前 ACTIVE Version 保持不变" in html
    assert "上传完成 ≠ 生效" in html
