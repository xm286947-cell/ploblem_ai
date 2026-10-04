"""Fail-closed startup orchestration for the Hardware durable data plane.

The resolver remains pure-read.  This coordinator is the only owner of
FIRST_INSTALL initialization, legacy-root activation, schema compatibility,
and the final READY decision.
"""
from __future__ import annotations

import hashlib
import gc
import json
import os
import shutil
import sqlite3
import sys
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from services.hardware_asset_legacy_migration import (
    LegacyAssetMigrationError,
    LegacyAssetMigrationRunner,
    LegacyMigrationPaths,
)
from services.hardware_asset_repository import (
    ASSET_SCHEMA_VERSION,
    CandidateAssetRepository,
)
from services.hardware_case_r1_workbench import HardwareR1WorkbenchStore
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_recovery_coordinator import (
    HardwareRecoveryCoordinator,
    HardwareRecoveryError,
)
from services.hardware_data_reliability import (
    CURRENT_SCHEMA_VERSION,
    HardwareDataReliabilityError,
    HardwareDataReliabilityManager,
)
from services.hardware_data_root import (
    BOOTSTRAP_CONTRACT_VERSION,
    BOOTSTRAP_FILENAME,
    DATA_LAYOUT_VERSION,
    MANIFEST_CONTRACT_VERSION,
    MANIFEST_RELATIVE_PATH,
    PERSISTENT_DATA_RELATIVE_PATHS,
    HardwareDataRootResolution,
    HardwareDataRootResolver,
)


HARDWARE_APP_VERSION = "2.1.0"
WORKBENCH_SCHEMA_VERSION = 1
ROOT_RECOVERY_MARKER = Path("manifest/recovery/current_operation.json")
ASSET_MIGRATION_MARKER = Path("manifest/hardware_asset_migration_recovery.json")
_DURABLE_DB_KEYS = ("hardware_db", "asset_db", "workbench_runtime_db")
_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}


class HardwareStartupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, value: Mapping[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid4().hex)
    payload = _json_bytes(value)
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            if path.exists():
                raise HardwareStartupError("HARDWARE_BOOTSTRAP_ALREADY_EXISTS")
            # A same-filesystem hard-link makes the verified temp visible
            # atomically without replacing a bootstrap created by another app.
            os.link(temp, path)
            temp.unlink()
        else:
            os.replace(temp, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    except HardwareStartupError:
        raise
    except OSError as error:
        raise HardwareStartupError("HARDWARE_STARTUP_METADATA_WRITE_FAILED") from error
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path, error_code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HardwareStartupError(error_code) from error
    if not isinstance(value, dict):
        raise HardwareStartupError(error_code)
    return value


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _db_integrity(path: Path, error_code: str) -> None:
    try:
        with closing(_connect_readonly(path)) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            if row is None or str(row[0]).lower() != "ok":
                raise HardwareStartupError(error_code)
    except HardwareStartupError:
        raise
    except sqlite3.Error as error:
        raise HardwareStartupError(error_code) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _StartupLock:
    """Non-blocking cross-platform lock held only during startup mutation/checks."""

    def __init__(self, path: Path):
        self.path = path
        self.stream: Any | None = None
        self.process_lock: threading.Lock | None = None
        self.process_lock_acquired = False

    def __enter__(self) -> "_StartupLock":
        try:
            key = str(self.path.resolve(strict=False))
            with _PROCESS_LOCK_GUARD:
                self.process_lock = _PROCESS_LOCKS.setdefault(key, threading.Lock())
            if not self.process_lock.acquire(blocking=False):
                raise HardwareStartupError("HARDWARE_STARTUP_LOCKED")
            self.process_lock_acquired = True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.path.open("a+b")
            self.stream.seek(0)
            if self.stream.read(1) == b"":
                self.stream.seek(0)
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except HardwareStartupError:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            if self.process_lock is not None and self.process_lock_acquired:
                self.process_lock.release()
                self.process_lock_acquired = False
            raise
        except (OSError, BlockingIOError) as error:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            if self.process_lock is not None and self.process_lock_acquired:
                self.process_lock.release()
                self.process_lock_acquired = False
            raise HardwareStartupError("HARDWARE_STARTUP_LOCKED") from error

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None
            if self.process_lock is not None and self.process_lock_acquired:
                self.process_lock.release()
                self.process_lock_acquired = False
            self.process_lock = None


class HardwareStartupCoordinator:
    """Classify, initialize/verify, and gate the Hardware business plane."""

    def __init__(
        self,
        application_root: str | Path,
        *,
        resolver: HardwareDataRootResolver | None = None,
        resolution: HardwareDataRootResolution | None = None,
    ) -> None:
        self.application_root = Path(application_root).expanduser().resolve(strict=False)
        self.resolver = resolver or HardwareDataRootResolver(self.application_root)
        self.resolution = resolution
        self._status: dict[str, Any] = {
            "status": "STARTING",
            "phase": "RESOLVE_DATA_ROOT",
            "installation_mode": None,
            "ready": False,
            "error_code": None,
            "data_root": None,
            "recovery_status": "NOT_STARTED",
            "active_root_operation": None,
            "pending_local_recovery_count": 0,
            "pending_remote_reconciliation_count": 0,
            "blocked_asset_count": 0,
            "last_recovery_error": None,
            "degraded": False,
        }

    def run(self) -> dict[str, Any]:
        resolution = self.resolution or self.resolver.resolve()
        self.resolution = resolution
        self._status.update(
            installation_mode=resolution.classification,
            data_root=str(resolution.data_root) if resolution.data_root else None,
            bootstrap_path=str(resolution.bootstrap_path),
        )
        if resolution.classification == "BLOCKED":
            marker_root = resolution.data_root
            if marker_root and (marker_root / ROOT_RECOVERY_MARKER).is_file():
                return self._blocked("HARDWARE_STARTUP_RECOVERY_REQUIRED", "CHECK_RECOVERY_MARKER")
            return self._blocked(resolution.error_code or "HARDWARE_STARTUP_BLOCKED", "CLASSIFY_INSTALLATION")

        lock_path = resolution.bootstrap_path.with_name(".hardware-startup.lock")
        try:
            with _StartupLock(lock_path):
                mode = resolution.classification
                if mode == "FIRST_INSTALL":
                    self._first_install(resolution)
                elif mode == "EXISTING_INSTALL":
                    self._existing_install(resolution)
                elif mode == "LEGACY_UPGRADE_REQUIRED":
                    self._legacy_upgrade(resolution)
                else:
                    raise HardwareStartupError("HARDWARE_STARTUP_BLOCKED")
        except (
            HardwareStartupError,
            HardwareDataReliabilityError,
            LegacyAssetMigrationError,
            HardwareRecoveryError,
        ) as error:
            code = str(getattr(error, "code", None) or "HARDWARE_STARTUP_BLOCKED")
            return self._blocked(code, str(self._status.get("phase") or "STARTUP"))
        except (OSError, sqlite3.Error, ValueError) as error:
            code = str(getattr(error, "code", None) or "HARDWARE_STARTUP_BLOCKED")
            return self._blocked(code, str(self._status.get("phase") or "STARTUP"))
        self._status.update(status="READY", phase="READY", ready=True, error_code=None)
        return dict(self._status)

    def _phase(self, phase: str) -> None:
        self._status.update(status="STARTING", phase=phase, ready=False, error_code=None)

    def _blocked(self, code: str, phase: str) -> dict[str, Any]:
        self._status.update(status="BLOCKED", phase=phase, ready=False, error_code=code)
        return dict(self._status)

    @staticmethod
    def _layout(root: Path) -> dict[str, Path]:
        return {key: root / relative for key, relative in PERSISTENT_DATA_RELATIVE_PATHS.items()}

    @staticmethod
    def _manifest_payload(
        installation_id: str,
        *,
        hardware_schema: int,
        asset_schema: int,
        workbench_schema: int,
        migration_id: str | None = None,
        backup_id: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        return {
            "contract_version": MANIFEST_CONTRACT_VERSION,
            "installation_id": installation_id,
            "data_layout_version": DATA_LAYOUT_VERSION,
            "hardware_schema_version": hardware_schema,
            "asset_schema_version": asset_schema,
            "workbench_schema_version": workbench_schema,
            "relative_paths": dict(PERSISTENT_DATA_RELATIVE_PATHS),
            "initialized_at": now,
            "last_successful_app_version": HARDWARE_APP_VERSION,
            "last_successful_startup_at": now,
            "last_migration_id": migration_id,
            "last_backup_id": backup_id,
        }

    @staticmethod
    def _bootstrap_payload(installation_id: str, data_root: Path) -> dict[str, Any]:
        return {
            "contract_version": BOOTSTRAP_CONTRACT_VERSION,
            "installation_id": installation_id,
            "persistent_data_root": str(data_root.resolve()),
            "data_layout_version": DATA_LAYOUT_VERSION,
            "created_at": _utc_now(),
        }

    def _write_marker(self, root: Path, *, operation_id: str, operation: str, state: str) -> None:
        marker = {
            "contract_version": "hardware-root-recovery/v1",
            "operation_id": operation_id,
            "operation": operation,
            "state": state,
            "installation_id": self._status.get("installation_id"),
            "target_root": self._status.get("data_root"),
            "updated_at": _utc_now(),
        }
        _atomic_write(root / ROOT_RECOVERY_MARKER, marker)

    @staticmethod
    def _clear_marker(root: Path) -> None:
        try:
            (root / ROOT_RECOVERY_MARKER).unlink()
        except FileNotFoundError:
            return
        try:
            descriptor = os.open(root / "manifest" / "recovery", os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass

    def _first_install(self, resolution: HardwareDataRootResolution) -> None:
        self._phase("FIRST_INSTALL")
        target = resolution.data_root
        if target is None:
            raise HardwareStartupError("PERSISTENT_DATA_ROOT_UNAVAILABLE")
        target = target.resolve(strict=False)
        if target.exists() and any(target.iterdir()):
            raise HardwareStartupError("UNMANAGED_DATA_ROOT_DETECTED")
        self._reject_orphan_initializing(target)
        operation_id = uuid4().hex
        staging = target.parent / f".initializing-{operation_id}"
        installation_id = uuid4().hex
        self._status.update(installation_id=installation_id, data_root=str(target))
        try:
            staging.mkdir(parents=True, exist_ok=False)
            paths = self._layout(staging)
            for key, path in paths.items():
                if key in _DURABLE_DB_KEYS:
                    path.parent.mkdir(parents=True, exist_ok=True)
                elif key != "sources":
                    path.mkdir(parents=True, exist_ok=True)
            paths["sources"].mkdir(parents=True, exist_ok=True)
            paths["rebuildable"].mkdir(parents=True, exist_ok=True)
            self._write_marker(staging, operation_id=operation_id, operation="FIRST_INSTALL", state="INITIALIZING")

            self._phase("INITIALIZE_HARDWARE_DB")
            hardware_status = HardwareDataReliabilityManager(
                paths["hardware_db"], backup_root=paths["backups"]
            ).ensure_ready()
            self._phase("INITIALIZE_ASSET_DB")
            CandidateAssetRepository(paths["asset_db"]).initialize()
            self._phase("INITIALIZE_WORKBENCH_RUNTIME_DB")
            HardwareR1WorkbenchStore(paths["workbench_runtime_db"])
            self._phase("INITIALIZE_SOURCE_ROOT")
            HardwareCaseSourceStore(paths["hardware_db"], paths["sources"], initialize_schema=False)

            self._status.update(
                hardware_schema_version=CURRENT_SCHEMA_VERSION,
                asset_schema_version=ASSET_SCHEMA_VERSION,
                workbench_schema_version=WORKBENCH_SCHEMA_VERSION,
            )
            manifest = self._manifest_payload(
                installation_id,
                hardware_schema=CURRENT_SCHEMA_VERSION,
                asset_schema=ASSET_SCHEMA_VERSION,
                workbench_schema=WORKBENCH_SCHEMA_VERSION,
                backup_id=hardware_status.get("backup_id"),
            )
            _atomic_write(staging / MANIFEST_RELATIVE_PATH, manifest)
            self._verify_data_plane(staging, manifest, allow_no_manifest_source=True)
            self._write_marker(staging, operation_id=operation_id, operation="FIRST_INSTALL", state="VERIFIED")
            self._phase("ACTIVATE_DATA_ROOT")
            target.parent.mkdir(parents=True, exist_ok=True)
            # SQLite connection finalizers can lag behind the end of their
            # initializer scope on Windows; release unreferenced handles before
            # atomically activating the directory tree.
            gc.collect()
            if target.exists():
                quarantine = target.with_name(target.name + ".empty-quarantine-" + operation_id)
                os.replace(target, quarantine)
            os.replace(staging, target)
            self._write_bootstrap_exclusive(resolution.bootstrap_path, self._bootstrap_payload(installation_id, target))
            self._clear_marker(target)
            self._status.update(
                data_root=str(target),
                installation_id=installation_id,
                hardware_schema_version=CURRENT_SCHEMA_VERSION,
                asset_schema_version=ASSET_SCHEMA_VERSION,
                workbench_schema_version=WORKBENCH_SCHEMA_VERSION,
            )
            self._existing_install(
                HardwareDataRootResolution(
                    "EXISTING_INSTALL", target, resolution.bootstrap_path,
                    manifest_path=target / MANIFEST_RELATIVE_PATH,
                ),
                first_install_activation=True,
            )
        except Exception:
            # Staging and markers are retained for the A7 RecoveryCoordinator;
            # startup must never silently discard an interrupted root operation.
            raise

    def _write_bootstrap_exclusive(self, path: Path, payload: Mapping[str, Any]) -> None:
        _atomic_write(path, payload, exclusive=True)

    @staticmethod
    def _reject_orphan_initializing(target: Path) -> None:
        parent = target.parent
        if not parent.exists():
            return
        for candidate in parent.glob(".initializing-*"):
            marker_path = candidate / ROOT_RECOVERY_MARKER
            if not marker_path.is_file():
                continue
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(marker, dict) and Path(str(marker.get("target_root") or "")).resolve(strict=False) == target.resolve(strict=False):
                raise HardwareStartupError("HARDWARE_STARTUP_RECOVERY_REQUIRED")

    def _existing_install(
        self,
        resolution: HardwareDataRootResolution,
        *,
        first_install_activation: bool = False,
    ) -> None:
        self._phase("VERIFY_MANIFEST")
        root = resolution.data_root
        if root is None:
            raise HardwareStartupError("HARDWARE_DATA_ROOT_MISSING")
        root = root.resolve(strict=False)
        manifest_path = root / MANIFEST_RELATIVE_PATH
        manifest = _read_json(manifest_path, "HARDWARE_DATA_MANIFEST_INVALID")
        bootstrap = _read_json(resolution.bootstrap_path, "HARDWARE_BOOTSTRAP_INVALID")
        if bootstrap.get("contract_version") != BOOTSTRAP_CONTRACT_VERSION or manifest.get("contract_version") != MANIFEST_CONTRACT_VERSION:
            raise HardwareStartupError("HARDWARE_DATA_MANIFEST_CONTRACT_UNSUPPORTED")
        installation_id = str(bootstrap.get("installation_id") or "")
        if not installation_id or manifest.get("installation_id") != installation_id:
            raise HardwareStartupError("INSTALLATION_ID_MISMATCH")
        if Path(str(bootstrap.get("persistent_data_root") or "")).resolve(strict=False) != root:
            raise HardwareStartupError("HARDWARE_DATA_ROOT_CONFIG_CONFLICT")
        self._status.update(installation_id=installation_id, data_root=str(root))

        self._phase("CHECK_RECOVERY_MARKER")
        if (root / ROOT_RECOVERY_MARKER).exists() and not first_install_activation:
            try:
                marker = json.loads((root / ROOT_RECOVERY_MARKER).read_text(encoding="utf-8"))
                if isinstance(marker, dict):
                    self._status["active_root_operation"] = str(
                        marker.get("operation") or "UNKNOWN"
                    )
            except (OSError, ValueError):
                self._status["active_root_operation"] = "UNKNOWN"
            self._status["recovery_status"] = "BLOCKED"
            raise HardwareStartupError("HARDWARE_STARTUP_RECOVERY_REQUIRED")
        self._check_asset_migration_marker(root)
        hardware_path, asset_path, workbench_path, source_root = self._required_durable_paths(root)
        self._phase("VERIFY_LAYOUT")
        self._verify_layout_paths(root, manifest)
        for path, code in (
            (hardware_path, "HARDWARE_DB_MISSING"),
            (asset_path, "HARDWARE_ASSET_DB_MISSING"),
            (workbench_path, "WORKBENCH_RUNTIME_DB_MISSING"),
        ):
            if not path.is_file() or path.stat().st_size == 0:
                raise HardwareStartupError(code)

        self._phase("VERIFY_SCHEMA_COMPATIBILITY")
        manifest_hw = manifest.get("hardware_schema_version")
        manifest_asset = manifest.get("asset_schema_version")
        manifest_workbench = manifest.get("workbench_schema_version")
        if isinstance(manifest_hw, int) and not isinstance(manifest_hw, bool) and manifest_hw > CURRENT_SCHEMA_VERSION:
            raise HardwareStartupError("HARDWARE_SCHEMA_TOO_NEW")
        if isinstance(manifest_asset, int) and not isinstance(manifest_asset, bool) and manifest_asset > ASSET_SCHEMA_VERSION:
            raise HardwareStartupError("ASSET_SCHEMA_TOO_NEW")
        if isinstance(manifest_workbench, int) and not isinstance(manifest_workbench, bool) and manifest_workbench > WORKBENCH_SCHEMA_VERSION:
            raise HardwareStartupError("WORKBENCH_SCHEMA_TOO_NEW")
        last_backup_id = manifest.get("last_backup_id")
        hardware_version, migration_backup = self._ensure_hardware_schema(hardware_path, root)
        if migration_backup:
            last_backup_id = migration_backup
        asset_version, asset_migration, asset_backup = self._ensure_asset_schema(asset_path, root)
        if asset_backup:
            last_backup_id = asset_backup
        workbench_version = self._verify_workbench_schema(workbench_path, manifest)
        hardware_migrated = bool(migration_backup or self._status.get("migration_id"))
        if manifest_hw != hardware_version and not hardware_migrated:
            raise HardwareStartupError("HARDWARE_DATA_MANIFEST_SCHEMA_MISMATCH")
        if manifest_asset != asset_version and not asset_migration:
            raise HardwareStartupError("HARDWARE_DATA_MANIFEST_SCHEMA_MISMATCH")
        if manifest_workbench != workbench_version:
            raise HardwareStartupError("HARDWARE_DATA_MANIFEST_SCHEMA_MISMATCH")
        self._status.update(
            hardware_schema_version=hardware_version,
            asset_schema_version=asset_version,
            workbench_schema_version=workbench_version,
        )

        self._phase("RECOVER_LOCAL_OPERATIONS")
        recovery = HardwareRecoveryCoordinator(
            hardware_db=hardware_path,
            asset_db=asset_path,
            source_root=source_root,
        ).recover()
        self._status.update(recovery)
        self._status["degraded"] = recovery["recovery_status"] == "DEGRADED"

        # A schema mutation is committed to the manifest before its root
        # operation marker is cleared. A crash cannot leave migrated DBs paired
        # with stale metadata and silently proceed on the next launch.
        if migration_backup or asset_migration or self._status.get("migration_id"):
            manifest.update(
                hardware_schema_version=hardware_version,
                asset_schema_version=asset_version,
                workbench_schema_version=workbench_version,
                last_migration_id=asset_migration or self._status.get("migration_id") or manifest.get("last_migration_id"),
                last_backup_id=last_backup_id,
            )
            _atomic_write(manifest_path, manifest)
            self._clear_marker(root)

        self._phase("VERIFY_SOURCE")
        _db_integrity(hardware_path, "HARDWARE_DB_INTEGRITY_FAILED")
        _db_integrity(asset_path, "HARDWARE_ASSET_DB_INTEGRITY_FAILED")
        _db_integrity(workbench_path, "WORKBENCH_RUNTIME_DB_INTEGRITY_FAILED")
        self._verify_active_sources(hardware_path, source_root)
        self._phase("VERIFY_CROSS_REFERENCE")
        self._verify_cross_references(hardware_path, asset_path, workbench_path)

        self._phase("REBUILDABLE_PLANE")
        rebuildable = root / PERSISTENT_DATA_RELATIVE_PATHS["rebuildable"]
        rebuildable.mkdir(parents=True, exist_ok=True)
        self._quarantine_corrupt_preview(rebuildable / "preview.db")

        self._phase("UPDATE_STARTUP_METADATA")
        manifest.update(
            last_successful_app_version=HARDWARE_APP_VERSION,
            last_successful_startup_at=_utc_now(),
            hardware_schema_version=hardware_version,
            asset_schema_version=asset_version,
            workbench_schema_version=workbench_version,
            last_migration_id=asset_migration or manifest.get("last_migration_id"),
            last_backup_id=last_backup_id,
        )
        _atomic_write(manifest_path, manifest)
        self._clear_marker(root)
        self._status.update(
            installation_id=installation_id,
            data_root=str(root),
            hardware_schema_version=hardware_version,
            asset_schema_version=asset_version,
            workbench_schema_version=workbench_version,
            migration_id=asset_migration,
            backup_id=last_backup_id,
        )

    def _verify_layout_paths(self, root: Path, manifest: Mapping[str, Any]) -> None:
        expected = dict(PERSISTENT_DATA_RELATIVE_PATHS)
        actual = manifest.get("relative_paths")
        if actual != expected:
            raise HardwareStartupError("HARDWARE_DATA_LAYOUT_MISMATCH")
        if manifest.get("data_layout_version") != DATA_LAYOUT_VERSION:
            raise HardwareStartupError("HARDWARE_DATA_LAYOUT_UNSUPPORTED")
        if not (root / "db").is_dir() or not (root / "sources").is_dir():
            raise HardwareStartupError("HARDWARE_DATA_LAYOUT_INCOMPLETE")

    @staticmethod
    def _required_durable_paths(root: Path) -> tuple[Path, Path, Path, Path]:
        paths = HardwareStartupCoordinator._layout(root)
        return (
            paths["hardware_db"],
            paths["asset_db"],
            paths["workbench_runtime_db"],
            paths["sources"],
        )

    def _check_asset_migration_marker(self, root: Path) -> None:
        marker_path = root / ASSET_MIGRATION_MARKER
        if not marker_path.exists():
            return
        marker = _read_json(marker_path, "ASSET_MIGRATION_RECOVERY_REQUIRED")
        if str(marker.get("state") or "").upper() != "COMPLETED":
            raise HardwareStartupError("ASSET_MIGRATION_RECOVERY_REQUIRED")

    def _ensure_hardware_schema(self, path: Path, root: Path) -> tuple[int, str | None]:
        version: int | None = None
        try:
            with closing(_connect_readonly(path)) as connection:
                tables = _table_names(connection)
                if "hardware_schema_version" in tables:
                    row = connection.execute(
                        "SELECT schema_version FROM hardware_schema_version WHERE singleton=1"
                    ).fetchone()
                    if row is not None:
                        version = int(row[0])
                elif not tables:
                    raise HardwareStartupError("HARDWARE_DB_UNINITIALIZED")
        except HardwareStartupError:
            raise
        except sqlite3.Error as error:
            raise HardwareStartupError("HARDWARE_DB_INTEGRITY_FAILED") from error
        if version is not None and version > CURRENT_SCHEMA_VERSION:
            raise HardwareStartupError("HARDWARE_SCHEMA_TOO_NEW")
        if version == CURRENT_SCHEMA_VERSION:
            status = HardwareDataReliabilityManager(path, backup_root=root / "backups").inspect_status()
            if status.get("status") != "READY":
                raise HardwareStartupError(str(status.get("error_code") or "HARDWARE_SCHEMA_INVALID"))
            return version, None
        self._phase("MIGRATE_HARDWARE_DB")
        operation_id = uuid4().hex
        self._write_marker(root, operation_id=operation_id, operation="MIGRATION", state="IN_PROGRESS")
        try:
            status = HardwareDataReliabilityManager(path, backup_root=root / "backups").ensure_ready()
            migrated = int(status.get("schema_version") or CURRENT_SCHEMA_VERSION)
            if migrated != CURRENT_SCHEMA_VERSION:
                raise HardwareStartupError("HARDWARE_SCHEMA_MIGRATION_FAILED")
            self._status.update(migration_id=status.get("migration_id"), backup_id=status.get("backup_id"))
            return migrated, status.get("backup_id")
        except Exception:
            # The marker remains for A7 to classify/recover a partial migration.
            raise

    def _ensure_asset_schema(self, path: Path, root: Path) -> tuple[int, str | None, str | None]:
        version: int | None = None
        try:
            with closing(_connect_readonly(path)) as connection:
                tables = _table_names(connection)
                if "hardware_asset_schema_version" in tables:
                    row = connection.execute(
                        "SELECT schema_version FROM hardware_asset_schema_version WHERE singleton=1"
                    ).fetchone()
                    if row is not None:
                        version = int(row[0])
                elif not tables:
                    raise HardwareStartupError("ASSET_SCHEMA_VERSION_MISSING")
        except HardwareStartupError:
            raise
        except sqlite3.Error as error:
            raise HardwareStartupError("HARDWARE_ASSET_DB_INTEGRITY_FAILED") from error
        if version is None:
            raise HardwareStartupError("ASSET_SCHEMA_VERSION_MISSING")
        if version > ASSET_SCHEMA_VERSION:
            raise HardwareStartupError("ASSET_SCHEMA_TOO_NEW")
        if version == ASSET_SCHEMA_VERSION:
            try:
                actual = CandidateAssetRepository(path).schema_version()
            except Exception as error:
                raise HardwareStartupError("ASSET_SCHEMA_INVALID") from error
            if actual != ASSET_SCHEMA_VERSION:
                raise HardwareStartupError("ASSET_SCHEMA_INVALID")
            return version, None, None

        self._phase("MIGRATE_ASSET_DB")
        operation_id = uuid4().hex
        self._write_marker(root, operation_id=operation_id, operation="MIGRATION", state="IN_PROGRESS")
        backup = HardwareDataReliabilityManager(
            path, backup_root=root / "backups" / "pre-asset-schema-migration"
        ).create_backup(
            reason="PRE_ASSET_SCHEMA_MIGRATION",
            source_schema=version,
            target_schema=ASSET_SCHEMA_VERSION,
        )
        try:
            CandidateAssetRepository(path).initialize()
            actual = CandidateAssetRepository(path).schema_version()
            if actual != ASSET_SCHEMA_VERSION:
                raise HardwareStartupError("ASSET_SCHEMA_MIGRATION_FAILED")
            return actual, "HARDWARE-ASSET-SCHEMA-MIGRATION", str(backup.get("backup_id"))
        except Exception:
            raise

    @staticmethod
    def _verify_workbench_schema(path: Path, manifest: Mapping[str, Any]) -> int:
        recorded = manifest.get("workbench_schema_version")
        if not isinstance(recorded, int) or isinstance(recorded, bool):
            raise HardwareStartupError("WORKBENCH_SCHEMA_VERSION_MISSING")
        if recorded > WORKBENCH_SCHEMA_VERSION:
            raise HardwareStartupError("WORKBENCH_SCHEMA_TOO_NEW")
        if recorded < WORKBENCH_SCHEMA_VERSION:
            raise HardwareStartupError("WORKBENCH_MIGRATION_REQUIRED")
        try:
            with closing(_connect_readonly(path)) as connection:
                tables = _table_names(connection)
                required = {"hardware_r1_batch", "hardware_r1_batch_item"}
                if not required.issubset(tables):
                    raise HardwareStartupError("WORKBENCH_SCHEMA_INVALID")
                columns = _table_columns(connection, "hardware_r1_batch_item")
                if not {"item_id", "batch_id", "business_case_id", "source_id", "result_json"}.issubset(columns):
                    raise HardwareStartupError("WORKBENCH_SCHEMA_INVALID")
        except HardwareStartupError:
            raise
        except sqlite3.Error as error:
            raise HardwareStartupError("WORKBENCH_RUNTIME_DB_INTEGRITY_FAILED") from error
        return recorded

    def _verify_active_sources(self, hardware_db: Path, source_root: Path) -> None:
        self._phase("VERIFY_SOURCE")
        try:
            with closing(_connect_readonly(hardware_db)) as connection:
                tables = _table_names(connection)
                required = {
                    "hardware_case_source_registry",
                    "hardware_r1_source_binding",
                }
                if not required.issubset(tables):
                    raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
                rows = connection.execute(
                    """
                    SELECT b.business_case_id,b.source_ref,b.source_id AS binding_source_id,
                           b.source_status AS binding_status,r.source_id AS registry_source_id,
                           r.relative_path,r.sha256,r.source_status AS registry_status
                    FROM hardware_r1_source_binding b
                    LEFT JOIN hardware_case_source_registry r ON r.source_ref=b.source_ref
                    WHERE b.source_status='ACTIVE'
                    ORDER BY b.business_case_id
                    """
                ).fetchall()
                registry_rows = connection.execute(
                    "SELECT source_ref,source_id,relative_path,sha256,source_status "
                    "FROM hardware_case_source_registry WHERE source_status='AVAILABLE' ORDER BY source_ref"
                ).fetchall()
        except HardwareStartupError:
            raise
        except sqlite3.Error as error:
            raise HardwareStartupError("SOURCE_INTEGRITY_FAILED") from error
        source_root = source_root.resolve(strict=False)
        records: dict[str, tuple[str, str, str]] = {}
        for row in registry_rows:
            records[str(row["source_ref"])] = (
                str(row["source_id"] or "").lower(),
                str(row["relative_path"] or ""),
                str(row["sha256"] or "").lower(),
            )
        for row in rows:
            source_ref = str(row["source_ref"] or "")
            if (
                not source_ref
                or str(row["binding_source_id"] or "").lower() != str(row["registry_source_id"] or "").lower()
                or row["registry_status"] != "AVAILABLE"
                or source_ref not in records
            ):
                raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
        for source_ref, (source_id, relative_path, expected_hash) in records.items():
            if not source_id or len(source_id) != 64 or expected_hash != source_id:
                raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
            pure = PurePosixPath(relative_path.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts:
                raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
            path = (source_root / Path(*pure.parts)).resolve(strict=False)
            try:
                path.relative_to(source_root)
            except ValueError as error:
                raise HardwareStartupError("SOURCE_INTEGRITY_FAILED") from error
            if not path.is_file():
                raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
            try:
                if _sha256(path) != expected_hash:
                    raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
            except OSError as error:
                raise HardwareStartupError("SOURCE_INTEGRITY_FAILED") from error

    @staticmethod
    def _verify_cross_references(hardware_db: Path, asset_db: Path, workbench_db: Path) -> None:
        try:
            with closing(_connect_readonly(asset_db)) as assets, closing(_connect_readonly(hardware_db)) as hardware:
                candidate_rows = assets.execute(
                    "SELECT candidate_id,business_case_id,source_id,source_ref,candidate_hash,asset_status "
                    "FROM hardware_candidate_asset WHERE asset_status='ACTIVE'"
                ).fetchall()
                for row in candidate_rows:
                    binding = hardware.execute(
                        """
                        SELECT b.source_ref,b.source_id,b.source_status,r.source_id AS registry_source_id,
                               r.sha256,r.source_status AS registry_status
                        FROM hardware_r1_source_binding b
                        JOIN hardware_case_source_registry r ON r.source_ref=b.source_ref
                        WHERE b.business_case_id=?
                        """,
                        (row["business_case_id"],),
                    ).fetchone()
                    if (
                        binding is None
                        or binding["source_status"] != "ACTIVE"
                        or binding["registry_status"] != "AVAILABLE"
                        or str(binding["source_id"] or "").lower() != str(row["source_id"] or "").lower()
                        or str(binding["registry_source_id"] or "").lower() != str(row["source_id"] or "").lower()
                        or str(binding["source_ref"] or "") != str(row["source_ref"] or "")
                        or str(binding["sha256"] or "").lower() != str(row["source_id"] or "").lower()
                    ):
                        raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")

                tables = _table_names(assets)
                if "hardware_candidate_event" in tables:
                    events = assets.execute(
                        """
                        SELECT e.candidate_id,e.new_candidate_hash,c.candidate_hash
                        FROM hardware_candidate_event e
                        LEFT JOIN hardware_candidate_asset c ON c.candidate_id=e.candidate_id
                        WHERE e.event_type='PROMOTION_STARTED'
                        """
                    ).fetchall()
                    for event in events:
                        if event["candidate_hash"] is None or str(event["new_candidate_hash"] or "") != str(event["candidate_hash"]):
                            raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")

                promotions = assets.execute(
                    """
                    SELECT p.asset_candidate_id,p.business_case_id,p.source_id,p.knowledge_id,
                           p.promotion_status,c.candidate_hash,c.business_case_id AS candidate_case,
                           c.source_id AS candidate_source
                    FROM hardware_asset_promotion p
                    LEFT JOIN hardware_candidate_asset c ON c.candidate_id=p.asset_candidate_id
                    """
                ).fetchall()
                for promotion in promotions:
                    if (
                        promotion["candidate_hash"] is None
                        or str(promotion["candidate_case"] or "") != str(promotion["business_case_id"] or "")
                        or str(promotion["candidate_source"] or "").lower() != str(promotion["source_id"] or "").lower()
                    ):
                        raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")
                    if str(promotion["promotion_status"] or "").upper() in {
                        "PUBLISHED_PENDING_QUERY_BACK", "VERIFIED"
                    }:
                        knowledge_id = str(promotion["knowledge_id"] or "").strip()
                        if not knowledge_id:
                            raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")
                        formal = hardware.execute(
                            """
                            SELECT 1 FROM hardware_r1_source_formal_reference
                            WHERE business_case_id=? AND source_id=? AND knowledge_id=?
                            """,
                            (promotion["business_case_id"], promotion["source_id"], knowledge_id),
                        ).fetchone()
                        if formal is None:
                            raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")

        except HardwareStartupError:
            raise
        except sqlite3.Error as error:
            raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID") from error

        # Operational Workbench promotion rows are checked against the durable
        # Candidate repository too; the Workbench remains the audit history.
        try:
            with closing(_connect_readonly(workbench_db)) as workbench, closing(_connect_readonly(asset_db)) as assets, closing(_connect_readonly(hardware_db)) as hardware:
                tables = _table_names(workbench)
                if "hardware_r1_knowledge_promotion" not in tables:
                    return
                required = {"candidate_id", "golden_hash", "business_case_id", "source_id", "status", "knowledge_id"}
                if not required.issubset(_table_columns(workbench, "hardware_r1_knowledge_promotion")):
                    raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")
                for row in workbench.execute(
                    "SELECT candidate_id,golden_hash,business_case_id,source_id,status,knowledge_id "
                    "FROM hardware_r1_knowledge_promotion"
                ).fetchall():
                    candidate = assets.execute(
                        "SELECT candidate_hash,business_case_id,source_id FROM hardware_candidate_asset "
                        "WHERE business_case_id=? AND source_id=?",
                        (row["business_case_id"], row["source_id"]),
                    ).fetchone()
                    if candidate is None or (
                        str(candidate["candidate_hash"] or "") != str(row["golden_hash"] or "")
                        or str(candidate["business_case_id"] or "") != str(row["business_case_id"] or "")
                        or str(candidate["source_id"] or "").lower() != str(row["source_id"] or "").lower()
                    ):
                        raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")
                    if str(row["status"] or "").upper() in {"PUBLISHED_PENDING_QUERY_BACK", "VERIFIED"}:
                        knowledge_id = str(row["knowledge_id"] or "").strip()
                        formal = hardware.execute(
                            "SELECT 1 FROM hardware_r1_source_formal_reference "
                            "WHERE business_case_id=? AND source_id=? AND knowledge_id=?",
                            (row["business_case_id"], row["source_id"], knowledge_id),
                        ).fetchone() if knowledge_id else None
                        if formal is None:
                            raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID")
        except HardwareStartupError:
            raise
        except sqlite3.Error as error:
            raise HardwareStartupError("HARDWARE_ASSET_CROSS_REFERENCE_INVALID") from error

    @staticmethod
    def _quarantine_corrupt_preview(path: Path) -> None:
        artifacts = [
            Path(str(path) + suffix)
            for suffix in ("", "-wal", "-shm", "-journal")
        ]
        if not any(item.exists() for item in artifacts):
            return
        valid = False
        if path.is_file():
            try:
                _db_integrity(path, "REBUILDABLE_PREVIEW_INVALID")
                valid = True
            except HardwareStartupError:
                pass
        if valid:
            return
        quarantine = path.with_name(path.name + ".quarantine-" + uuid4().hex)
        try:
            for artifact in artifacts:
                if artifact.exists():
                    os.replace(artifact, quarantine.with_name(quarantine.name + artifact.name[len(path.name):]))
        except OSError as error:
            raise HardwareStartupError("REBUILDABLE_PLANE_RECOVERY_FAILED") from error

    def _legacy_upgrade(self, resolution: HardwareDataRootResolution) -> None:
        self._phase("LEGACY_UPGRADE_PREFLIGHT")
        legacy_root = resolution.data_root
        if legacy_root is None:
            raise HardwareStartupError("LEGACY_LAYOUT_NOT_FOUND")
        configured = str(self.resolver.environment.get("HARDWARE_DATA_ROOT") or "").strip()
        target = Path(configured).expanduser().resolve(strict=False) if configured else self.resolver.default_data_root
        if target == legacy_root.resolve(strict=False):
            raise HardwareStartupError("HARDWARE_DATA_ROOT_CONFIG_CONFLICT")
        if self.resolver._invalid_root_location(target) or self.resolver._invalid_bootstrap_location(target):
            raise HardwareStartupError("PERSISTENT_DATA_ROOT_OVERLAPS_APPLICATION_ROOT")
        if target.exists() and any(target.iterdir()):
            raise HardwareStartupError("UNMANAGED_DATA_ROOT_DETECTED")
        self._reject_orphan_initializing(target)
        old_hardware = legacy_root / "hardware_case_mvp.db"
        old_workbench = legacy_root / "hardware_case_mvp_r1_workbench.db"
        if not old_hardware.is_file() or not old_workbench.is_file():
            raise HardwareStartupError("LEGACY_LAYOUT_NOT_FOUND")
        old_sources = next(
            (legacy_root / name for name in ("hardware_case_mvp_sources", "hardware_case_sources") if (legacy_root / name).is_dir()),
            legacy_root / "hardware_case_mvp_sources",
        )
        operation_id = uuid4().hex
        installation_id = uuid4().hex
        staging = target.parent / f".initializing-{operation_id}"
        self._status.update(installation_id=installation_id, data_root=str(target))
        staging.mkdir(parents=True, exist_ok=False)
        try:
            paths = self._layout(staging)
            for key, path in paths.items():
                if key in _DURABLE_DB_KEYS:
                    path.parent.mkdir(parents=True, exist_ok=True)
                elif key != "sources":
                    path.mkdir(parents=True, exist_ok=True)
            paths["sources"].mkdir(parents=True, exist_ok=True)
            paths["rebuildable"].mkdir(parents=True, exist_ok=True)
            self._write_marker(staging, operation_id=operation_id, operation="MIGRATION", state="IN_PROGRESS")
            self._phase("COPY_LEGACY_DURABLE_DATA")
            self._sqlite_backup_copy(old_hardware, paths["hardware_db"])
            self._sqlite_backup_copy(old_workbench, paths["workbench_runtime_db"])
            if old_sources.is_dir():
                shutil.copytree(old_sources, paths["sources"], dirs_exist_ok=True, symlinks=True)

            self._phase("MIGRATE_HARDWARE_DB")
            hardware_status = HardwareDataReliabilityManager(
                paths["hardware_db"], backup_root=paths["backups"] / "pre-upgrade-hardware"
            ).ensure_ready()
            if not self._workbench_has_required_schema(paths["workbench_runtime_db"]):
                raise HardwareStartupError("LEGACY_WORKBENCH_DB_INVALID")

            self._phase("MIGRATE_ASSET_DB")
            migration_paths = LegacyMigrationPaths(
                hardware_db=old_hardware,
                workbench_db=old_workbench,
                source_root=old_sources,
                target_data_root=staging,
                preview_db=legacy_root / "hardware_case_mvp_r1_preview.db",
            )
            migration = LegacyAssetMigrationRunner(migration_paths).run()
            asset_schema = CandidateAssetRepository(paths["asset_db"]).schema_version()
            manifest = self._manifest_payload(
                installation_id,
                hardware_schema=int(hardware_status.get("schema_version") or CURRENT_SCHEMA_VERSION),
                asset_schema=int(asset_schema or ASSET_SCHEMA_VERSION),
                workbench_schema=WORKBENCH_SCHEMA_VERSION,
                migration_id=str(migration.get("migration_id") or ""),
                backup_id=hardware_status.get("backup_id"),
            )
            _atomic_write(staging / MANIFEST_RELATIVE_PATH, manifest)
            self._phase("VERIFY_SOURCE")
            self._verify_data_plane(staging, manifest)
            self._write_marker(staging, operation_id=operation_id, operation="MIGRATION", state="VERIFIED")
            self._phase("ACTIVATE_DATA_ROOT")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.replace(target, target.with_name(target.name + ".empty-quarantine-" + operation_id))
            os.replace(staging, target)
            self._write_bootstrap_exclusive(resolution.bootstrap_path, self._bootstrap_payload(installation_id, target))
            self._clear_marker(target)
            self._status.update(migration_id=migration.get("migration_id"), backup_id=manifest.get("last_backup_id"))
            self._existing_install(
                HardwareDataRootResolution("EXISTING_INSTALL", target, resolution.bootstrap_path, manifest_path=target / MANIFEST_RELATIVE_PATH),
                first_install_activation=True,
            )
        except Exception:
            # Keep legacy originals and staging intact; A7 owns recovery.
            raise

    @staticmethod
    def _sqlite_backup_copy(source_path: Path, target_path: Path) -> None:
        try:
            source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            target = sqlite3.connect(target_path, timeout=10)
            try:
                source.backup(target)
                integrity = target.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or str(integrity[0]).lower() != "ok":
                    raise HardwareStartupError("LEGACY_DATABASE_COPY_FAILED")
            finally:
                target.close()
                source.close()
        except HardwareStartupError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise HardwareStartupError("LEGACY_DATABASE_COPY_FAILED") from error

    @staticmethod
    def _workbench_has_required_schema(path: Path) -> bool:
        try:
            with closing(_connect_readonly(path)) as connection:
                tables = _table_names(connection)
                return {"hardware_r1_batch", "hardware_r1_batch_item"}.issubset(tables)
        except sqlite3.Error:
            return False

    def _verify_data_plane(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        *,
        allow_no_manifest_source: bool = False,
    ) -> None:
        hardware, asset, workbench, source_root = self._required_durable_paths(root)
        for path, code in (
            (hardware, "HARDWARE_DB_MISSING"),
            (asset, "HARDWARE_ASSET_DB_MISSING"),
            (workbench, "WORKBENCH_RUNTIME_DB_MISSING"),
        ):
            if not path.is_file() or path.stat().st_size == 0:
                raise HardwareStartupError(code)
            _db_integrity(path, "DURABLE_DB_INTEGRITY_FAILED")
        if not source_root.is_dir():
            raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
        self._verify_layout_paths(root, manifest)
        versions = (
            int(manifest.get("hardware_schema_version") or 0),
            int(manifest.get("asset_schema_version") or 0),
            int(manifest.get("workbench_schema_version") or 0),
        )
        if versions != (CURRENT_SCHEMA_VERSION, ASSET_SCHEMA_VERSION, WORKBENCH_SCHEMA_VERSION):
            raise HardwareStartupError("HARDWARE_DATA_MANIFEST_SCHEMA_MISMATCH")
        with closing(_connect_readonly(hardware)) as connection:
            tables = _table_names(connection)
            if not {"hardware_case_source_registry", "hardware_r1_source_binding"}.issubset(tables):
                raise HardwareStartupError("SOURCE_INTEGRITY_FAILED")
        with closing(_connect_readonly(asset)) as connection:
            if "hardware_candidate_asset" not in _table_names(connection):
                raise HardwareStartupError("ASSET_SCHEMA_INVALID")
        if not self._workbench_has_required_schema(workbench):
            raise HardwareStartupError("WORKBENCH_SCHEMA_INVALID")
        if not allow_no_manifest_source:
            self._verify_active_sources(hardware, source_root)
            self._verify_cross_references(hardware, asset, workbench)


__all__ = [
    "HARDWARE_APP_VERSION",
    "HardwareStartupCoordinator",
    "HardwareStartupError",
    "WORKBENCH_SCHEMA_VERSION",
]
