from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).parents[1]


def client(tmp_path):
    db = tmp_path / "console.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return TestClient(create_p0_app(db))


def test_console_pages_and_assets_are_mounted_in_real_p0_app(tmp_path):
    app = client(tmp_path)
    settings = app.get("/p0/settings")
    intake = app.get("/p0/data-intake")
    assert settings.status_code == 200 and "产品与字段映射" in settings.text
    assert intake.status_code == 200 and "上传并预检" in intake.text
    assert app.get("/p0/static/p0_console.css").status_code == 200
    assert app.get("/p0/static/p0_console.js").status_code == 200


def test_console_uses_dynamic_products_catalog_mapping_and_preview_contract():
    template = (ROOT / "quality_knowledge/web/templates/p0_console.html").read_text(encoding="utf-8")
    script = (ROOT / "quality_knowledge/web/static/p0_console.js").read_text(encoding="utf-8")
    assert "HMI" not in template and "IFA" not in template
    for marker in (
        "/products", "/mapping-drafts", "/mappings/", "/validate", "/activate",
        "/intake/preview", "/intake/confirm", "required_missing", "required_missing_details", "field_decisions",
    ):
        assert marker in script
    assert "59 个" in template and "统一字段" in template
    assert "搜索中文名、Key、Domain、源表头" in template
    assert "Preview" in template and "确认正式导入" in template
    assert "if(create){selected=await request" in script
    assert "已基于当前正式版创建可编辑 Draft" in script


def test_main_pages_expose_navigation_to_intake_and_settings():
    insight = (ROOT / "quality_knowledge/web/templates/p0_insights.html").read_text(encoding="utf-8")
    issues = (ROOT / "quality_knowledge/web/templates/p0_issues.html").read_text(encoding="utf-8")
    base = (ROOT / "quality_knowledge/web/templates/p0_base.html").read_text(encoding="utf-8")
    for page in (insight, issues):
        assert 'extends "p0_base.html"' in page
    assert "/p0/data-intake" in base
    assert "/p0/settings" in base
