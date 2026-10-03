"""Hardware-owned schema, migration, backup, restore and recovery control."""
from __future__ import annotations

import gc
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from services.hardware_migrations import v001_current_schema, v002_r1_source_binding


CURRENT_SCHEMA_VERSION = 2
SCHEMA_VERSION_NAME = "HARDWARE_SCHEMA_V2"
VERSION_TABLE = "hardware_schema_version"
MIGRATION_TABLE = "hardware_schema_migration"
RECOVERY_SUFFIX = ".recovery.json"


class HardwareDataReliabilityError(RuntimeError):
    def __init__(self, code: str, *, backup_id: str | None = None):
        self.code = code
        self.backup_id = backup_id
        super().__init__(code)


@dataclass(frozen=True)
class Migration:
    migration_id: str
    source_version: int
    target_version: int
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS = (
    Migration(
        v001_current_schema.MIGRATION_ID,
        v001_current_schema.SOURCE_VERSION,
        v001_current_schema.TARGET_VERSION,
        v001_current_schema.apply,
    ),
    Migration(
        v002_r1_source_binding.MIGRATION_ID,
        v002_r1_source_binding.SOURCE_VERSION,
        v002_r1_source_binding.TARGET_VERSION,
        v002_r1_source_binding.apply,
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HardwareDataReliabilityManager:
    def __init__(
        self,
        db_path: str | Path,
        *,
        backup_root: str | Path | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_root = (
            Path(backup_root)
            if backup_root is not None
            else self.db_path.with_name(self.db_path.stem + "_backups")
        )
        self.recovery_path = self.db_path.with_name(
            self.db_path.name + RECOVERY_SUFFIX
        )
        self.fault_injector = fault_injector

    def connect(self, path: Path | None = None) -> sqlite3.Connection:
        connection = sqlite3.connect(path or self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _user_tables(connection: sqlite3.Connection) -> list[str]:
        return [
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None

    def _write_recovery(self, payload: dict[str, Any]) -> None:
        self.recovery_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_recovery(self) -> dict[str, Any] | None:
        if not self.recovery_path.is_file():
            return None
        try:
            value = json.loads(self.recovery_path.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "RECOVERY_STATE_INVALID"}
        return value if isinstance(value, dict) else {"status": "RECOVERY_STATE_INVALID"}

    def _clear_recovery(self) -> None:
        try:
            self.recovery_path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _bootstrap_metadata(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hardware_schema_version (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                schema_version INTEGER NOT NULL,
                schema_name TEXT NOT NULL,
                state TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hardware_schema_migration (
                migration_id TEXT PRIMARY KEY,
                source_version INTEGER NOT NULL,
                target_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                backup_id TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error_code TEXT
            )
            """
        )

    @staticmethod
    def _schema_fingerprint(connection: sqlite3.Connection) -> str:
        rows = connection.execute(
            """
            SELECT type,name,tbl_name,COALESCE(sql,'') AS sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
              AND name NOT IN ('hardware_schema_version','hardware_schema_migration')
            ORDER BY type,name
            """
        ).fetchall()
        normalized = "\n".join(
            "|".join(str(item) for item in row)
            for row in rows
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _integrity(connection: sqlite3.Connection) -> str:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"

    @staticmethod
    def _validate_required_schema(connection: sqlite3.Connection) -> None:
        required_schema = {
            **v001_current_schema.REQUIRED_COLUMNS,
            **v002_r1_source_binding.REQUIRED_COLUMNS,
        }
        for table, required in required_schema.items():
            columns = {
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            missing = sorted(required - columns)
            if missing:
                raise HardwareDataReliabilityError(
                    "SCHEMA_REQUIRED_COLUMN_MISSING:"
                    + table
                    + ":"
                    + ",".join(missing)
                )

    def current_version(self) -> int | None:
        if not self.db_path.is_file():
            return None
        with closing(self.connect()) as connection:
            if not self._table_exists(connection, VERSION_TABLE):
                return None
            row = connection.execute(
                "SELECT schema_version FROM hardware_schema_version WHERE singleton=1"
            ).fetchone()
            return int(row[0]) if row else None

    def inspect_status(self) -> dict[str, Any]:
        recovery = self._read_recovery()
        if recovery is not None:
            return {
                "status": "UNREADY",
                "error_code": str(recovery.get("status") or "RECOVERY_REQUIRED"),
                "recovery": {
                    "backup_id": recovery.get("backup_id"),
                    "source_schema": recovery.get("source_schema"),
                    "target_schema": recovery.get("target_schema"),
                },
            }
        if not self.db_path.is_file():
            return {
                "status": "UNINITIALIZED",
                "schema_version": None,
                "schema_name": None,
            }
        try:
            with closing(self.connect()) as connection:
                if self._integrity(connection).lower() != "ok":
                    return {
                        "status": "UNREADY",
                        "error_code": "HARDWARE_DB_INTEGRITY_FAILED",
                    }
                tables = self._user_tables(connection)
                if not tables:
                    return {
                        "status": "UNINITIALIZED",
                        "schema_version": None,
                        "schema_name": None,
                    }
                if not self._table_exists(connection, VERSION_TABLE):
                    return {
                        "status": "UNREADY",
                        "error_code": "SCHEMA_VERSION_MISSING",
                        "schema_version": 0,
                        "schema_name": "LEGACY_UNVERSIONED",
                    }
                row = connection.execute(
                    """
                    SELECT schema_version,schema_name,state,fingerprint,updated_at
                    FROM hardware_schema_version WHERE singleton=1
                    """
                ).fetchone()
                if row is None:
                    return {
                        "status": "UNREADY",
                        "error_code": "SCHEMA_VERSION_MISSING",
                    }
                version = int(row["schema_version"])
                if version != CURRENT_SCHEMA_VERSION:
                    return {
                        "status": "UNREADY",
                        "error_code": "UNKNOWN_SCHEMA_VERSION",
                        "schema_version": version,
                        "schema_name": row["schema_name"],
                    }
                if str(row["state"]) != "READY":
                    return {
                        "status": "UNREADY",
                        "error_code": "SCHEMA_STATE_NOT_READY",
                        "schema_version": version,
                        "schema_name": row["schema_name"],
                        "schema_state": row["state"],
                    }
                try:
                    self._validate_required_schema(connection)
                except HardwareDataReliabilityError as error:
                    return {
                        "status": "UNREADY",
                        "error_code": error.code,
                        "schema_version": version,
                        "schema_name": row["schema_name"],
                    }
                fingerprint = self._schema_fingerprint(connection)
                if fingerprint != str(row["fingerprint"]):
                    return {
                        "status": "UNREADY",
                        "error_code": "SCHEMA_DRIFT_DETECTED",
                        "schema_version": version,
                        "schema_name": row["schema_name"],
                    }
                return {
                    "status": "READY",
                    "schema_version": version,
                    "schema_name": str(row["schema_name"]),
                    "schema_state": str(row["state"]),
                    "fingerprint": fingerprint[:16],
                    "updated_at": str(row["updated_at"]),
                }
        except sqlite3.DatabaseError:
            return {
                "status": "UNREADY",
                "error_code": "HARDWARE_DB_UNAVAILABLE",
            }

    def create_backup(
        self,
        *,
        reason: str,
        source_schema: int | None = None,
        target_schema: int | None = None,
    ) -> dict[str, Any]:
        if not self.db_path.is_file():
            raise HardwareDataReliabilityError("BACKUP_SOURCE_MISSING")
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_id = "HWB-" + uuid4().hex.upper()
        backup_db = self.backup_root / f"{backup_id}.sqlite3"
        manifest_path = self.backup_root / f"{backup_id}.json"
        try:
            with closing(self.connect()) as source, closing(sqlite3.connect(backup_db)) as target:
                source.backup(target)
                target.execute("PRAGMA foreign_keys=ON")
                integrity = self._integrity(target)
                if integrity.lower() != "ok":
                    raise HardwareDataReliabilityError("BACKUP_INTEGRITY_FAILED")
            manifest = {
                "backup_id": backup_id,
                "reason": str(reason),
                "created_at": _utc_now(),
                "source_db_name": self.db_path.name,
                "source_schema": source_schema,
                "target_schema": target_schema,
                "backup_file": backup_db.name,
                "backup_sha256": _sha256(backup_db),
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return manifest
        except Exception as error:
            try:
                backup_db.unlink()
            except OSError:
                pass
            try:
                manifest_path.unlink()
            except OSError:
                pass
            if isinstance(error, HardwareDataReliabilityError):
                raise
            raise HardwareDataReliabilityError("BACKUP_FAILED") from error

    def _load_backup(self, backup_id: str) -> tuple[dict[str, Any], Path]:
        manifest_path = self.backup_root / f"{backup_id}.json"
        if not manifest_path.is_file():
            raise HardwareDataReliabilityError("BACKUP_NOT_FOUND")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as error:
            raise HardwareDataReliabilityError("BACKUP_MANIFEST_INVALID") from error
        backup_db = self.backup_root / str(manifest.get("backup_file") or "")
        if not backup_db.is_file():
            raise HardwareDataReliabilityError("BACKUP_FILE_MISSING")
        if _sha256(backup_db) != str(manifest.get("backup_sha256") or ""):
            raise HardwareDataReliabilityError("BACKUP_HASH_MISMATCH")
        with closing(sqlite3.connect(backup_db)) as connection:
            if self._integrity(connection).lower() != "ok":
                raise HardwareDataReliabilityError("BACKUP_INTEGRITY_FAILED")
        return manifest, backup_db

    def restore(
        self,
        backup_id: str,
        *,
        target_db_path: str | Path | None = None,
    ) -> dict[str, Any]:
        manifest, backup_db = self._load_backup(backup_id)
        target_path = Path(target_db_path) if target_db_path else self.db_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(target_path.name + ".restore.tmp")
        try:
            if temp_path.exists():
                temp_path.unlink()
            with closing(sqlite3.connect(backup_db)) as source, closing(sqlite3.connect(temp_path)) as target:
                source.backup(target)
                if self._integrity(target).lower() != "ok":
                    raise HardwareDataReliabilityError("RESTORE_INTEGRITY_FAILED")
            # CPython sqlite connections created by callers can be GC-finalized
            # slightly after their last reference disappears. Collect only stale,
            # unreferenced handles; genuinely active target handles still make
            # os.replace fail closed as RESTORE_TARGET_BUSY.
            gc.collect()
            os.replace(temp_path, target_path)
        except Exception as error:
            try:
                temp_path.unlink()
            except OSError:
                pass
            if isinstance(error, HardwareDataReliabilityError):
                raise
            if isinstance(error, PermissionError):
                raise HardwareDataReliabilityError("RESTORE_TARGET_BUSY") from error
            raise HardwareDataReliabilityError("RESTORE_FAILED") from error
        return {
            "status": "RESTORED",
            "backup_id": backup_id,
            "target_db_name": target_path.name,
            "source_schema": manifest.get("source_schema"),
            "target_schema": manifest.get("target_schema"),
            "restored_sha256": _sha256(target_path),
        }

    def _migration_for(self, source_version: int) -> Migration:
        for migration in MIGRATIONS:
            if migration.source_version == source_version:
                return migration
        raise HardwareDataReliabilityError("MIGRATION_PATH_NOT_FOUND")

    def _legacy_version(self) -> int:
        if not self.db_path.is_file():
            return 0
        with closing(self.connect()) as connection:
            tables = self._user_tables(connection)
            if not tables:
                return 0
            if self._table_exists(connection, VERSION_TABLE):
                row = connection.execute(
                    "SELECT schema_version FROM hardware_schema_version WHERE singleton=1"
                ).fetchone()
                if row is None:
                    raise HardwareDataReliabilityError("SCHEMA_VERSION_MISSING")
                return int(row[0])
            return 0

    def _record_ready(self, connection: sqlite3.Connection, version: int) -> None:
        fingerprint = self._schema_fingerprint(connection)
        connection.execute(
            """
            INSERT INTO hardware_schema_version(
                singleton,schema_version,schema_name,state,fingerprint,updated_at
            ) VALUES(1,?,?,?,?,?)
            ON CONFLICT(singleton) DO UPDATE SET
                schema_version=excluded.schema_version,
                schema_name=excluded.schema_name,
                state=excluded.state,
                fingerprint=excluded.fingerprint,
                updated_at=excluded.updated_at
            """,
            (
                version,
                SCHEMA_VERSION_NAME,
                "READY",
                fingerprint,
                _utc_now(),
            ),
        )

    def ensure_ready(self) -> dict[str, Any]:
        recovery = self._read_recovery()
        if recovery is not None:
            raise HardwareDataReliabilityError(
                "RECOVERY_REQUIRED",
                backup_id=recovery.get("backup_id"),
            )
        status = self.inspect_status()
        if status.get("status") == "READY":
            return status
        if status.get("error_code") == "UNKNOWN_SCHEMA_VERSION":
            raise HardwareDataReliabilityError("UNKNOWN_SCHEMA_VERSION")
        source_version = self._legacy_version()
        if source_version > CURRENT_SCHEMA_VERSION:
            raise HardwareDataReliabilityError("UNKNOWN_SCHEMA_VERSION")
        if source_version == CURRENT_SCHEMA_VERSION:
            raise HardwareDataReliabilityError(
                str(status.get("error_code") or "SCHEMA_NOT_READY")
            )

        existing = self.db_path.is_file() and self.db_path.stat().st_size > 0
        backup: dict[str, Any] | None = None
        if existing:
            backup = self.create_backup(
                reason="PRE_MIGRATION",
                source_schema=source_version,
                target_schema=CURRENT_SCHEMA_VERSION,
            )
        backup_id = backup.get("backup_id") if backup else None
        migration = self._migration_for(source_version)
        try:
            with closing(self.connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._bootstrap_metadata(connection)
                started_at = _utc_now()
                connection.execute(
                    """
                    INSERT OR REPLACE INTO hardware_schema_migration(
                        migration_id,source_version,target_version,status,
                        backup_id,started_at,completed_at,error_code
                    ) VALUES(?,?,?,?,?,?,NULL,NULL)
                    """,
                    (
                        migration.migration_id,
                        migration.source_version,
                        migration.target_version,
                        "RUNNING",
                        backup_id,
                        started_at,
                    ),
                )
                migration.apply(connection)
                self._validate_required_schema(connection)
                if self.fault_injector is not None:
                    self.fault_injector("before_commit")
                self._record_ready(connection, migration.target_version)
                connection.execute(
                    """
                    UPDATE hardware_schema_migration
                    SET status='COMPLETED',completed_at=?,error_code=NULL
                    WHERE migration_id=?
                    """,
                    (_utc_now(), migration.migration_id),
                )
                connection.commit()
            self._clear_recovery()
            ready = self.inspect_status()
            if ready.get("status") != "READY":
                raise HardwareDataReliabilityError(
                    str(ready.get("error_code") or "MIGRATION_VERIFY_FAILED")
                )
            return {
                **ready,
                "migration_id": migration.migration_id,
                "backup_id": backup_id,
            }
        except Exception as error:
            if backup_id:
                try:
                    self.restore(backup_id)
                except Exception:
                    pass
            marker = {
                "status": "MIGRATION_FAILED_RECOVERY_REQUIRED",
                "error_code": getattr(error, "code", type(error).__name__),
                "backup_id": backup_id,
                "source_schema": source_version,
                "target_schema": CURRENT_SCHEMA_VERSION,
                "failed_at": _utc_now(),
            }
            self._write_recovery(marker)
            if isinstance(error, HardwareDataReliabilityError):
                raise HardwareDataReliabilityError(
                    "MIGRATION_FAILED",
                    backup_id=backup_id,
                ) from error
            raise HardwareDataReliabilityError(
                "MIGRATION_FAILED",
                backup_id=backup_id,
            ) from error

    def recover_from_backup(self, backup_id: str) -> dict[str, Any]:
        self.restore(backup_id)
        self._clear_recovery()
        try:
            return self.ensure_ready()
        except Exception:
            self._write_recovery(
                {
                    "status": "RECOVERY_FAILED",
                    "backup_id": backup_id,
                    "source_schema": None,
                    "target_schema": CURRENT_SCHEMA_VERSION,
                    "failed_at": _utc_now(),
                }
            )
            raise


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "HardwareDataReliabilityError",
    "HardwareDataReliabilityManager",
    "SCHEMA_VERSION_NAME",
]
