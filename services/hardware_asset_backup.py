"""Coordinated, verifiable Durable Asset backup sets and offline restore."""
from __future__ import annotations

import hashlib
import json
import os
import gc
import shutil
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping
from uuid import uuid4

from services.hardware_data_reliability import (
    CURRENT_SCHEMA_VERSION,
    HardwareDataReliabilityError,
    HardwareDataReliabilityManager,
)
from services.hardware_asset_repository import ASSET_SCHEMA_VERSION
from services.hardware_case_r1_workbench import HardwareR1WorkbenchStore
from services.hardware_data_root import (
    DATA_LAYOUT_VERSION,
    MANIFEST_RELATIVE_PATH,
    PERSISTENT_DATA_RELATIVE_PATHS,
    HardwareDataRootResolution,
    HardwareDataRootResolver,
)
from services.hardware_durable_mutation_gate import (
    HardwareApplicationLock,
    HardwareDurableMutationError,
    HardwareDurableMutationGate,
)
from services.hardware_startup_coordinator import (
    HARDWARE_APP_VERSION,
    ROOT_RECOVERY_MARKER,
    WORKBENCH_SCHEMA_VERSION,
    HardwareStartupCoordinator,
    HardwareStartupError,
    _StartupLock,
)


BACKUP_CONTRACT_VERSION = "hardware-knowledge-durable-backup/v1"
BACKUP_ID_PREFIX = "HKA-BK-"
BACKUP_REASONS = frozenset(
    {"MANUAL", "PRE_UPGRADE", "PRE_MIGRATION", "PRE_RESTORE_ROLLBACK", "RECOVERY_CHECKPOINT"}
)
DB_FILES = {
    "hardware_db": "hardware_case_mvp.db",
    "asset_db": "hardware_asset.db",
    "workbench_db": "workbench_runtime.db",
}
ROOT_OPERATION_LOCK_NAME = ".hardware-startup.lock"


class HardwareAssetBackupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise HardwareAssetBackupError("BACKUP_STAGING_FAILED") from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HardwareAssetBackupError(code) from error
    if not isinstance(value, dict):
        raise HardwareAssetBackupError(code)
    return value


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    if table not in _table_names(connection):
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _db_integrity(path: Path, code: str) -> None:
    try:
        with closing(_connect_readonly(path)) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as error:
        raise HardwareAssetBackupError(code) from error
    if row is None or str(row[0]).lower() != "ok":
        raise HardwareAssetBackupError(code)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _copy_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return
    if source.is_symlink() or not source.is_dir():
        raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
    for item in source.iterdir():
        if item.is_symlink():
            raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
        destination = target / item.name
        if item.is_dir():
            _copy_tree(item, destination)
        elif item.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
        else:
            raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")


class HardwareAssetBackupCoordinator:
    """Create complete Backup Sets and perform offline, staged restoration."""

    def __init__(
        self,
        resolver: HardwareDataRootResolver,
        *,
        application_version: str = HARDWARE_APP_VERSION,
        mutation_gate: HardwareDurableMutationGate | None = None,
    ):
        self.resolver = resolver
        self.application_version = str(application_version)
        resolution = resolver.resolve()
        self.resolution = resolution
        if resolution.data_root is None:
            raise HardwareAssetBackupError("BACKUP_SOURCE_MISSING")
        self.data_root = resolution.data_root.resolve(strict=False)
        self.bootstrap_path = resolution.bootstrap_path
        self.backup_root = self.data_root / "backups"
        self.mutation_gate = mutation_gate or HardwareDurableMutationGate(self.data_root)
        self._app_lock = HardwareApplicationLock(self.data_root)
        self._root_lock_path = self.bootstrap_path.with_name(ROOT_OPERATION_LOCK_NAME)

    def _require_current_install(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.resolution.classification != "EXISTING_INSTALL" or not self.data_root.is_dir():
            raise HardwareAssetBackupError("BACKUP_SOURCE_MISSING")
        data_manifest = _read_json(self.data_root / MANIFEST_RELATIVE_PATH, "BACKUP_MANIFEST_INVALID")
        bootstrap = _read_json(self.bootstrap_path, "BACKUP_MANIFEST_INVALID")
        installation_id = str(data_manifest.get("installation_id") or "")
        if not installation_id or bootstrap.get("installation_id") != installation_id:
            raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
        if Path(str(bootstrap.get("persistent_data_root") or "")).resolve(strict=False) != self.data_root:
            raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
        return data_manifest, bootstrap

    def _root_operation_marker(self, operation_id: str, operation: str, state: str) -> None:
        marker_path = self.data_root / ROOT_RECOVERY_MARKER
        if marker_path.exists():
            existing = _read_json(marker_path, "BACKUP_LOCKED")
            if existing.get("operation_id") != operation_id or existing.get("operation") not in {operation, "RESTORE"}:
                raise HardwareAssetBackupError("BACKUP_LOCKED")
        _write_atomic(
            marker_path,
            _pretty_json(
                {
                    "contract_version": "hardware-root-recovery/v1",
                    "operation_id": operation_id,
                    "operation": operation,
                    "state": state,
                    "installation_id": self._installation_id(),
                    "target_root": str(self.data_root),
                    "updated_at": _now(),
                }
            ),
        )

    def _installation_id(self) -> str:
        manifest = _read_json(self.data_root / MANIFEST_RELATIVE_PATH, "BACKUP_MANIFEST_INVALID")
        return str(manifest.get("installation_id") or "")

    def _clear_root_marker(self) -> None:
        try:
            (self.data_root / ROOT_RECOVERY_MARKER).unlink()
        except FileNotFoundError:
            pass

    def _source_inventory(self) -> list[dict[str, Any]]:
        hardware_db = self.data_root / "db" / DB_FILES["hardware_db"]
        source_root = (self.data_root / PERSISTENT_DATA_RELATIVE_PATHS["sources"]).resolve(strict=True)
        try:
            with closing(_connect_readonly(hardware_db)) as connection:
                connection.row_factory = sqlite3.Row
                tables = _table_names(connection)
                required = {"hardware_case_source_registry", "hardware_r1_source_binding"}
                if not required.issubset(tables):
                    raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
                rows = connection.execute(
                    """
                    SELECT b.business_case_id,r.source_ref,r.source_id,r.relative_path,
                           r.display_name,r.size_bytes,r.sha256,r.source_status,
                           b.source_status AS binding_status
                    FROM hardware_case_source_registry r
                    LEFT JOIN hardware_r1_source_binding b ON b.source_ref=r.source_ref
                    ORDER BY COALESCE(b.business_case_id,''),r.source_ref,r.relative_path
                    """
                ).fetchall()
        except HardwareAssetBackupError:
            raise
        except sqlite3.Error as error:
            raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID") from error

        inventory: list[dict[str, Any]] = []
        for row in rows:
            raw_relative = str(row["relative_path"] or "")
            relative = PurePosixPath(raw_relative)
            if (
                not raw_relative
                or "\\" in raw_relative
                or relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
            source_path = source_root.joinpath(*relative.parts).resolve(strict=True)
            try:
                source_path.relative_to(source_root)
            except ValueError as error:
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID") from error
            expected_hash = str(row["sha256"] or "").lower()
            source_id = str(row["source_id"] or "").lower()
            if (
                not source_path.is_file()
                or source_path.stat().st_size != int(row["size_bytes"] or 0)
                or _sha256(source_path) != expected_hash
                or source_id != expected_hash
                or str(row["source_status"] or "") != "AVAILABLE"
                or (row["binding_status"] is not None and str(row["binding_status"]) != "ACTIVE")
            ):
                raise HardwareAssetBackupError("BACKUP_SOURCE_HASH_MISMATCH")
            inventory.append(
                {
                    "business_case_id": str(row["business_case_id"]) if row["business_case_id"] is not None else None,
                    "source_ref": str(row["source_ref"]),
                    "source_id": source_id,
                    "relative_path": (PurePosixPath("sources") / relative).as_posix(),
                    "display_name": str(row["display_name"]),
                    "size_bytes": int(row["size_bytes"]),
                    "sha256": expected_hash,
                    "source_status": str(row["source_status"]),
                }
            )
        inventory.sort(key=lambda item: (item["business_case_id"] or "", item["source_ref"], item["relative_path"]))
        return inventory

    def _copy_sources(self, stage: Path, inventory: list[dict[str, Any]]) -> None:
        original_root = (self.data_root / "sources").resolve(strict=True)
        backup_root = stage / "sources"
        backup_root.mkdir(parents=True, exist_ok=True)
        for item in inventory:
            rel = PurePosixPath(item["relative_path"])
            if not rel.parts or rel.parts[0] != "sources" or any(part in {"", ".", ".."} for part in rel.parts):
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
            suffix = PurePosixPath(*rel.parts[1:])
            source = original_root.joinpath(*suffix.parts).resolve(strict=True)
            try:
                source.relative_to(original_root)
            except ValueError as error:
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID") from error
            target = backup_root.joinpath(*suffix.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if target.stat().st_size != item["size_bytes"] or _sha256(target) != item["sha256"]:
                raise HardwareAssetBackupError("BACKUP_SOURCE_HASH_MISMATCH")

    def _snapshot_db(self, key: str, stage: Path, source_schema: int | None) -> str:
        source = self.data_root / "db" / DB_FILES[key]
        if not source.is_file() or source.stat().st_size == 0:
            raise HardwareAssetBackupError("BACKUP_SOURCE_MISSING")
        destination = stage / "db" / DB_FILES[key]
        destination.parent.mkdir(parents=True, exist_ok=True)
        work_root = stage / ".sqlite-online-backup-work" / key
        manager = HardwareDataReliabilityManager(source, backup_root=work_root)
        try:
            snapshot = manager.create_backup(
                reason="HARDWARE_DURABLE_BACKUP_SET",
                source_schema=source_schema,
                target_schema=source_schema,
            )
            snapshot_path = work_root / str(snapshot["backup_file"])
            shutil.copy2(snapshot_path, destination)
        except Exception as error:
            if isinstance(error, HardwareDataReliabilityError):
                raise HardwareAssetBackupError("BACKUP_DB_INTEGRITY_FAILED") from error
            raise HardwareAssetBackupError("BACKUP_DB_INTEGRITY_FAILED") from error
        _db_integrity(destination, "BACKUP_DB_INTEGRITY_FAILED")
        return _sha256(destination)

    def _counts(self, db_root: Path) -> dict[str, int]:
        hardware = db_root / DB_FILES["hardware_db"]
        asset = db_root / DB_FILES["asset_db"]
        workbench = db_root / DB_FILES["workbench_db"]
        try:
            with closing(_connect_readonly(hardware)) as h, closing(
                _connect_readonly(asset)
            ) as a, closing(_connect_readonly(workbench)) as w:
                hc, ac, wc = _table_names(h), _table_names(a), _table_names(w)
                return {
                    "source_count": _table_count(h, "hardware_case_source_registry"),
                    "candidate_count": _table_count(a, "hardware_candidate_asset"),
                    "production_review_count": _table_count(a, "hardware_candidate_review"),
                    "promotion_count": _table_count(a, "hardware_asset_promotion"),
                    "formal_reference_count": _table_count(h, "hardware_r1_source_formal_reference"),
                    "batch_count": _table_count(w, "hardware_r1_batch"),
                    "batch_item_count": _table_count(w, "hardware_r1_batch_item"),
                }
        except sqlite3.Error as error:
            raise HardwareAssetBackupError("BACKUP_CROSS_REFERENCE_INVALID") from error

    def _build_manifest(
        self,
        *,
        backup_id: str,
        reason: str,
        data_manifest: Mapping[str, Any],
        db_hashes: Mapping[str, str],
        inventory: list[dict[str, Any]],
        counts: Mapping[str, int],
        state: str,
    ) -> dict[str, Any]:
        return {
            "contract_version": BACKUP_CONTRACT_VERSION,
            "backup_id": backup_id,
            "installation_id": data_manifest.get("installation_id"),
            "data_layout_version": int(data_manifest.get("data_layout_version") or DATA_LAYOUT_VERSION),
            "application_version": self.application_version,
            "created_at": _now(),
            "reason": reason,
            "hardware_schema_version": int(data_manifest.get("hardware_schema_version") or 0),
            "asset_schema_version": int(data_manifest.get("asset_schema_version") or 0),
            "workbench_schema_version": int(data_manifest.get("workbench_schema_version") or 0),
            "hardware_db_file": f"db/{DB_FILES['hardware_db']}",
            "hardware_db_sha256": db_hashes["hardware_db"],
            "asset_db_file": f"db/{DB_FILES['asset_db']}",
            "asset_db_sha256": db_hashes["asset_db"],
            "workbench_db_file": f"db/{DB_FILES['workbench_db']}",
            "workbench_db_sha256": db_hashes["workbench_db"],
            "source_count": len(inventory),
            "source_total_bytes": sum(int(item["size_bytes"]) for item in inventory),
            "source_inventory_sha256": hashlib.sha256(_canonical_json(inventory)).hexdigest(),
            **dict(counts),
            "backup_state": state,
        }

    def _write_manifest(self, directory: Path, manifest: Mapping[str, Any]) -> None:
        path = directory / "manifest" / "backup_manifest.json"
        payload = _pretty_json(dict(manifest))
        _write_atomic(path, payload)
        _write_atomic(directory / "manifest" / "backup_manifest.sha256", (_sha256(path) + "  backup_manifest.json\n").encode("ascii"))

    def _write_inventory(self, directory: Path, inventory: list[dict[str, Any]]) -> None:
        _write_atomic(directory / "manifest" / "source_inventory.json", _canonical_json(inventory) + b"\n")

    def _write_backup_audit(self, event: str, operation_id: str, backup_id: str, result: str, error_code: str | None = None) -> None:
        audit_root = self.data_root / "audit"
        audit_root.mkdir(parents=True, exist_ok=True)
        path = audit_root / "hardware_backup_restore.jsonl"
        item = {
            "operation_id": operation_id,
            "backup_id": backup_id,
            "actor": "LOCAL_OPERATOR",
            "application_version": self.application_version,
            "result": result,
            "error_code": error_code,
            "event": event,
            "started_at": _now(),
            "completed_at": _now() if event.endswith(("PUBLISHED", "FAILED", "ACTIVATED", "ROLLED_BACK")) else None,
        }
        with path.open("ab") as stream:
            stream.write(_canonical_json(item) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    @contextmanager
    def _root_lock(self) -> Iterator[None]:
        lock = _StartupLock(self._root_lock_path)
        try:
            lock.__enter__()
        except HardwareStartupError as error:
            raise HardwareAssetBackupError("BACKUP_LOCKED") from error
        try:
            yield
        finally:
            lock.__exit__(None, None, None)

    @contextmanager
    def _backup_lock(self) -> Iterator[None]:
        # The restore operation holds this lock while swapping the Data Root;
        # store it beside the root so the lock itself is not an open child.
        lock = _StartupLock(self.data_root.parent / f".{self.data_root.name}.hardware-backup.lock")
        try:
            lock.__enter__()
        except HardwareStartupError as error:
            raise HardwareAssetBackupError("BACKUP_LOCKED") from error
        try:
            yield
        finally:
            lock.__exit__(None, None, None)

    def create_backup(self, *, reason: str = "MANUAL") -> dict[str, Any]:
        reason = str(reason or "").strip().upper()
        if reason not in BACKUP_REASONS:
            raise HardwareAssetBackupError("BACKUP_REASON_INVALID")
        with self._root_lock(), self._backup_lock():
            return self._create_backup_locked(reason)

    def _create_backup_locked(self, reason: str) -> dict[str, Any]:
        data_manifest, bootstrap = self._require_current_install()
        operation_id = uuid4().hex
        backup_id = BACKUP_ID_PREFIX + uuid4().hex.upper()
        stage = self.backup_root / f".creating-{backup_id}"
        final = self.backup_root / backup_id
        marker_written = False
        manifest: dict[str, Any] | None = None
        self.backup_root.mkdir(parents=True, exist_ok=True)
        if final.exists() or stage.exists():
            raise HardwareAssetBackupError("BACKUP_PUBLISH_FAILED")
        try:
            self._root_operation_marker(operation_id, "BACKUP", "FREEZING")
            marker_written = True
            self._write_backup_audit("BACKUP_STARTED", operation_id, backup_id, "STARTED")
            with self.mutation_gate.freeze(operation_id=operation_id, reason=reason):
                stage.mkdir(parents=True, exist_ok=False)
                for child in ("manifest", "db", "sources", "audit"):
                    (stage / child).mkdir(parents=True, exist_ok=True)
                bootstrap_snapshot = {
                    "contract_version": bootstrap.get("contract_version"),
                    "installation_id": bootstrap.get("installation_id"),
                    "data_layout_version": bootstrap.get("data_layout_version"),
                    "created_at": bootstrap.get("created_at"),
                    "captured_at": _now(),
                }
                _write_atomic(stage / "bootstrap_snapshot.json", _pretty_json(bootstrap_snapshot))
                _write_atomic(stage / "manifest" / "data_root_manifest.json", _pretty_json(data_manifest))
                inventory = self._source_inventory()
                self._write_inventory(stage, inventory)
                db_hashes = {
                    "hardware_db": self._snapshot_db(
                        "hardware_db", stage, int(data_manifest.get("hardware_schema_version") or 0)
                    ),
                    "asset_db": self._snapshot_db(
                        "asset_db", stage, int(data_manifest.get("asset_schema_version") or 0)
                    ),
                    "workbench_db": self._snapshot_db(
                        "workbench_db", stage, int(data_manifest.get("workbench_schema_version") or 0)
                    ),
                }
                shutil.rmtree(stage / ".sqlite-online-backup-work", ignore_errors=True)
                self._copy_sources(stage, inventory)
                _copy_tree(self.data_root / "audit", stage / "audit")
                counts = self._counts(stage / "db")
                self._verify_payload(stage, None, expected_inventory=inventory, verify_manifest=False)
                manifest = self._build_manifest(
                    backup_id=backup_id,
                    reason=reason,
                    data_manifest=data_manifest,
                    db_hashes=db_hashes,
                    inventory=inventory,
                    counts=counts,
                    state="VERIFIED",
                )
                self._write_manifest(stage, manifest)
                self._root_operation_marker(operation_id, "BACKUP", "VERIFIED")

            self._root_operation_marker(operation_id, "BACKUP", "PUBLISHING")
            gc.collect()
            os.replace(stage, final)
            manifest = {**(manifest or {}), "backup_state": "PUBLISHED"}
            self._write_manifest(final, manifest)
            self._verify_payload(final, manifest, expected_inventory=None, verify_manifest=True)
            self._write_backup_audit("BACKUP_PUBLISHED", operation_id, backup_id, "PUBLISHED")
            self._clear_root_marker()
            marker_written = False
            return dict(manifest)
        except Exception as error:
            code = str(getattr(error, "code", None) or "BACKUP_STAGING_FAILED")
            if stage.exists():
                try:
                    if manifest is not None:
                        self._write_manifest(stage, {**manifest, "backup_state": "FAILED", "error_code": code})
                except Exception:
                    pass
            try:
                self._write_backup_audit("BACKUP_FAILED", operation_id, backup_id, "FAILED", code)
            except OSError:
                pass
            if marker_written:
                try:
                    if self.mutation_gate.status().get("state") == "OPEN":
                        self._clear_root_marker()
                except HardwareDurableMutationError:
                    pass
            if final.exists():
                try:
                    bad_manifest = _read_json(final / "manifest" / "backup_manifest.json", "BACKUP_MANIFEST_INVALID")
                    self._write_manifest(final, {**bad_manifest, "backup_state": "FAILED", "error_code": code})
                except Exception:
                    pass
            if isinstance(error, HardwareAssetBackupError):
                raise
            if isinstance(error, HardwareDurableMutationError):
                raise HardwareAssetBackupError(error.code) from error
            raise HardwareAssetBackupError(code) from error

    def _verify_payload(
        self,
        directory: Path,
        manifest: Mapping[str, Any] | None,
        *,
        expected_inventory: list[dict[str, Any]] | None,
        verify_manifest: bool,
    ) -> dict[str, Any]:
        if verify_manifest:
            manifest_path = directory / "manifest" / "backup_manifest.json"
            actual_sidecar = (directory / "manifest" / "backup_manifest.sha256").read_text(encoding="ascii").split()[0]
            if actual_sidecar != _sha256(manifest_path):
                raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
            loaded = _read_json(manifest_path, "BACKUP_MANIFEST_INVALID")
            if loaded.get("backup_state") != "PUBLISHED" or loaded.get("contract_version") != BACKUP_CONTRACT_VERSION:
                raise HardwareAssetBackupError("BACKUP_NOT_PUBLISHED")
            if loaded.get("reason") not in BACKUP_REASONS:
                raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
            data_root_manifest = _read_json(
                directory / "manifest" / "data_root_manifest.json",
                "BACKUP_MANIFEST_INVALID",
            )
            bootstrap_snapshot = _read_json(
                directory / "bootstrap_snapshot.json",
                "BACKUP_MANIFEST_INVALID",
            )
            if (
                data_root_manifest.get("installation_id") != loaded.get("installation_id")
                or bootstrap_snapshot.get("installation_id") != loaded.get("installation_id")
                or data_root_manifest.get("data_layout_version") != loaded.get("data_layout_version")
            ):
                raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
            try:
                for key, filename in DB_FILES.items():
                    if loaded.get(f"{key}_file") != f"db/{filename}":
                        raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
                    schema_key = f"{key.removesuffix('_db')}_schema_version"
                    if int(data_root_manifest.get(schema_key) or 0) != int(loaded.get(schema_key) or 0):
                        raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
            except (TypeError, ValueError) as error:
                raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID") from error
            manifest = loaded
        if manifest is None and verify_manifest:
            raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
        inventory_path = directory / "manifest" / "source_inventory.json"
        try:
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID") from error
        if not isinstance(inventory, list) or hashlib.sha256(_canonical_json(inventory)).hexdigest() != (
            (manifest or {}).get("source_inventory_sha256")
            if manifest is not None
            else hashlib.sha256(_canonical_json(inventory)).hexdigest()
        ):
            if manifest is not None or expected_inventory is not None:
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
        if expected_inventory is not None and inventory != expected_inventory:
            raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
        if inventory != sorted(
            inventory,
            key=lambda item: (
                str(item.get("business_case_id") or ""),
                str(item.get("source_ref") or ""),
                str(item.get("relative_path") or ""),
            ),
        ):
            raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")

        for key, filename in DB_FILES.items():
            path = directory / "db" / filename
            if not path.is_file() or path.stat().st_size == 0:
                raise HardwareAssetBackupError("BACKUP_SOURCE_MISSING")
            code = "BACKUP_DB_INTEGRITY_FAILED"
            _db_integrity(path, code)
            if manifest is not None and _sha256(path) != str(manifest.get(f"{key}_sha256") or ""):
                raise HardwareAssetBackupError("BACKUP_DB_HASH_MISMATCH")
        for item in inventory:
            rel = PurePosixPath(str(item.get("relative_path") or ""))
            if not rel.parts or rel.parts[0] != "sources" or rel.is_absolute() or any(p in {"", ".", ".."} for p in rel.parts):
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
            source = directory.joinpath(*rel.parts).resolve(strict=True)
            try:
                source.relative_to((directory / "sources").resolve(strict=True))
            except ValueError as error:
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID") from error
            if source.stat().st_size != int(item.get("size_bytes") or -1) or _sha256(source) != str(item.get("sha256") or ""):
                raise HardwareAssetBackupError("BACKUP_SOURCE_HASH_MISMATCH")

        if all((directory / "db" / name).is_file() for name in DB_FILES.values()):
            try:
                startup = HardwareStartupCoordinator(directory, resolver=self._staging_resolver(directory))
                startup._verify_active_sources(
                    directory / "db" / DB_FILES["hardware_db"], directory / "sources"
                )
                startup._verify_cross_references(
                    directory / "db" / DB_FILES["hardware_db"],
                    directory / "db" / DB_FILES["asset_db"],
                    directory / "db" / DB_FILES["workbench_db"],
                )
            except HardwareStartupError as error:
                raise HardwareAssetBackupError("BACKUP_CROSS_REFERENCE_INVALID") from error
        counts = self._counts(directory / "db")
        if manifest is not None:
            for key, value in counts.items():
                if int(manifest.get(key, -1)) != value:
                    raise HardwareAssetBackupError("BACKUP_CROSS_REFERENCE_INVALID")
            if int(manifest.get("source_count", -1)) != len(inventory):
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
            if int(manifest.get("source_total_bytes", -1)) != sum(
                int(item.get("size_bytes") or 0) for item in inventory
            ):
                raise HardwareAssetBackupError("BACKUP_SOURCE_INVENTORY_INVALID")
        return {"inventory": inventory, "counts": counts}

    def _staging_resolver(self, root: Path) -> HardwareDataRootResolver:
        # Cross-reference verification does not read the resolver; the explicit
        # isolated root prevents accidental fallback to the production install.
        return HardwareDataRootResolver(
            root,
            environment={"HARDWARE_DATA_ROOT": str(root)},
            bootstrap_path=root.parent / ("." + root.name + ".verification-bootstrap.json"),
            legacy_roots=(),
        )

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_root.is_dir():
            return []
        output: list[dict[str, Any]] = []
        for directory in sorted(self.backup_root.glob(BACKUP_ID_PREFIX + "*")):
            if not directory.is_dir():
                continue
            try:
                manifest = _read_json(directory / "manifest" / "backup_manifest.json", "BACKUP_MANIFEST_INVALID")
                if manifest.get("backup_state") == "PUBLISHED":
                    output.append(
                        {
                            "backup_id": manifest.get("backup_id"),
                            "created_at": manifest.get("created_at"),
                            "reason": manifest.get("reason"),
                            "backup_state": "PUBLISHED",
                        }
                    )
            except HardwareAssetBackupError:
                continue
        return output

    def verify_backup(self, backup_id: str) -> dict[str, Any]:
        directory = self.backup_root / str(backup_id or "")
        try:
            directory.resolve(strict=True).relative_to(self.backup_root.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise HardwareAssetBackupError("BACKUP_NOT_PUBLISHED") from error
        manifest = _read_json(directory / "manifest" / "backup_manifest.json", "BACKUP_MANIFEST_INVALID")
        if manifest.get("backup_id") != directory.name:
            raise HardwareAssetBackupError("BACKUP_MANIFEST_INVALID")
        self._verify_payload(directory, manifest, expected_inventory=None, verify_manifest=True)
        return manifest

    def _restore_source_precheck(self, backup_manifest: Mapping[str, Any]) -> dict[str, Any]:
        data_manifest, _bootstrap = self._require_current_install()
        if backup_manifest.get("installation_id") != data_manifest.get("installation_id"):
            raise HardwareAssetBackupError("BACKUP_INSTALLATION_ID_MISMATCH")
        if int(backup_manifest.get("data_layout_version") or 0) != DATA_LAYOUT_VERSION:
            raise HardwareAssetBackupError("RESTORE_PRECHECK_FAILED")
        versions = (
            int(backup_manifest.get("hardware_schema_version") or 0),
            int(backup_manifest.get("asset_schema_version") or 0),
            int(backup_manifest.get("workbench_schema_version") or 0),
        )
        current = (CURRENT_SCHEMA_VERSION, ASSET_SCHEMA_VERSION, WORKBENCH_SCHEMA_VERSION)
        if any(source > target for source, target in zip(versions, current)):
            raise HardwareAssetBackupError("BACKUP_SCHEMA_TOO_NEW")
        return data_manifest

    def restore(self, backup_id: str) -> dict[str, Any]:
        """Restore a PUBLISHED Backup Set to the same offline installation."""
        try:
            self._app_lock.acquire()
        except HardwareDurableMutationError as error:
            raise HardwareAssetBackupError("RESTORE_REQUIRES_OFFLINE") from error
        operation_id = uuid4().hex
        restore_id = "HKA-RS-" + uuid4().hex.upper()
        stage = self.data_root.parent / f".restore-staging-{restore_id}"
        rollback = self.data_root.parent / f".rollback-{restore_id}"
        marker_written = False
        try:
            with self._root_lock(), self._backup_lock():
                backup_manifest = self.verify_backup(backup_id)
                current_manifest = self._restore_source_precheck(backup_manifest)
                rollback_info = self._create_backup_locked("PRE_RESTORE_ROLLBACK")
                with self.mutation_gate.freeze(operation_id=operation_id, reason="RESTORE"):
                    marker = {
                        "contract_version": "hardware-root-recovery/v1",
                        "operation_id": operation_id,
                        "operation": "RESTORE",
                        "state": "STAGING",
                        "restore_id": restore_id,
                        "backup_id": backup_id,
                        "rollback_root": str(rollback),
                        "staging_root": str(stage),
                        "target_root": str(self.data_root),
                        "installation_id": current_manifest.get("installation_id"),
                        "updated_at": _now(),
                    }
                    self.data_root.mkdir(parents=True, exist_ok=True)
                    _write_atomic(self.data_root / ROOT_RECOVERY_MARKER, _pretty_json(marker))
                    marker_written = True
                    if stage.exists() or rollback.exists():
                        raise HardwareAssetBackupError("RESTORE_STAGING_FAILED")
                    self._build_restore_staging(backup_id, backup_manifest, stage, current_manifest, rollback_info["backup_id"])
                    marker["state"] = "VERIFIED"
                    marker["updated_at"] = _now()
                    _write_atomic(self.data_root / ROOT_RECOVERY_MARKER, _pretty_json(marker))
                    _write_atomic(stage / ROOT_RECOVERY_MARKER, _pretty_json(marker))
                    self._activate_restore(stage, rollback, operation_id, backup_id)
                    self._clear_root_marker()
            # The restore/startup lock is released before invoking the normal
            # StartupCoordinator verification, which acquires that same lock.
            final_resolver = HardwareDataRootResolver(
                self.resolver.application_root,
                environment={"HARDWARE_DATA_ROOT": str(self.data_root)},
                bootstrap_path=self.bootstrap_path,
                legacy_roots=(),
            )
            ready = HardwareStartupCoordinator(
                self.resolver.application_root,
                resolver=final_resolver,
            ).run()
            if not ready.get("ready"):
                _write_atomic(self.data_root / ROOT_RECOVERY_MARKER, _pretty_json({**marker, "state": "POST_ACTIVATION_VERIFY_FAILED", "updated_at": _now()}))
                raise HardwareAssetBackupError("RESTORE_RECOVERY_REQUIRED")
            marker_written = False
            self._write_backup_audit("RESTORE_ACTIVATED", operation_id, backup_id, "ACTIVATED")
            return {
                "restore_id": restore_id,
                "backup_id": backup_id,
                "rollback_backup_id": rollback_info["backup_id"],
                "status": "RESTORED",
                "startup_status": "READY",
                "rollback_root": rollback.name,
            }
        except Exception as error:
            code = str(getattr(error, "code", None) or "RESTORE_STAGING_FAILED")
            if marker_written:
                # Preserve the marker and both roots for deterministic A7 recovery.
                pass
            try:
                self._write_backup_audit("RESTORE_FAILED", operation_id, backup_id, "FAILED", code)
            except OSError:
                pass
            if isinstance(error, HardwareAssetBackupError):
                raise
            if isinstance(error, HardwareDurableMutationError):
                raise HardwareAssetBackupError(error.code) from error
            raise HardwareAssetBackupError(code) from error
        finally:
            self._app_lock.release()

    def _build_restore_staging(
        self,
        backup_id: str,
        backup_manifest: Mapping[str, Any],
        stage: Path,
        current_manifest: Mapping[str, Any],
        rollback_backup_id: str,
    ) -> None:
        source = self.backup_root / backup_id
        stage.mkdir(parents=True, exist_ok=False)
        for relative in ("manifest", "db", "sources", "audit", "backups", "rebuildable"):
            (stage / relative).mkdir(parents=True, exist_ok=True)
        data_manifest = _read_json(source / "manifest" / "data_root_manifest.json", "BACKUP_MANIFEST_INVALID")
        inventory = json.loads((source / "manifest" / "source_inventory.json").read_text(encoding="utf-8"))

        for key, filename in DB_FILES.items():
            src = source / "db" / filename
            dest = stage / "db" / filename
            work = stage / ".sqlite-restore-work" / key
            snapshot = HardwareDataReliabilityManager(src, backup_root=work).create_backup(
                reason="HARDWARE_OFFLINE_RESTORE",
                source_schema=int(backup_manifest.get(f"{key.replace('_db','')}_schema_version") or 0),
                target_schema=int(backup_manifest.get(f"{key.replace('_db','')}_schema_version") or 0),
            )
            shutil.copy2(work / str(snapshot["backup_file"]), dest)
            _db_integrity(dest, "RESTORE_DB_INTEGRITY_FAILED")
        shutil.rmtree(stage / ".sqlite-restore-work", ignore_errors=True)

        if int(backup_manifest.get("workbench_schema_version") or 0) < WORKBENCH_SCHEMA_VERSION:
            try:
                HardwareR1WorkbenchStore(stage / "db" / DB_FILES["workbench_db"])
            except Exception as error:
                raise HardwareAssetBackupError("RESTORE_SCHEMA_MIGRATION_FAILED") from error
            data_manifest["workbench_schema_version"] = WORKBENCH_SCHEMA_VERSION

        for item in inventory:
            rel = PurePosixPath(str(item["relative_path"]))
            relative = PurePosixPath(*rel.parts[1:])
            src = source / "sources" / Path(*relative.parts)
            dest = stage / "sources" / Path(*relative.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if dest.stat().st_size != int(item["size_bytes"]) or _sha256(dest) != str(item["sha256"]):
                raise HardwareAssetBackupError("RESTORE_SOURCE_INTEGRITY_FAILED")

        _copy_tree(source / "audit", stage / "audit")
        # Preserve already published restore sources and the rollback point
        # across the root switch; never carry partial .creating-* directories.
        for published in self.list_backups():
            directory = self.backup_root / str(published["backup_id"])
            _copy_tree(directory, stage / "backups" / directory.name)
        if rollback_backup_id not in {item["backup_id"] for item in self.list_backups()}:
            rollback_dir = self.backup_root / rollback_backup_id
            if rollback_dir.is_dir():
                _copy_tree(rollback_dir, stage / "backups" / rollback_backup_id)

        data_manifest.update(
            {
                "installation_id": current_manifest.get("installation_id"),
                "data_layout_version": DATA_LAYOUT_VERSION,
                "hardware_schema_version": int(backup_manifest.get("hardware_schema_version") or 0),
                "asset_schema_version": int(backup_manifest.get("asset_schema_version") or 0),
                "workbench_schema_version": int(
                    data_manifest.get("workbench_schema_version")
                    or backup_manifest.get("workbench_schema_version")
                    or 0
                ),
                "last_backup_id": backup_id,
                "last_successful_startup_at": _now(),
            }
        )
        _write_atomic(stage / MANIFEST_RELATIVE_PATH, _pretty_json(data_manifest))
        _write_atomic(stage / "manifest" / "restore_manifest.json", _pretty_json({"restore_id": stage.name, "backup_id": backup_id, "rollback_backup_id": rollback_backup_id, "created_at": _now()}))
        startup_resolver = HardwareDataRootResolver(
            self.resolver.application_root,
            environment={"HARDWARE_DATA_ROOT": str(stage)},
            bootstrap_path=stage.parent / f".{stage.name}.bootstrap.json",
            legacy_roots=(),
        )
        bootstrap = {
            "contract_version": "hardware-data-bootstrap/v1",
            "installation_id": current_manifest.get("installation_id"),
            "persistent_data_root": str(stage.resolve()),
            "data_layout_version": DATA_LAYOUT_VERSION,
            "created_at": _now(),
        }
        _write_atomic(startup_resolver.bootstrap_path, _pretty_json(bootstrap))
        try:
            migrated = HardwareStartupCoordinator(
                self.resolver.application_root,
                resolver=startup_resolver,
            ).run()
            if not migrated.get("ready"):
                raise HardwareAssetBackupError("RESTORE_CROSS_REFERENCE_INVALID")
        finally:
            try:
                startup_resolver.bootstrap_path.unlink()
            except FileNotFoundError:
                pass
        # Rebuildable plane is deliberately empty and excluded from backup.
        for relative in ("preview.db", "stage_cache", "consumption_projection.db", "temp"):
            target = stage / "rebuildable" / relative
            if target.exists():
                raise HardwareAssetBackupError("RESTORE_STAGING_FAILED")

    def _activate_restore(self, stage: Path, rollback: Path, operation_id: str, backup_id: str) -> None:
        marker_external = self.bootstrap_path.with_name(f".hardware-restore-{operation_id}.json")
        _write_atomic(
            marker_external,
            _pretty_json(
                {
                    "contract_version": "hardware-root-recovery/v1",
                    "operation_id": operation_id,
                    "operation": "RESTORE",
                    "state": "ACTIVATING",
                    "backup_id": backup_id,
                    "rollback_root": str(rollback),
                    "staging_root": str(stage),
                    "target_root": str(self.data_root),
                    "updated_at": _now(),
                }
            ),
        )
        try:
            gc.collect()
            os.replace(self.data_root, rollback)
            try:
                os.replace(stage, self.data_root)
            except OSError:
                try:
                    os.replace(rollback, self.data_root)
                    self._clear_root_marker()
                    marker_external.unlink(missing_ok=True)
                except OSError:
                    # Keep both recovery markers and roots for A7.
                    pass
                raise
            _write_atomic(
                self.bootstrap_path,
                _pretty_json(
                    {
                        "contract_version": "hardware-data-bootstrap/v1",
                        "installation_id": self._installation_id_from_root(self.data_root),
                        "persistent_data_root": str(self.data_root),
                        "data_layout_version": DATA_LAYOUT_VERSION,
                        "created_at": _now(),
                    }
                ),
            )
            marker_external.unlink(missing_ok=True)
        except OSError as error:
            raise HardwareAssetBackupError("RESTORE_ACTIVATION_FAILED") from error

    @staticmethod
    def _installation_id_from_root(root: Path) -> str:
        return str(_read_json(root / MANIFEST_RELATIVE_PATH, "RESTORE_PRECHECK_FAILED").get("installation_id") or "")


__all__ = [
    "BACKUP_CONTRACT_VERSION",
    "HardwareAssetBackupCoordinator",
    "HardwareAssetBackupError",
]
