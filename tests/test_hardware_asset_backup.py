from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.web import create_p0_app
from services.hardware_asset_backup import (
    HardwareAssetBackupCoordinator,
    HardwareAssetBackupError,
)
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_durable_mutation_gate import HardwareDurableMutationGate
from services.hardware_startup_coordinator import HardwareStartupCoordinator, ROOT_RECOVERY_MARKER


def _ready_install(tmp_path: Path):
    application_root = tmp_path / "application"
    application_root.mkdir(parents=True)
    data_root = tmp_path / "persistent-data"
    bootstrap = tmp_path / "user-config" / "bootstrap.json"
    resolver = HardwareDataRootResolver(
        application_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap,
        legacy_roots=(),
    )
    status = HardwareStartupCoordinator(application_root, resolver=resolver).run()
    assert status["ready"] is True, status
    coordinator = HardwareAssetBackupCoordinator(resolver)
    source_store = HardwareCaseSourceStore(
        data_root / "db" / "hardware_case_mvp.db",
        data_root / "sources",
        initialize_schema=False,
    )
    return application_root, data_root, bootstrap, resolver, status, coordinator, source_store


def test_backup_set_contains_verified_multi_db_and_source_inventory(tmp_path: Path):
    _app, root, _bootstrap, _resolver, _status, backup, source_store = _ready_install(tmp_path)
    source_store.register_active_bytes("A0152", "A0152.docx", b"durable Word source")

    manifest = backup.create_backup(reason="MANUAL")

    directory = root / "backups" / manifest["backup_id"]
    assert manifest["backup_state"] == "PUBLISHED"
    assert set(backup.list_backups()[0]) == {"backup_id", "created_at", "reason", "backup_state"}
    assert backup.verify_backup(manifest["backup_id"]) == manifest
    assert {p.name for p in (directory / "db").iterdir()} == {
        "hardware_case_mvp.db",
        "hardware_asset.db",
        "workbench_runtime.db",
    }
    inventory = json.loads((directory / "manifest" / "source_inventory.json").read_text())
    assert len(inventory) == 1
    assert inventory[0]["business_case_id"] == "A0152"
    assert inventory[0]["relative_path"].startswith("sources/")
    assert (directory / inventory[0]["relative_path"]).read_bytes() == b"durable Word source"
    assert not (directory / "rebuildable").exists()
    assert str(root) not in (directory / "manifest" / "backup_manifest.json").read_text()
    assert HardwareDurableMutationGate(root).status()["state"] == "OPEN"
    assert not (root / ROOT_RECOVERY_MARKER).exists()


def test_backup_fails_closed_on_source_hash_mismatch_and_thaws_gate(tmp_path: Path):
    _app, root, _bootstrap, _resolver, _status, backup, source_store = _ready_install(tmp_path)
    metadata = source_store.register_active_bytes("A0162", "A0162.docx", b"original bytes")
    source_path = source_store.resolve_path(metadata["source_ref"])
    source_path.write_bytes(b"tampered bytes")

    try:
        backup.create_backup()
    except HardwareAssetBackupError as error:
        assert error.code == "BACKUP_SOURCE_HASH_MISMATCH"
    else:
        raise AssertionError("tampered Source must not be published")

    assert backup.list_backups() == []
    assert HardwareDurableMutationGate(root).status()["state"] == "OPEN"
    assert not (root / ROOT_RECOVERY_MARKER).exists()


def test_verify_rejects_manifest_paths_that_do_not_match_the_backup_payload(tmp_path: Path):
    _app, root, _bootstrap, _resolver, _status, backup, _source_store = _ready_install(tmp_path)
    manifest = backup.create_backup()
    directory = root / "backups" / manifest["backup_id"]
    manifest_path = directory / "manifest" / "backup_manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["hardware_db_file"] = "db/other.db"
    manifest_path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (directory / "manifest" / "backup_manifest.sha256").write_text(
        f"{digest}  backup_manifest.json\n", encoding="ascii"
    )

    with pytest.raises(HardwareAssetBackupError, match="BACKUP_MANIFEST_INVALID"):
        backup.verify_backup(manifest["backup_id"])


def test_frozen_gate_allows_reads_blocks_hardware_mutations_and_requires_offline_restore(tmp_path: Path):
    app_root, root, _bootstrap, _resolver, status, backup, _source = _ready_install(tmp_path)
    app = create_p0_app(
        root / "db" / "quality.db",
        project_root=app_root,
        enabled_domains={"HARDWARE_CASE"},
        hardware_case_db_path=root / "db" / "hardware_case_mvp.db",
        hardware_case_source_root=root / "sources",
        hardware_r1_workbench_db_path=root / "db" / "workbench_runtime.db",
        hardware_r1_preview_db_path=root / "rebuildable" / "preview.db",
        hardware_startup_status=status,
    )
    gate = HardwareDurableMutationGate(root)
    with TestClient(app) as client:
        with gate.freeze(operation_id="test-freeze", reason="TEST"):
            assert client.get("/api/system/hardware/startup").status_code == 200
            response = client.post("/api/v2/hardware-cases/trees/nodes", json={})
            assert response.status_code == 409
            assert response.json()["detail"] == "DURABLE_MUTATION_FROZEN"
        try:
            backup.restore("HKA-BK-not-present")
        except HardwareAssetBackupError as error:
            assert error.code == "RESTORE_REQUIRES_OFFLINE"
        else:
            raise AssertionError("restore must reject a live Hardware application")
    assert gate.status()["state"] == "OPEN"


def test_offline_restore_uses_rollback_backup_staging_and_atomic_activation(tmp_path: Path):
    _app, root, _bootstrap, resolver, _status, backup, source_store = _ready_install(tmp_path)
    source = source_store.register_active_bytes("A0207", "A0207.docx", b"original source")
    published = backup.create_backup(reason="MANUAL")
    source_store.delete_active_source("A0207", deleted_by="test")

    result = backup.restore(published["backup_id"])

    restored_source = HardwareCaseSourceStore(
        root / "db" / "hardware_case_mvp.db",
        root / "sources",
        initialize_schema=False,
    )
    assert result["status"] == "RESTORED"
    assert result["startup_status"] == "READY"
    assert restored_source.get_active_source("A0207")["source_id"] == source["source_id"]
    assert restored_source.resolve_path(source["source_ref"]).read_bytes() == b"original source"
    assert (root / "backups" / result["rollback_backup_id"]).is_dir()
    assert {item["backup_id"] for item in backup.list_backups()} >= {
        published["backup_id"],
        result["rollback_backup_id"],
    }
    assert (tmp_path / "user-config" / "bootstrap.json").is_file()
    assert HardwareStartupCoordinator(
        resolver.application_root,
        resolver=HardwareDataRootResolver(
            resolver.application_root,
            environment={"HARDWARE_DATA_ROOT": str(root)},
            bootstrap_path=tmp_path / "user-config" / "bootstrap.json",
            legacy_roots=(),
        ),
    ).run()["ready"] is True
