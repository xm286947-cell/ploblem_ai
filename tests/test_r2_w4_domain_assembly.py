from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.overall_assembly import build_domain_assembly
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _storage_stub() -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def home():
        return '<a id="overallShellBack" href="/p0/overall">Overall</a>'

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "storage-life-w4"}

    return app


def _client(tmp_path: Path) -> TestClient:
    db = tmp_path / "w4-overall.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    app = create_p0_app(
        db,
        stage_runner=object(),
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        storage_app=_storage_stub(),
    )
    return TestClient(app)


def test_w4_assembly_registry_is_metadata_only_and_fail_closed():
    state = {
        "historical_case_service": object(),
        "hardware_case_service": object(),
        "p04_service": None,
        "storage_workspace_binding": {"prefix": "/storage-workspace"},
    }
    payload = build_domain_assembly(state)

    assert payload["contract_version"] == "overall-domain-assembly/v1"
    assert payload["total"] == 4
    assert payload["ready"] == 3
    assert payload["unbound"] == 1
    assert payload["all_ready"] is False
    assert payload["direct_domain_repository_access"] is False
    assert payload["composition_policy"] == "PUBLIC_CONTRACT_OR_EXISTING_APP_MOUNT_ONLY"

    by_id = {item["domain_id"]: item for item in payload["items"]}
    assert by_id["quality-scenario"]["state"] == "UNBOUND"
    assert by_id["quality-scenario"]["fail_closed"] is True
    assert by_id["storage"]["binding_kind"] == "ASGI_MOUNT_EXISTING_APP"
    assert by_id["storage"]["mount_prefix"] == "/storage-workspace"
    assert all(item["data_ownership"] == "DOMAIN_OWNED" for item in payload["items"])
    assert all(item["overall_direct_repository_access"] is False for item in payload["items"])


def test_w4_full_composed_app_reports_four_ready_domains(tmp_path: Path):
    client = _client(tmp_path)

    response = client.get("/api/v2/overall/assembly")
    assert response.status_code == 200
    payload = response.json()

    assert payload["all_ready"] is True
    assert payload["ready"] == 4
    by_id = {item["domain_id"]: item for item in payload["items"]}

    assert by_id["major"]["entry_path"] == "/p0/cases"
    assert "historical-case/v1" in by_id["major"]["public_contracts"]
    assert "major-problem-context/v1" in by_id["major"]["public_contracts"]

    assert by_id["hardware"]["entry_path"] == "/p0/hardware-cases"
    assert by_id["hardware"]["public_contracts"] == ["hardware-case/v1"]

    assert by_id["quality-scenario"]["entry_path"] == "/p0/quality-scenario-insights"
    assert by_id["quality-scenario"]["public_contracts"] == ["quality-scenario-insight/v1"]
    assert by_id["quality-scenario"]["consumed_contracts"] == ["major-problem-context/v1"]

    assert by_id["storage"]["entry_path"] == "/storage-workspace/"
    assert by_id["storage"]["mount_prefix"] == "/storage-workspace"
    assert "knowledge-query/v1" in by_id["storage"]["public_contracts"]
    assert "knowledge-evidence/v1" in by_id["storage"]["public_contracts"]

    assert all(item["evidence_contract"] == "common-evidence/v1.0" for item in payload["items"])
    assert all(item["return_contract"] == "overall-return-context/v1" for item in payload["items"])


def test_w4_overall_home_surfaces_assembly_without_copying_domain_pages(tmp_path: Path):
    client = _client(tmp_path)
    page = client.get("/p0/overall")

    assert page.status_code == 200
    assert 'data-domain-assembly' in page.text
    assert "4/4 READY" in page.text
    assert "overall-domain-assembly/v1" in page.text
    assert "PUBLIC_CONTRACT_OR_EXISTING_APP_MOUNT_ONLY" in page.text
    assert page.text.count("DIRECT_REPOSITORY=NO") == 4

    for domain_id in ("major", "hardware", "quality-scenario", "storage"):
        assert f'data-domain-id="{domain_id}"' in page.text


def test_w4_assembly_module_has_no_domain_repository_or_database_import():
    source = (ROOT / "quality_knowledge/web/overall_assembly.py").read_text(encoding="utf-8")

    assert "Repository" not in source
    assert "sqlite" not in source.lower()
    assert "products.storage_rc1" not in source
    assert "services." not in source
    assert "repositories." not in source
