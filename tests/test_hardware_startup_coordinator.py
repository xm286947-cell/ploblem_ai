from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
import sys

import pytest

from services.hardware_asset_migrations import v001_candidate_repository
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_asset_migrations import v001_candidate_repository
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_case_r1_workbench import HardwareR1WorkbenchStore
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_data_reliability import HardwareDataReliabilityManager
from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_operability import readiness_status
from services.hardware_startup_coordinator import (
    HardwareStartupCoordinator,
    _StartupLock,
)


def _paths(tmp_path: Path, *, legacy_roots: tuple[Path, ...] = ()):
    app_root = tmp_path / "application"
    app_root.mkdir(parents=True, exist_ok=True)
    data_root = tmp_path / "persistent-data"
    bootstrap = tmp_path / "user-config" / "bootstrap.json"
    resolver = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap,
        legacy_roots=legacy_roots,
    )
    return app_root, data_root, bootstrap, resolver


def _install(tmp_path: Path):
    app_root, data_root, bootstrap, resolver = _paths(tmp_path)
    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()
    if not result["ready"] and sys.platform == "win32":
        diagnostic_root = tmp_path / "startup-diagnostic"
        diagnostic_app, _diagnostic_data, _diagnostic_bootstrap, diagnostic_resolver = _paths(diagnostic_root)
        HardwareStartupCoordinator(diagnostic_app, resolver=diagnostic_resolver)._first_install(
            diagnostic_resolver.resolve()
        )
    assert result["ready"] is True, (
        f"startup phase={result['phase']} error={result['error_code']} "
        f"mode={result['installation_mode']}"
    )
    return app_root, data_root, bootstrap, resolver, result


def test_first_install_and_existing_startup_are_idempotent(tmp_path: Path):
    app_root, data_root, bootstrap, resolver = _paths(tmp_path)
    first = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert first["ready"] is True, (
        f"startup phase={first['phase']} error={first['error_code']} "
        f"mode={first['installation_mode']}"
    )
    assert first["installation_mode"] == "FIRST_INSTALL"
    assert bootstrap.is_file()
    manifest_path = data_root / "manifest" / "hardware_data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["installation_id"] == json.loads(bootstrap.read_text())["installation_id"]
    assert manifest["relative_paths"]["asset_db"] == "db/hardware_asset.db"
    assert (data_root / "db/hardware_case_mvp.db").is_file()
    assert (data_root / "db/hardware_asset.db").is_file()
    assert (data_root / "db/workbench_runtime.db").is_file()
    assert (data_root / "sources").is_dir()
    assert not (data_root / "manifest/recovery/current_operation.json").exists()

    db_sizes_before = {
        path.name: path.stat().st_size
        for path in (data_root / "db").glob("*.db")
    }
    second = HardwareStartupCoordinator(app_root, resolver=resolver).run()
    db_sizes_after = {
        path.name: path.stat().st_size
        for path in (data_root / "db").glob("*.db")
    }
    assert second["ready"] is True
    assert second["installation_mode"] == "EXISTING_INSTALL"
    assert db_sizes_after == db_sizes_before


def test_missing_asset_database_fails_closed_without_empty_recreation(tmp_path: Path):
    app_root, data_root, _bootstrap, resolver, _ = _install(tmp_path)
    asset_db = data_root / "db" / "hardware_asset.db"
    asset_db.unlink()

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "HARDWARE_ASSET_DB_MISSING"
    assert not asset_db.exists()


def test_schema_too_new_blocks_startup(tmp_path: Path):
    app_root, data_root, _bootstrap, resolver, _ = _install(tmp_path)
    asset_db = data_root / "db" / "hardware_asset.db"
    with sqlite3.connect(asset_db) as connection:
        connection.execute(
            "UPDATE hardware_asset_schema_version SET schema_version=99 WHERE singleton=1"
        )
    manifest_path = data_root / "manifest" / "hardware_data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["asset_schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "ASSET_SCHEMA_TOO_NEW"


def test_asset_schema_migration_is_backed_up_and_manifested(tmp_path: Path):
    app_root, data_root, _bootstrap, resolver, _ = _install(tmp_path)
    asset_db = data_root / "db" / "hardware_asset.db"
    asset_db.unlink()
    with sqlite3.connect(asset_db) as connection:
        v001_candidate_repository.apply(connection)
        CandidateAssetRepository._set_schema_version(
            connection, 1, v001_candidate_repository.SCHEMA_NAME
        )
        CandidateAssetRepository._write_migration_record(
            connection, v001_candidate_repository, 0
        )
        connection.commit()
    manifest_path = data_root / "manifest" / "hardware_data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["asset_schema_version"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is True, result
    assert CandidateAssetRepository(asset_db).schema_version() == 2
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated_manifest["asset_schema_version"] == 2
    assert updated_manifest["last_migration_id"] == "HARDWARE-ASSET-SCHEMA-MIGRATION"
    assert list((data_root / "backups" / "pre-asset-schema-migration").glob("*.sqlite3"))


def test_hardware_schema_too_new_blocks_startup(tmp_path: Path):
    app_root, data_root, _bootstrap, resolver, _ = _install(tmp_path)
    hardware_db = data_root / "db" / "hardware_case_mvp.db"
    with sqlite3.connect(hardware_db) as connection:
        connection.execute(
            "UPDATE hardware_schema_version SET schema_version=99 WHERE singleton=1"
        )
    manifest_path = data_root / "manifest" / "hardware_data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hardware_schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "HARDWARE_SCHEMA_TOO_NEW"


def test_active_source_hash_mismatch_blocks_without_repairing_registry(tmp_path: Path):
    app_root, data_root, _bootstrap, resolver, _ = _install(tmp_path)
    hardware_db = data_root / "db" / "hardware_case_mvp.db"
    source_root = data_root / "sources"
    source = HardwareCaseSourceStore(hardware_db, source_root, initialize_schema=False)
    metadata = source.register_active_bytes("A0162", "A0162.docx", b"trusted source")
    source_path = source.resolve_path(metadata["source_ref"])
    source_path.write_bytes(b"modified source")

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "SOURCE_INTEGRITY_FAILED"
    assert source_path.read_bytes() == b"modified source"


def test_active_candidate_must_match_current_source_binding(tmp_path: Path):
    app_root, data_root, _bootstrap, resolver, _ = _install(tmp_path)
    hardware_db = data_root / "db" / "hardware_case_mvp.db"
    source_store = HardwareCaseSourceStore(
        hardware_db, data_root / "sources", initialize_schema=False
    )
    source = source_store.register_active_bytes("A0162", "A0162.docx", b"candidate source")
    asset_db = data_root / "db" / "hardware_asset.db"
    repository = CandidateAssetRepository(asset_db)
    case_id = "A0162"
    source_id = source["source_id"]
    knowledge_object = {
        "contract_version": "hardware-case-knowledge-object/v1",
        "identity": {"business_case_id": case_id, "raw_title": "Candidate"},
        "source_fact": {"business_case_id": case_id, "source_id": source_id},
        "engineering_context": {"primary_subject": {"value": "grounded fact"}},
        "evidence": [
            {
                "block_id": "B0001",
                "source_locator": {"paragraph": 1},
                "text": "grounded fact",
            }
        ],
        "conflicts": [],
        "review": {"object_status": "CANDIDATE", "field_decisions": []},
    }
    candidate = repository.create_or_commit_candidate(
        business_case_id=case_id,
        source_id=source_id,
        source_ref=source["source_ref"],
        knowledge_object=knowledge_object,
        generation_run_id="run-1",
        pipeline_version="pipeline/v1",
        agent_config_version="agent/v1",
        knowledge_schema_version="hardware-case-knowledge-object/v1",
        validator_version="validator/v1",
    )
    with sqlite3.connect(asset_db) as connection:
        connection.execute(
            "UPDATE hardware_candidate_asset SET source_ref='r1:A0162:stale' WHERE candidate_id=?",
            (candidate["candidate_id"],),
        )

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "HARDWARE_ASSET_CROSS_REFERENCE_INVALID"


def test_root_recovery_marker_blocks_normal_startup(tmp_path: Path):
    app_root, data_root, _bootstrap, resolver, _ = _install(tmp_path)
    marker = data_root / "manifest" / "recovery" / "current_operation.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"operation": "MIGRATION", "state": "IN_PROGRESS"}))

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "HARDWARE_STARTUP_RECOVERY_REQUIRED"
    assert marker.is_file()


def test_readiness_and_business_routes_are_gated_before_ready(tmp_path: Path):
    from fastapi.testclient import TestClient

    from quality_knowledge.web import create_p0_app

    app_root = tmp_path / "application"
    app_root.mkdir()
    missing_db = tmp_path / "not-created" / "hardware.db"
    blocked = {
        "status": "BLOCKED",
        "phase": "VERIFY_LAYOUT",
        "ready": False,
        "error_code": "HARDWARE_DATA_ROOT_MISSING",
    }
    app = create_p0_app(
        missing_db,
        project_root=app_root,
        hardware_case_db_path=missing_db,
        portrait_db_path=":memory:",
        hardware_startup_status=blocked,
        enabled_domains={"HARDWARE_CASE"},
    )

    assert not any(getattr(route, "path", "") == "/p0/hardware-cases" for route in app.routes)
    assert not missing_db.parent.exists()
    status, payload = readiness_status(
        root=app_root,
        hardware_db_path=missing_db,
        startup_status=blocked,
    )
    assert status == 503
    assert payload["dependencies"]["HARDWARE_STARTUP"]["error_code"] == blocked["error_code"]
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.get("/api/system/hardware/startup").status_code == 503
        assert client.get("/p0/hardware-cases").status_code == 404
    assert not missing_db.parent.exists()


def test_legacy_upgrade_uses_staging_and_preserves_legacy_root(tmp_path: Path):
    app_root = tmp_path / "application"
    app_root.mkdir()
    legacy = tmp_path / "legacy-data"
    legacy.mkdir()
    hardware_db = legacy / "hardware_case_mvp.db"
    workbench_db = legacy / "hardware_case_mvp_r1_workbench.db"
    source_root = legacy / "hardware_case_mvp_sources"
    source_root.mkdir()
    HardwareDataReliabilityManager(hardware_db).ensure_ready()
    HardwareR1WorkbenchStore(workbench_db)
    source_store = HardwareCaseSourceStore(hardware_db, source_root, initialize_schema=False)
    source = source_store.register_active_bytes("A0152", "A0152.docx", b"legacy evidence")
    original_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (hardware_db, workbench_db, source_store.resolve_path(source["source_ref"]))
    }
    data_root = tmp_path / "persistent-data"
    bootstrap = tmp_path / "user-config" / "bootstrap.json"
    resolver = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap,
        legacy_roots=(legacy,),
    )
    assert resolver.resolve().classification == "LEGACY_UPGRADE_REQUIRED"

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is True, result
    assert result["installation_mode"] == "LEGACY_UPGRADE_REQUIRED"
    assert (data_root / "db/hardware_case_mvp.db").is_file()
    assert (data_root / "db/hardware_asset.db").is_file()
    assert (data_root / "db/workbench_runtime.db").is_file()
    assert (data_root / "sources").is_dir()
    assert json.loads((data_root / "manifest/hardware_asset_migration_recovery.json").read_text())["state"] == "COMPLETED"
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in original_hashes
    } == original_hashes
    assert bootstrap.is_file()


def test_startup_lock_rejects_second_coordinator(tmp_path: Path):
    app_root, data_root, bootstrap, resolver = _paths(tmp_path)
    lock_path = bootstrap.with_name(".hardware-startup.lock")
    with _StartupLock(lock_path):
        result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "HARDWARE_STARTUP_LOCKED"
    assert not data_root.exists()
