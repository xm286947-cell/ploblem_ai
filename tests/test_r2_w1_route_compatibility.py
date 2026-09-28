from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web import create_app
from quality_knowledge.web.p0_pages import create_p0_insights_router


def test_w1_existing_legacy_routes_keep_their_frozen_semantics(tmp_path: Path):
    client = TestClient(create_app(tmp_path / "route-compat.db"))

    issues = client.get("/issues")
    assert issues.status_code == 200
    assert "问题工作台" in issues.text
    assert "彻底解决工作台" in issues.text
    assert "漏测分析" in issues.text

    analysis = client.get("/analysis")
    assert analysis.status_code == 200
    assert "软件问题考核" not in analysis.text

    itr_materials = client.get("/materials/itr")
    assert itr_materials.status_code == 200
    assert "ITR 材料导入" in itr_materials.text
    assert "本页不承担现场业务处理" in itr_materials.text

    software_materials = client.get("/materials/software-operations")
    assert software_materials.status_code == 200
    assert "软件运营数据导入" in software_materials.text
    assert "软件问题考核工作台" not in software_materials.text


def test_w1_p0_routes_are_real_entries_and_preserve_adapter_boundaries():
    app = FastAPI()
    app.include_router(create_p0_insights_router())
    client = TestClient(app)

    issues = client.get("/p0/issues")
    assert issues.status_code == 200
    assert "问题工作台" in issues.text
    assert 'href="/itr/resolution-workbench"' in issues.text
    assert 'href="/p0/missed-test-analysis"' in issues.text

    batch = client.get("/p0/batch-analysis")
    assert batch.status_code == 200
    assert "批量" in batch.text

    missed = client.get(
        "/p0/missed-test-analysis",
        params={"q": "ITR-1"},
        follow_redirects=False,
    )
    assert missed.status_code == 503
    assert missed.json()["detail"] == "LEGACY_DB_UNAVAILABLE"
