from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _storage_stub() -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def home():
        return '<a href="/p0/overall">Overall</a>'

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "storage-life-hardware-role-test"}

    return app


def _client(tmp_path: Path, *, role: str | None = None) -> TestClient:
    db = tmp_path / f"overall-{role or 'default'}.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    app = create_p0_app(
        db,
        stage_runner=object(),
        hardware_case_db_path=tmp_path / f"hardware-{role or 'default'}.db",
        hardware_tree_upload_dir=tmp_path / f"tree-{role or 'default'}",
        hardware_case_source_root=tmp_path / f"sources-{role or 'default'}",
        storage_app=_storage_stub(),
        hardware_case_host_role=role,
    )
    return TestClient(app)


def test_hardware_consumer_host_does_not_allow_query_or_header_escalation(
    tmp_path: Path,
):
    client = _client(tmp_path, role="CONSUMER")

    home = client.get("/p0/hardware-cases?role=maintainer")
    assert home.status_code == 200
    assert 'data-role="CONSUMER"' in home.text
    assert 'href="/p0/hardware-cases/base-data"' not in home.text
    assert 'href="/p0/hardware-cases/intake"' not in home.text
    assert 'href="/p0/hardware-cases/review"' not in home.text

    for path in (
        "/p0/hardware-cases/base-data",
        "/p0/hardware-cases/intake",
        "/p0/hardware-cases/review",
    ):
        response = client.get(path)
        assert response.status_code == 403
        assert response.json()["detail"] == "HARDWARE_CASE_MAINTAINER_REQUIRED"

    escalated = client.get(
        "/api/v2/hardware-cases",
        headers={"X-Hardware-Case-Role": "MAINTAINER"},
    )
    assert escalated.status_code == 403
    assert escalated.json()["detail"] == "HARDWARE_CASE_MAINTAINER_REQUIRED"


def test_hardware_maintainer_host_discovers_existing_maintenance_entries(
    tmp_path: Path,
):
    client = _client(tmp_path, role="MAINTAINER")

    home = client.get("/p0/hardware-cases")
    assert home.status_code == 200
    assert 'data-role="MAINTAINER"' in home.text
    assert 'href="/p0/hardware-cases/base-data"' in home.text
    assert 'href="/p0/hardware-cases/intake"' in home.text
    assert 'href="/p0/hardware-cases/review"' in home.text

    for path in (
        "/p0/hardware-cases/base-data",
        "/p0/hardware-cases/intake",
        "/p0/hardware-cases/review",
    ):
        response = client.get(path)
        assert response.status_code == 200

    maintainer_read = client.get(
        "/api/v2/hardware-cases",
        headers={"X-Hardware-Case-Role": "MAINTAINER"},
    )
    assert maintainer_read.status_code == 200


def test_overall_cases_area_marks_hardware_base_data_by_host_role(
    tmp_path: Path,
):
    consumer = _client(tmp_path / "consumer", role="CONSUMER")
    consumer_page = consumer.get("/p0/overall/areas/cases-knowledge")
    assert consumer_page.status_code == 200
    assert "仅维护角色可进入；当前 Host 为 Consumer" in consumer_page.text

    maintainer = _client(tmp_path / "maintainer", role="MAINTAINER")
    maintainer_page = maintainer.get("/p0/overall/areas/cases-knowledge")
    assert maintainer_page.status_code == 200
    assert 'href="/p0/hardware-cases/base-data"' in maintainer_page.text


def test_hardware_host_role_defaults_to_consumer(tmp_path: Path):
    client = _client(tmp_path)
    home = client.get("/p0/hardware-cases")
    assert home.status_code == 200
    assert 'data-role="CONSUMER"' in home.text
