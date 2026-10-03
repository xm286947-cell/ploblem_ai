from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import services.hardware_operability as operability
from quality_knowledge.web.p0_app import create_p0_app
from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_data_reliability import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_VERSION_NAME,
    HardwareDataReliabilityError,
    HardwareDataReliabilityManager,
)


class _HealthyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _runtime_config(tmp_path: Path) -> Path:
    path = tmp_path / "model.local.yaml"
    path.write_text(
        """active_model: hardware_ci
models:
  hardware_ci:
    provider: openai_compatible
    base_url: http://127.0.0.1:9/v1
    api_key_env: HARDWARE_CASE_API_KEY
    model: mock-model
    temperature: 0
    max_tokens: 8192
""",
        encoding="utf-8",
    )
    return path


def _ready_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HARDWARE_CASE_MODEL_CONFIG", str(_runtime_config(tmp_path)))
    monkeypatch.setenv("HARDWARE_CASE_API_KEY", "W3_2_SECRET")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_BASE_URL", "http://127.0.0.1:19091")
    monkeypatch.setenv(
        "HARDWARE_KNOWLEDGE_RELEASE_VERSION",
        "KNOWLEDGE_TEST_R1",
    )
    monkeypatch.setenv(
        "HARDWARE_KNOWLEDGE_READINESS_URL",
        "http://127.0.0.1:19091/health",
    )
    monkeypatch.setattr(
        operability,
        "urlopen",
        lambda request, timeout=0: _HealthyResponse(),
    )


def _app(tmp_path: Path, db: Path):
    return create_p0_app(
        tmp_path / "quality.db",
        hardware_case_db_path=db,
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )


def test_schema_status_is_visible_and_ready_on_fresh_bootstrap(tmp_path):
    db = tmp_path / "hardware.db"
    client = TestClient(_app(tmp_path, db))

    schema = client.get("/api/system/hardware/schema")
    assert schema.status_code == 200
    payload = schema.json()
    assert payload["status"] == "READY"
    assert payload["schema_version"] == CURRENT_SCHEMA_VERSION
    assert payload["schema_name"] == SCHEMA_VERSION_NAME


def test_unknown_schema_blocks_hardware_business_routes(tmp_path):
    db = tmp_path / "hardware.db"
    HardwareDataReliabilityManager(db).ensure_ready()
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE hardware_schema_version SET schema_version=999 WHERE singleton=1"
        )

    client = TestClient(_app(tmp_path, db))
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    schema = client.get("/api/system/hardware/schema")
    assert schema.status_code == 503
    assert schema.json()["error_code"] == "UNKNOWN_SCHEMA_VERSION"
    assert client.get("/api/public/hardware/v1/cases").status_code == 404


def test_readiness_stays_failed_until_migration_recovery_completes(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "hardware.db"
    legacy = HardwareCaseRepository(db)
    legacy.save_case(
        {
            "case_id": "HC-RECOVERY-1",
            "title": "Recovery",
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": [],
            "product_context": {},
            "facts": {},
        }
    )

    def _fail(point: str):
        if point == "before_commit":
            raise RuntimeError("injected")

    failing = HardwareDataReliabilityManager(db, fault_injector=_fail)
    try:
        failing.ensure_ready()
    except HardwareDataReliabilityError as error:
        backup_id = error.backup_id
    else:
        raise AssertionError("migration failure expected")
    assert backup_id

    _ready_environment(tmp_path, monkeypatch)
    blocked = TestClient(_app(tmp_path, db))
    assert blocked.get("/ready").status_code == 503
    assert (
        blocked.get("/api/system/hardware/schema").json()["error_code"]
        == "MIGRATION_FAILED_RECOVERY_REQUIRED"
    )

    HardwareDataReliabilityManager(db).recover_from_backup(backup_id)
    recovered = TestClient(_app(tmp_path, db))
    assert recovered.get("/api/system/hardware/schema").status_code == 200
    ready = recovered.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["dependencies"]["HARDWARE_DB"]["status"] == "READY"
