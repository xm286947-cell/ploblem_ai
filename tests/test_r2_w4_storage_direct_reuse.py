from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import products.storage_rc1.storage_life.app as storage_app_module
from products.storage_rc1.storage_life import core, product_api, runtime_bridge


def _bind_storage_db(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "storage_life.sqlite3")
    with core.connect():
        pass
    return TestClient(storage_app_module.app)


def _seed_device(
    *,
    device_id: str,
    source_id: str,
    model: str,
    project_ref: str | None = None,
) -> None:
    with core.connect() as con:
        con.execute(
            "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
            (
                source_id,
                f"{model}.pdf",
                (source_id * 64)[:64],
                f"/synthetic/{model}.pdf",
                "",
                "Synthetic",
                1,
                core.now(),
            ),
        )
        con.execute(
            "INSERT INTO devices VALUES (?,?,?,?,?)",
            (device_id, "Synthetic", model, "eMMC", source_id),
        )
        if project_ref:
            con.execute(
                "INSERT INTO links VALUES (?,?,?,?,?,?)",
                (
                    f"L-{device_id}",
                    device_id,
                    "project",
                    project_ref,
                    "PROJECT_DEVICE",
                    core.now(),
                ),
            )


def test_project_device_context_reuses_existing_links_and_device_master(
    tmp_path: Path,
    monkeypatch,
):
    client = _bind_storage_db(tmp_path, monkeypatch)
    _seed_device(device_id="DEV-1", source_id="a", model="A", project_ref="PROJ-1")
    _seed_device(device_id="DEV-2", source_id="b", model="B", project_ref="PROJ-1")
    _seed_device(device_id="DEV-3", source_id="c", model="C")

    response = client.get("/api/product/projects/PROJ-1/context")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["projection_version"] == "storage-project-device-context/v1"
    assert body["project_ref"] == "PROJ-1"
    assert body["device_ids"] == ["DEV-1", "DEV-2"]
    assert body["ownership"] == {
        "project_master": False,
        "device_master": "EXISTING_DEVICES",
        "binding": "EXISTING_LINKS",
    }
    assert body["status_projection"]["owner_role"] == "STORAGE_MAINTAINER"


def test_project_link_uses_existing_link_endpoint_and_matrix_engine(
    tmp_path: Path,
    monkeypatch,
):
    client = _bind_storage_db(tmp_path, monkeypatch)
    _seed_device(device_id="DEV-1", source_id="d", model="D")
    _seed_device(device_id="DEV-2", source_id="e", model="E")
    _seed_device(device_id="DEV-3", source_id="f", model="F")

    for device_id in ("DEV-1", "DEV-2"):
        linked = client.post(
            "/api/links",
            json={
                "device_id": device_id,
                "target_system": "project",
                "target_id": "PROJ-2",
                "relation": "PROJECT_DEVICE",
            },
        )
        assert linked.status_code == 201, linked.text

    matrix = client.post(
        "/api/product/projects/PROJ-2/matrix",
        json={"device_ids": ["DEV-1", "DEV-2"]},
    )
    assert matrix.status_code == 200, matrix.text
    payload = matrix.json()
    assert payload["status"] == "READY"
    assert payload["matrix_engine"] == "DIRECT_REUSE:product_api.compare_devices"
    assert [x["id"] for x in payload["comparison"]["devices"]] == ["DEV-1", "DEV-2"]

    escaped = client.post(
        "/api/product/projects/PROJ-2/matrix",
        json={"device_ids": ["DEV-1", "DEV-3"]},
    )
    assert escaped.status_code == 422
    assert "PROJECT_DEVICE_SCOPE_VIOLATION" in escaped.text


def test_provider_operability_is_projection_only(monkeypatch):
    monkeypatch.setattr(
        runtime_bridge,
        "status",
        lambda: {
            "configured": True,
            "provider": "openai-compatible",
            "profile": "storage-test",
            "model": "synthetic-model",
            "api_key_present": True,
            "runtime_expected_commit": "runtime-sha",
        },
    )
    monkeypatch.setattr(
        runtime_bridge,
        "last_executions",
        lambda: [
            {
                "request_id": "storage-1",
                "status": "COMPLETED",
                "provider": "openai-compatible",
                "model": "synthetic-model",
                "observed_at": "2026-09-29T07:00:00+00:00",
            }
        ],
    )

    result = product_api.provider_operability()
    assert result["provider_configured"] is True
    assert result["connectivity"] == "PASS"
    assert result["last_check"] == "2026-09-29T07:00:00+00:00"
    assert result["probe_performed"] is False
    assert result["management_deep_links"]["agent_config"]["href"] is None
    assert result["management_deep_links"]["runtime_diagnostics"]["href"] is None
    assert result["status_projection"]["owner_role"] == "OVERALL_RUNTIME_AGENT_CONFIG_OWNER"


def test_provider_operability_projects_last_runtime_error(monkeypatch):
    monkeypatch.setattr(
        runtime_bridge,
        "status",
        lambda: {
            "configured": True,
            "provider": "openai-compatible",
            "profile": "storage-test",
            "model": "synthetic-model",
            "api_key_present": True,
            "runtime_expected_commit": "runtime-sha",
        },
    )
    monkeypatch.setattr(
        runtime_bridge,
        "last_executions",
        lambda: [
            {
                "request_id": "storage-2",
                "status": "FAILED",
                "observed_at": "2026-09-29T07:01:00+00:00",
                "error": {
                    "code": "PROVIDER_TIMEOUT",
                    "category": "PROVIDER",
                    "message": "timeout",
                },
            }
        ],
    )

    result = product_api.provider_operability()
    assert result["connectivity"] == "FAIL"
    assert result["last_error"]["code"] == "PROVIDER_TIMEOUT"
    assert result["status_projection"]["failure_reason"] == "PROVIDER_TIMEOUT"
    assert result["probe_performed"] is False


def test_storage_ui_freezes_formal_knowledge_as_canonical_flow():
    html = (
        Path(storage_app_module.__file__).with_name("index.html")
        .read_text(encoding="utf-8")
    )
    formal = html.index("正式 Knowledge Production · Canonical Flow")
    legacy = html.index("Supporting / Legacy 本地资料核验")
    assert formal < legacy
    assert "<details class=\"card\">" in html
    assert "非正式发布入口" in html
    assert "Project / Device Context" in html
    assert "AI Provider Operability" in html
    assert "Storage 不创建私有配置/诊断中心" in html
