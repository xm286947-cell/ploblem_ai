from __future__ import annotations

from fastapi.testclient import TestClient

from products.storage_rc1.storage_life import core
from products.storage_rc1.storage_life.app import app as storage_app
from products.storage_rc1.storage_life.engineering_insight import _ensure_schema
from quality_knowledge.web.p0_app import create_p0_app


def test_overall_shell_binds_existing_storage_host_and_keeps_product_routes(
    tmp_path, monkeypatch
):
    storage_data = tmp_path / "storage-data"
    monkeypatch.setattr(core, "DATA", storage_data)
    monkeypatch.setattr(core, "DB", storage_data / "storage_life.sqlite3")
    _ensure_schema()

    overall = create_p0_app(
        tmp_path / "overall.sqlite3",
        enabled_domains={"HARDWARE_CASE"},
        storage_app=storage_app,
    )
    binding = overall.state.storage_workspace_binding
    client = TestClient(overall)

    assert binding["storage_app"] is storage_app
    assert binding["storage_db"] == str(core.DB)
    assert sum(
        1 for route in overall.routes if getattr(route, "path", None) == "/storage-workspace"
    ) == 1

    shell_page = client.get("/p0/hardware-cases")
    assert shell_page.status_code == 200
    assert 'href="/storage-workspace/"' in shell_page.text

    workspace = client.get("/storage-workspace/")
    assert workspace.status_code == 200
    assert 'id="overallShellBack"' in workspace.text
    assert 'href="/p0/issues"' in workspace.text
    assert 'window.__STORAGE_WORKSPACE_PREFIX__ = "/storage-workspace"' in workspace.text

    health = client.get("/storage-workspace/api/health")
    observation = client.get(
        "/storage-workspace/storage/runtime-observations/does-not-exist"
    )
    formulas = client.get("/storage-workspace/storage/lifetime/formulas")
    impact = client.get("/storage-workspace/storage/software-impact/does-not-exist")
    assert health.status_code == 200
    assert health.json()["service"] == "storage-life"
    assert observation.status_code == 404
    assert formulas.status_code == 200
    assert "NVME_DATA_UNITS_WRITTEN_V1" in {
        item["formula_id"] for item in formulas.json()["items"]
    }
    assert impact.status_code == 404


def test_storage_standalone_host_remains_compatible():
    page = TestClient(storage_app).get("/")
    assert page.status_code == 200
    assert 'window.__STORAGE_WORKSPACE_PREFIX__ = ""' in page.text
    assert 'href="/p0/issues"' in page.text
    assert TestClient(storage_app).get("/api/health").status_code == 200
