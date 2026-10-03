from __future__ import annotations

import json
from contextlib import closing
import sqlite3
from pathlib import Path

import pytest

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_data_reliability import (
    CURRENT_SCHEMA_VERSION,
    HardwareDataReliabilityError,
    HardwareDataReliabilityManager,
    SCHEMA_VERSION_NAME,
)


def _legacy_db(path: Path) -> None:
    repo = HardwareCaseRepository(path)
    repo.save_case(
        {
            "case_id": "HC-LEGACY-1",
            "title": "Legacy case",
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": ["word:legacy.docx"],
            "product_context": {"product": "Controller"},
            "facts": {},
        }
    )


def _case_title(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT title FROM hardware_case WHERE case_id='HC-LEGACY-1'"
        ).fetchone()
    return str(row[0]) if row else ""


def test_schema_registry_and_forward_migration_preserve_legacy_data(tmp_path):
    db = tmp_path / "hardware.db"
    _legacy_db(db)

    manager = HardwareDataReliabilityManager(db)
    result = manager.ensure_ready()

    assert result["status"] == "READY"
    assert result["schema_version"] == CURRENT_SCHEMA_VERSION
    assert result["schema_name"] == SCHEMA_VERSION_NAME
    assert result["backup_id"]
    assert _case_title(db) == "Legacy case"

    status = manager.inspect_status()
    assert status["status"] == "READY"
    assert status["schema_version"] == CURRENT_SCHEMA_VERSION
    assert status["fingerprint"]


def test_migration_is_idempotent_and_not_reapplied(tmp_path):
    db = tmp_path / "hardware.db"
    _legacy_db(db)
    manager = HardwareDataReliabilityManager(db)

    first = manager.ensure_ready()
    second = manager.ensure_ready()

    assert first["status"] == second["status"] == "READY"
    with closing(sqlite3.connect(db)) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM hardware_schema_migration"
        ).fetchone()[0]
    assert count == 2
    assert _case_title(db) == "Legacy case"


def test_unknown_schema_version_fails_closed(tmp_path):
    db = tmp_path / "hardware.db"
    manager = HardwareDataReliabilityManager(db)
    manager.ensure_ready()

    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            "UPDATE hardware_schema_version SET schema_version=999 WHERE singleton=1"
        )
        connection.commit()

    status = manager.inspect_status()
    assert status["status"] == "UNREADY"
    assert status["error_code"] == "UNKNOWN_SCHEMA_VERSION"
    with pytest.raises(HardwareDataReliabilityError) as exc:
        manager.ensure_ready()
    assert exc.value.code == "UNKNOWN_SCHEMA_VERSION"


def test_schema_drift_is_detected_even_when_sqlite_opens(tmp_path):
    db = tmp_path / "hardware.db"
    manager = HardwareDataReliabilityManager(db)
    manager.ensure_ready()

    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE unauthorized_drift(id TEXT)")
        connection.commit()

    status = manager.inspect_status()
    assert status["status"] == "UNREADY"
    assert status["error_code"] == "SCHEMA_DRIFT_DETECTED"


def test_pre_migration_backup_is_traceable(tmp_path):
    db = tmp_path / "hardware.db"
    _legacy_db(db)
    manager = HardwareDataReliabilityManager(db)

    result = manager.ensure_ready()
    backup_id = result["backup_id"]
    manifest_path = manager.backup_root / f"{backup_id}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["backup_id"] == backup_id
    assert manifest["reason"] == "PRE_MIGRATION"
    assert manifest["source_schema"] == 0
    assert manifest["target_schema"] == CURRENT_SCHEMA_VERSION
    assert len(manifest["backup_sha256"]) == 64
    assert (manager.backup_root / manifest["backup_file"]).is_file()


def test_backup_failure_blocks_migration_without_touching_data(tmp_path, monkeypatch):
    db = tmp_path / "hardware.db"
    _legacy_db(db)
    manager = HardwareDataReliabilityManager(db)

    def _fail_backup(**kwargs):
        raise HardwareDataReliabilityError("BACKUP_FAILED")

    monkeypatch.setattr(manager, "create_backup", _fail_backup)
    with pytest.raises(HardwareDataReliabilityError) as exc:
        manager.ensure_ready()

    assert exc.value.code == "BACKUP_FAILED"
    assert _case_title(db) == "Legacy case"
    with closing(sqlite3.connect(db)) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hardware_schema_version'"
        ).fetchone()
    assert table is None


def test_migration_failure_is_atomic_and_requires_recovery(tmp_path):
    db = tmp_path / "hardware.db"
    _legacy_db(db)

    def _fail(point: str):
        if point == "before_commit":
            raise RuntimeError("injected")

    manager = HardwareDataReliabilityManager(db, fault_injector=_fail)
    with pytest.raises(HardwareDataReliabilityError) as exc:
        manager.ensure_ready()

    assert exc.value.code == "MIGRATION_FAILED"
    assert exc.value.backup_id
    assert _case_title(db) == "Legacy case"
    failed = manager.inspect_status()
    assert failed["status"] == "UNREADY"
    assert failed["error_code"] == "MIGRATION_FAILED_RECOVERY_REQUIRED"

    recovered = HardwareDataReliabilityManager(db).recover_from_backup(
        exc.value.backup_id
    )
    assert recovered["status"] == "READY"
    assert _case_title(db) == "Legacy case"


def test_restore_same_environment_roundtrip(tmp_path):
    db = tmp_path / "hardware.db"
    _legacy_db(db)
    manager = HardwareDataReliabilityManager(db)
    manager.ensure_ready()
    backup = manager.create_backup(
        reason="MANUAL_TEST",
        source_schema=CURRENT_SCHEMA_VERSION,
        target_schema=CURRENT_SCHEMA_VERSION,
    )

    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "UPDATE hardware_case SET title='Mutated' WHERE case_id='HC-LEGACY-1'"
        )
        connection.commit()
    finally:
        connection.close()
    assert _case_title(db) == "Mutated"

    restored = manager.restore(backup["backup_id"])
    assert restored["status"] == "RESTORED"
    assert _case_title(db) == "Legacy case"
    assert manager.inspect_status()["status"] == "READY"


def test_restore_fresh_environment_is_immediately_verifiable(tmp_path):
    source = tmp_path / "source.db"
    _legacy_db(source)
    manager = HardwareDataReliabilityManager(source)
    manager.ensure_ready()
    backup = manager.create_backup(
        reason="PORTABLE_RESTORE",
        source_schema=CURRENT_SCHEMA_VERSION,
        target_schema=CURRENT_SCHEMA_VERSION,
    )

    fresh = tmp_path / "fresh" / "hardware.db"
    restored = manager.restore(backup["backup_id"], target_db_path=fresh)
    assert restored["status"] == "RESTORED"
    assert _case_title(fresh) == "Legacy case"

    fresh_status = HardwareDataReliabilityManager(fresh).inspect_status()
    assert fresh_status["status"] == "READY"
    assert fresh_status["schema_version"] == CURRENT_SCHEMA_VERSION


def test_restore_rejects_tampered_backup(tmp_path):
    db = tmp_path / "hardware.db"
    manager = HardwareDataReliabilityManager(db)
    manager.ensure_ready()
    backup = manager.create_backup(
        reason="TAMPER_TEST",
        source_schema=CURRENT_SCHEMA_VERSION,
        target_schema=CURRENT_SCHEMA_VERSION,
    )
    manifest = json.loads(
        (manager.backup_root / f"{backup['backup_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    backup_db = manager.backup_root / manifest["backup_file"]
    backup_db.write_bytes(backup_db.read_bytes() + b"tamper")

    with pytest.raises(HardwareDataReliabilityError) as exc:
        manager.restore(backup["backup_id"])
    assert exc.value.code == "BACKUP_HASH_MISMATCH"
