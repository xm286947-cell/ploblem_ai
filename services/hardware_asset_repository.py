"""Durable repository for Hardware R1 Candidate Assets.

This module owns only ``hardware_asset.db``. It deliberately has no Workbench,
Source Store, Runtime, Provider, or Unified Knowledge dependency. Schema
creation is explicit so startup orchestration can classify the data root before
opening or initializing the database.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from services.hardware_asset_migrations import (
    v001_candidate_repository,
    v002_legacy_migration,
)


_ASSET_MIGRATIONS = (v001_candidate_repository, v002_legacy_migration)
_MIGRATIONS_BY_SOURCE = {item.SOURCE_VERSION: item for item in _ASSET_MIGRATIONS}
ASSET_SCHEMA_VERSION = _ASSET_MIGRATIONS[-1].TARGET_VERSION
_SCHEMA_NAMES = {
    v001_candidate_repository.TARGET_VERSION: v001_candidate_repository.SCHEMA_NAME,
    v002_legacy_migration.TARGET_VERSION: v002_legacy_migration.SCHEMA_NAME,
}
KNOWLEDGE_OBJECT_CONTRACT_VERSION = "hardware-case-knowledge-object/v1"
ASSET_STATUSES = frozenset({"ACTIVE", "INVALIDATED"})
REVIEW_STATUSES = frozenset({"NOT_REQUIRED", "REQUIRED", "RESOLVED"})
PROMOTION_STATUSES = frozenset(
    {
        "NOT_STARTED",
        "PRECHECK_PASS",
        "CANDIDATE_INTAKED",
        "INTAKE_FAILED",
        "REVIEW_CONFIRMED",
        "REVIEW_FAILED",
        "PUBLISHED_PENDING_QUERY_BACK",
        "PUBLISH_FAILED",
        "VERIFIED",
        "VERIFY_FAILED",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CandidateAssetRepositoryError(RuntimeError):
    """Stable error contract for local Durable Candidate operations."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID") from error


def candidate_identity(business_case_id: str, source_id: str) -> str:
    payload = f"{business_case_id}\n{source_id}".encode("utf-8")
    return "HCAND-" + hashlib.sha256(payload).hexdigest()


def candidate_content_hash(knowledge_object: Mapping[str, Any]) -> str:
    canonical = _canonical_json(dict(knowledge_object))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deterministic_evidence_id(
    business_case_id: str, source_id: str, block_id: str
) -> str:
    """Reuse the existing Hardware R1 evidence-reference identity rule."""
    digest = hashlib.sha256(
        f"{business_case_id}|{source_id}|{block_id}".encode("utf-8")
    ).hexdigest()
    return "HCR1-EV-" + digest[:24]


def _is_unresolved_conflict(value: Mapping[str, Any]) -> bool:
    return (
        str(value.get("status") or "").upper() == "OPEN"
        or str(value.get("resolution_status") or "").upper() == "NEEDS_REVIEW"
    )


class CandidateAssetRepository:
    """Versioned SQLite repository for immutable Candidate identity and audit."""

    def __init__(self, db_path: str | Path, *, timeout_seconds: float = 5.0):
        self.db_path = Path(db_path).expanduser()
        self.timeout_seconds = float(timeout_seconds)

    def initialize(self) -> dict[str, Any]:
        """Create or deterministically migrate the explicitly selected asset DB."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(
                sqlite3.connect(self.db_path, timeout=self.timeout_seconds)
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                tables = self._table_names(connection)
                version_table = "hardware_asset_schema_version"
                if version_table not in tables:
                    if tables:
                        raise CandidateAssetRepositoryError(
                            "ASSET_SCHEMA_VERSION_MISSING"
                        )
                    v001_candidate_repository.apply(connection)
                    self._set_schema_version(
                        connection,
                        v001_candidate_repository.TARGET_VERSION,
                        v001_candidate_repository.SCHEMA_NAME,
                    )
                    self._write_migration_record(
                        connection, v001_candidate_repository, 0
                    )
                    current_version = v001_candidate_repository.TARGET_VERSION
                else:
                    columns = self._columns(connection, version_table)
                    if not {"singleton", "schema_version", "schema_name", "updated_at"}.issubset(columns):
                        raise CandidateAssetRepositoryError(
                            "ASSET_SCHEMA_VERSION_MISSING"
                        )
                    version_row = connection.execute(
                        "SELECT schema_version,schema_name FROM hardware_asset_schema_version "
                        "WHERE singleton=1"
                    ).fetchone()
                    if version_row is None:
                        raise CandidateAssetRepositoryError(
                            "ASSET_SCHEMA_VERSION_MISSING"
                        )
                    current_version = int(version_row["schema_version"])
                    if current_version > ASSET_SCHEMA_VERSION:
                        raise CandidateAssetRepositoryError("ASSET_SCHEMA_TOO_NEW")
                    if current_version > 0 and version_row["schema_name"] != _SCHEMA_NAMES.get(
                        current_version
                    ):
                        raise CandidateAssetRepositoryError(
                            "ASSET_SCHEMA_VERSION_MISMATCH"
                        )
                while current_version < ASSET_SCHEMA_VERSION:
                    migration = _MIGRATIONS_BY_SOURCE.get(current_version)
                    if migration is None:
                        raise CandidateAssetRepositoryError(
                            "ASSET_SCHEMA_MIGRATION_UNSUPPORTED"
                        )
                    migration.apply(connection)
                    current_version = migration.TARGET_VERSION
                    self._set_schema_version(
                        connection,
                        current_version,
                        migration.SCHEMA_NAME,
                    )
                    self._write_migration_record(
                        connection, migration, migration.SOURCE_VERSION
                    )
                self._validate_migration_history(connection)
                self._validate_schema(connection)
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or str(integrity[0]).lower() != "ok":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_DATA_INTEGRITY_ERROR"
                    )
                connection.commit()
        except CandidateAssetRepositoryError:
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_ASSET_STORE_UNAVAILABLE"
            ) from error
        return {"schema_version": ASSET_SCHEMA_VERSION, "status": "READY"}

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _set_schema_version(
        connection: sqlite3.Connection,
        schema_version: int,
        schema_name: str,
    ) -> None:
        now = _utc_now()
        connection.execute(
            """
            INSERT INTO hardware_asset_schema_version(
                singleton,schema_version,schema_name,updated_at
            ) VALUES(1,?,?,?)
            ON CONFLICT(singleton) DO UPDATE SET
                schema_version=excluded.schema_version,
                schema_name=excluded.schema_name,
                updated_at=excluded.updated_at
            """,
            (schema_version, schema_name, now),
        )

    @staticmethod
    def _write_migration_record(
        connection: sqlite3.Connection,
        migration: Any,
        source_version: int,
    ) -> None:
        now = _utc_now()
        connection.execute(
            """
            INSERT INTO hardware_asset_schema_migration(
                migration_id,source_version,target_version,applied_at
            ) VALUES(?,?,?,?)
            ON CONFLICT(migration_id) DO NOTHING
            """,
            (
                migration.MIGRATION_ID,
                source_version,
                migration.TARGET_VERSION,
                now,
            ),
        )

    @staticmethod
    def _validate_migration_history(connection: sqlite3.Connection) -> None:
        for migration in _ASSET_MIGRATIONS:
            row = connection.execute(
                "SELECT source_version,target_version "
                "FROM hardware_asset_schema_migration WHERE migration_id=?",
                (migration.MIGRATION_ID,),
            ).fetchone()
            if (
                row is None
                or int(row["source_version"]) != migration.SOURCE_VERSION
                or int(row["target_version"]) != migration.TARGET_VERSION
            ):
                raise CandidateAssetRepositoryError(
                    "ASSET_SCHEMA_MIGRATION_RECORD_MISSING"
                )

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        required_columns = dict(v001_candidate_repository.REQUIRED_COLUMNS)
        if ASSET_SCHEMA_VERSION >= v002_legacy_migration.TARGET_VERSION:
            required_columns.update(v002_legacy_migration.REQUIRED_COLUMNS)
        for table, required in required_columns.items():
            actual = CandidateAssetRepository._columns(connection, table)
            if not required.issubset(actual):
                raise CandidateAssetRepositoryError(
                    "CANDIDATE_DATA_INTEGRITY_ERROR"
                )
        required_triggers = {
            "hardware_candidate_event_no_update",
            "hardware_candidate_event_no_delete",
            "hardware_candidate_review_no_update",
            "hardware_candidate_review_no_delete",
            "hardware_candidate_asset_no_delete",
        }
        if ASSET_SCHEMA_VERSION >= v002_legacy_migration.TARGET_VERSION:
            required_triggers.update(
                {
                    "hardware_candidate_legacy_origin_no_update",
                    "hardware_candidate_legacy_origin_no_delete",
                }
            )
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        if not required_triggers.issubset(triggers):
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            )

    def schema_version(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT schema_version FROM hardware_asset_schema_version "
                "WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise CandidateAssetRepositoryError("ASSET_SCHEMA_VERSION_MISSING")
            version = int(row["schema_version"])
            if version > ASSET_SCHEMA_VERSION:
                raise CandidateAssetRepositoryError("ASSET_SCHEMA_TOO_NEW")
            return version

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise CandidateAssetRepositoryError(
                "CANDIDATE_ASSET_STORE_UNAVAILABLE"
            )
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.db_path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            version = connection.execute(
                "SELECT schema_version,schema_name "
                "FROM hardware_asset_schema_version WHERE singleton=1"
            ).fetchone()
            if version is None:
                raise CandidateAssetRepositoryError(
                    "ASSET_SCHEMA_VERSION_MISSING"
                )
            schema_version = int(version["schema_version"])
            if schema_version > ASSET_SCHEMA_VERSION:
                raise CandidateAssetRepositoryError("ASSET_SCHEMA_TOO_NEW")
            if schema_version != ASSET_SCHEMA_VERSION:
                raise CandidateAssetRepositoryError(
                    "ASSET_SCHEMA_MIGRATION_REQUIRED"
                )
            if version["schema_name"] != _SCHEMA_NAMES.get(schema_version):
                raise CandidateAssetRepositoryError(
                    "ASSET_SCHEMA_VERSION_MISMATCH"
                )
            self._validate_migration_history(connection)
            return connection
        except CandidateAssetRepositoryError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise CandidateAssetRepositoryError(
                "CANDIDATE_ASSET_STORE_UNAVAILABLE"
            ) from error

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validated_candidate_input(
        *,
        business_case_id: str,
        source_id: str,
        source_ref: str,
        knowledge_object: Mapping[str, Any],
        pipeline_version: str,
        agent_config_version: str,
        knowledge_schema_version: str,
        validator_version: str,
    ) -> dict[str, Any]:
        case_id = str(business_case_id or "").strip()
        source = str(source_id or "").strip()
        ref = str(source_ref or "").strip()
        if (
            not case_id
            or not _SHA256_RE.fullmatch(source)
            or not ref
            or not isinstance(knowledge_object, Mapping)
        ):
            raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")
        version_fields = {
            "pipeline_version": pipeline_version,
            "agent_config_version": agent_config_version,
            "knowledge_schema_version": knowledge_schema_version,
            "validator_version": validator_version,
        }
        versions = {
            name: str(value or "").strip()
            for name, value in version_fields.items()
        }
        if any(not value for value in versions.values()):
            raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")

        obj = dict(knowledge_object)
        identity = obj.get("identity")
        source_fact = obj.get("source_fact")
        evidence = obj.get("evidence")
        review = obj.get("review")
        conflicts = obj.get("conflicts", [])
        if (
            obj.get("contract_version") != KNOWLEDGE_OBJECT_CONTRACT_VERSION
            or not isinstance(identity, Mapping)
            or not isinstance(source_fact, Mapping)
            or str(identity.get("business_case_id") or "").strip() != case_id
            or str(source_fact.get("source_id") or "").strip() != source
            or not isinstance(evidence, list)
            or not evidence
            or not isinstance(review, Mapping)
            or not isinstance(conflicts, list)
            or review.get("object_status") != "CANDIDATE"
        ):
            raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")

        evidence_refs: list[dict[str, str]] = []
        seen_blocks: set[str] = set()
        for item in evidence:
            if not isinstance(item, Mapping):
                raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")
            block_id = str(item.get("block_id") or "").strip()
            locator = item.get("source_locator")
            if (
                not block_id
                or block_id in seen_blocks
                or not isinstance(locator, Mapping)
            ):
                raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")
            seen_blocks.add(block_id)
            evidence_refs.append(
                {
                    "evidence_id": deterministic_evidence_id(case_id, source, block_id),
                    "block_id": block_id,
                    "locator_json": _canonical_json(dict(locator)),
                }
            )
        evidence_refs.sort(key=lambda item: (item["evidence_id"], item["block_id"]))
        knowledge_json = _canonical_json(obj)
        candidate_hash = hashlib.sha256(knowledge_json.encode("utf-8")).hexdigest()
        unresolved = any(
            isinstance(item, Mapping) and _is_unresolved_conflict(item)
            for item in conflicts
        )
        return {
            "candidate_id": candidate_identity(case_id, source),
            "business_case_id": case_id,
            "source_id": source,
            "source_ref": ref,
            "candidate_hash": candidate_hash,
            "knowledge_object_json": knowledge_json,
            "knowledge_object": obj,
            "evidence_refs": evidence_refs,
            "production_review_status": "REQUIRED" if unresolved else "NOT_REQUIRED",
            **versions,
        }

    def create_or_commit_candidate(
        self,
        *,
        business_case_id: str,
        source_id: str,
        source_ref: str,
        knowledge_object: Mapping[str, Any],
        generation_run_id: str | None,
        pipeline_version: str,
        agent_config_version: str,
        knowledge_schema_version: str,
        validator_version: str,
    ) -> dict[str, Any]:
        value = self._validated_candidate_input(
            business_case_id=business_case_id,
            source_id=source_id,
            source_ref=source_ref,
            knowledge_object=knowledge_object,
            pipeline_version=pipeline_version,
            agent_config_version=agent_config_version,
            knowledge_schema_version=knowledge_schema_version,
            validator_version=validator_version,
        )
        run_id = str(generation_run_id or "").strip() or None
        now = _utc_now()
        try:
            with self._write_transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM hardware_candidate_asset "
                    "WHERE business_case_id=? AND source_id=?",
                    (value["business_case_id"], value["source_id"]),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO hardware_candidate_asset(
                            candidate_id,business_case_id,source_id,source_ref,
                            candidate_hash,knowledge_object_json,generation_run_id,
                            pipeline_version,agent_config_version,
                            knowledge_schema_version,validator_version,asset_status,
                            production_review_status,promotion_status,row_version,
                            created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,'NOT_STARTED',1,?,?)
                        """,
                        (
                            value["candidate_id"],
                            value["business_case_id"],
                            value["source_id"],
                            value["source_ref"],
                            value["candidate_hash"],
                            value["knowledge_object_json"],
                            run_id,
                            value["pipeline_version"],
                            value["agent_config_version"],
                            value["knowledge_schema_version"],
                            value["validator_version"],
                            value["production_review_status"],
                            now,
                            now,
                        ),
                    )
                    self._insert_evidence_refs(
                        connection, value, created_at=now
                    )
                    self._insert_event(
                        connection,
                        candidate_id=value["candidate_id"],
                        event_type="CREATED",
                        old_hash=None,
                        new_hash=value["candidate_hash"],
                        run_id=run_id,
                        actor="HARDWARE_R1_PIPELINE",
                        reason="Durable candidate created",
                        created_at=now,
                    )
                    commit_result = "CREATED"
                else:
                    if row["candidate_id"] != value["candidate_id"]:
                        raise CandidateAssetRepositoryError(
                            "CANDIDATE_DATA_INTEGRITY_ERROR"
                        )
                    if row["source_ref"] != value["source_ref"]:
                        raise CandidateAssetRepositoryError(
                            "CANDIDATE_SOURCE_INVALID"
                        )
                    old_hash = str(row["candidate_hash"])
                    new_hash = str(value["candidate_hash"])
                    old_status = str(row["asset_status"])
                    promotion_status = str(row["promotion_status"])
                    review_status = str(row["production_review_status"])
                    if old_status == "INVALIDATED":
                        if promotion_status != "NOT_STARTED":
                            raise CandidateAssetRepositoryError(
                                "CANDIDATE_LOCKED_BY_PROMOTION"
                            )
                        if old_hash != new_hash:
                            raise CandidateAssetRepositoryError(
                                "CANDIDATE_ASSET_INVALIDATED"
                            )
                        event_type = "REACTIVATED"
                        commit_result = "REACTIVATED"
                        next_asset_status = "ACTIVE"
                    elif old_hash == new_hash:
                        commit_result = "IDEMPOTENT_REUSE"
                        result = self._public_candidate(
                            connection, row, commit_result=commit_result
                        )
                        return result
                    else:
                        if promotion_status != "NOT_STARTED":
                            raise CandidateAssetRepositoryError(
                                "CANDIDATE_LOCKED_BY_PROMOTION"
                            )
                        if review_status == "RESOLVED":
                            raise CandidateAssetRepositoryError(
                                "CANDIDATE_LOCKED_BY_REVIEW"
                            )
                        event_type = "REGENERATED"
                        commit_result = "REGENERATED"
                        next_asset_status = "ACTIVE"

                    connection.execute(
                        """
                        UPDATE hardware_candidate_asset
                        SET source_ref=?,candidate_hash=?,knowledge_object_json=?,
                            generation_run_id=?,pipeline_version=?,agent_config_version=?,
                            knowledge_schema_version=?,validator_version=?,asset_status=?,
                            production_review_status=?,row_version=row_version+1,updated_at=?
                        WHERE candidate_id=?
                        """,
                        (
                            value["source_ref"],
                            value["candidate_hash"],
                            value["knowledge_object_json"],
                            run_id,
                            value["pipeline_version"],
                            value["agent_config_version"],
                            value["knowledge_schema_version"],
                            value["validator_version"],
                            next_asset_status,
                            (
                                review_status
                                if event_type == "REACTIVATED"
                                else value["production_review_status"]
                            ),
                            now,
                            value["candidate_id"],
                        ),
                    )
                    connection.execute(
                        "DELETE FROM hardware_candidate_evidence_ref WHERE candidate_id=?",
                        (value["candidate_id"],),
                    )
                    self._insert_evidence_refs(
                        connection, value, created_at=now
                    )
                    self._insert_event(
                        connection,
                        candidate_id=value["candidate_id"],
                        event_type=event_type,
                        old_hash=old_hash,
                        new_hash=new_hash,
                        run_id=run_id,
                        actor="HARDWARE_R1_PIPELINE",
                        reason=(
                            "Invalidated candidate reactivated from the same source"
                            if event_type == "REACTIVATED"
                            else "Unreviewed candidate regenerated"
                        ),
                        created_at=now,
                    )
                result_row = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (value["candidate_id"],),
                ).fetchone()
                return self._public_candidate(
                    connection, result_row, commit_result=commit_result
                )
        except CandidateAssetRepositoryError:
            raise
        except sqlite3.Error as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            ) from error

    @staticmethod
    def _insert_evidence_refs(
        connection: sqlite3.Connection,
        value: Mapping[str, Any],
        *,
        created_at: str,
    ) -> None:
        connection.executemany(
            """
            INSERT INTO hardware_candidate_evidence_ref(
                candidate_id,evidence_id,source_id,source_ref,block_id,
                locator_json,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            [
                (
                    value["candidate_id"],
                    item["evidence_id"],
                    value["source_id"],
                    value["source_ref"],
                    item["block_id"],
                    item["locator_json"],
                    created_at,
                )
                for item in value["evidence_refs"]
            ],
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
        event_type: str,
        old_hash: str | None,
        new_hash: str | None,
        run_id: str | None,
        actor: str,
        reason: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO hardware_candidate_event(
                event_id,candidate_id,event_type,old_candidate_hash,
                new_candidate_hash,run_id,actor,reason,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "HCEV-" + uuid4().hex,
                candidate_id,
                event_type,
                old_hash,
                new_hash,
                run_id,
                actor,
                reason,
                created_at,
            ),
        )

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (str(candidate_id),),
                ).fetchone()
                return (
                    None
                    if row is None
                    else self._public_candidate(connection, row)
                )
        except CandidateAssetRepositoryError:
            raise
        except sqlite3.Error as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            ) from error

    def find_by_business_case_id(self, business_case_id: str) -> list[dict[str, Any]]:
        return self._find(
            "business_case_id", str(business_case_id or "").strip()
        )

    def find_by_source_id(self, source_id: str) -> list[dict[str, Any]]:
        return self._find("source_id", str(source_id or "").strip())

    def get_active_by_business_case_id(
        self, business_case_id: str
    ) -> list[dict[str, Any]]:
        return self._find(
            "business_case_id",
            str(business_case_id or "").strip(),
            asset_status="ACTIVE",
        )

    def _find(
        self,
        field: str,
        value: str,
        *,
        asset_status: str | None = None,
    ) -> list[dict[str, Any]]:
        if field not in {"business_case_id", "source_id"}:
            raise ValueError("unsupported candidate lookup")
        try:
            with closing(self._connect()) as connection:
                sql = f"SELECT * FROM hardware_candidate_asset WHERE {field}=?"
                params: list[Any] = [value]
                if asset_status is not None:
                    sql += " AND asset_status=?"
                    params.append(asset_status)
                sql += " ORDER BY candidate_id"
                rows = connection.execute(sql, params).fetchall()
                return [self._public_candidate(connection, row) for row in rows]
        except CandidateAssetRepositoryError:
            raise
        except sqlite3.Error as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            ) from error

    @staticmethod
    def _public_candidate(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        commit_result: str | None = None,
    ) -> dict[str, Any]:
        try:
            knowledge_object = json.loads(row["knowledge_object_json"])
            evidence = connection.execute(
                """
                SELECT evidence_id,source_id,source_ref,block_id,locator_json,created_at
                FROM hardware_candidate_evidence_ref
                WHERE candidate_id=? ORDER BY evidence_id
                """,
                (row["candidate_id"],),
            ).fetchall()
            evidence_refs = [
                {
                    "evidence_id": item["evidence_id"],
                    "source_id": item["source_id"],
                    "source_ref": item["source_ref"],
                    "block_id": item["block_id"],
                    "locator": json.loads(item["locator_json"]),
                    "created_at": item["created_at"],
                }
                for item in evidence
            ]
            result = {
                "candidate_id": row["candidate_id"],
                "business_case_id": row["business_case_id"],
                "source_id": row["source_id"],
                "source_ref": row["source_ref"],
                "candidate_hash": row["candidate_hash"],
                "knowledge_object": knowledge_object,
                "generation_run_id": row["generation_run_id"],
                "pipeline_version": row["pipeline_version"],
                "agent_config_version": row["agent_config_version"],
                "knowledge_schema_version": row["knowledge_schema_version"],
                "validator_version": row["validator_version"],
                "asset_status": row["asset_status"],
                "production_review_status": row["production_review_status"],
                "promotion_status": row["promotion_status"],
                "row_version": int(row["row_version"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "evidence_refs": evidence_refs,
            }
            if commit_result is not None:
                result["commit_result"] = commit_result
            return result
        except (json.JSONDecodeError, TypeError) as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            ) from error

    @staticmethod
    def _validate_reviewed_object(
        knowledge_object: Mapping[str, Any],
        *,
        business_case_id: str,
        source_id: str,
        reviewer: str,
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(knowledge_object, Mapping):
            raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")
        obj = dict(knowledge_object)
        identity = obj.get("identity")
        source_fact = obj.get("source_fact")
        review = obj.get("review")
        evidence = obj.get("evidence")
        conflicts = obj.get("conflicts")
        if (
            obj.get("contract_version") != KNOWLEDGE_OBJECT_CONTRACT_VERSION
            or not isinstance(identity, Mapping)
            or not isinstance(source_fact, Mapping)
            or not isinstance(review, Mapping)
            or review.get("object_status") != "CANDIDATE"
            or str(identity.get("business_case_id") or "").strip() != business_case_id
            or str(source_fact.get("source_id") or "").strip() != source_id
            or not isinstance(evidence, list)
            or not isinstance(conflicts, list)
            or any(
                isinstance(item, Mapping) and _is_unresolved_conflict(item)
                for item in conflicts
            )
            or not str(review.get("reviewer") or "").strip()
            or str(review.get("reviewer") or "").strip() != reviewer
            or not str(review.get("reviewed_at") or "").strip()
        ):
            raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")
        canonical = _canonical_json(obj)
        return canonical, obj

    def apply_production_review(
        self,
        candidate_id: str,
        *,
        reviewed_knowledge_object: Mapping[str, Any],
        expected_row_version: int,
        reviewer: str,
        reason: str,
    ) -> dict[str, Any]:
        candidate_key = str(candidate_id or "").strip()
        reviewer_name = str(reviewer or "").strip()
        reason_text = str(reason or "").strip()
        if not candidate_key or not reviewer_name or not reason_text:
            raise CandidateAssetRepositoryError("CANDIDATE_INPUT_INVALID")
        now = _utc_now()
        try:
            with self._write_transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (candidate_key,),
                ).fetchone()
                if row is None:
                    raise CandidateAssetRepositoryError("CANDIDATE_NOT_FOUND")
                if int(row["row_version"]) != int(expected_row_version):
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_CONCURRENT_UPDATE"
                    )
                if row["asset_status"] != "ACTIVE":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_ASSET_INVALIDATED"
                    )
                if row["promotion_status"] != "NOT_STARTED":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_LOCKED_BY_PROMOTION"
                    )
                if row["production_review_status"] != "REQUIRED":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_REVIEW_TRANSITION_INVALID"
                    )
                canonical, reviewed_obj = self._validate_reviewed_object(
                    reviewed_knowledge_object,
                    business_case_id=str(row["business_case_id"]),
                    source_id=str(row["source_id"]),
                    reviewer=reviewer_name,
                )
                prior_obj = json.loads(row["knowledge_object_json"])
                if prior_obj.get("evidence") != reviewed_obj.get("evidence"):
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_SOURCE_INVALID"
                    )
                new_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                old_hash = str(row["candidate_hash"])
                review_record = {
                    "reviewer": reviewer_name,
                    "reason": reason_text,
                    "review": dict(reviewed_obj["review"]),
                }
                connection.execute(
                    """
                    UPDATE hardware_candidate_asset
                    SET candidate_hash=?,knowledge_object_json=?,
                        production_review_status='RESOLVED',
                        row_version=row_version+1,updated_at=?
                    WHERE candidate_id=?
                    """,
                    (new_hash, canonical, now, candidate_key),
                )
                connection.execute(
                    """
                    INSERT INTO hardware_candidate_review(
                        review_id,candidate_id,before_candidate_hash,
                        after_candidate_hash,reviewer,reason,review_record_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        "HCRV-" + uuid4().hex,
                        candidate_key,
                        old_hash,
                        new_hash,
                        reviewer_name,
                        reason_text,
                        _canonical_json(review_record),
                        now,
                    ),
                )
                self._insert_event(
                    connection,
                    candidate_id=candidate_key,
                    event_type="PRODUCTION_REVIEWED",
                    old_hash=old_hash,
                    new_hash=new_hash,
                    run_id=row["generation_run_id"],
                    actor=reviewer_name,
                    reason=reason_text,
                    created_at=now,
                )
                updated = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (candidate_key,),
                ).fetchone()
                return self._public_candidate(connection, updated)
        except CandidateAssetRepositoryError:
            raise
        except sqlite3.Error as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            ) from error

    def update_review_status(
        self,
        candidate_id: str,
        *,
        expected_status: str,
        new_status: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        candidate_key = str(candidate_id or "").strip()
        expected = str(expected_status or "").strip().upper()
        target = str(new_status or "").strip().upper()
        if not candidate_key or expected not in REVIEW_STATUSES or target not in REVIEW_STATUSES:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_REVIEW_TRANSITION_INVALID"
            )
        now = _utc_now()
        try:
            with self._write_transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (candidate_key,),
                ).fetchone()
                if row is None:
                    raise CandidateAssetRepositoryError("CANDIDATE_NOT_FOUND")
                if (
                    row["production_review_status"] != expected
                    or int(row["row_version"]) != int(expected_row_version)
                ):
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_CONCURRENT_UPDATE"
                    )
                if row["asset_status"] != "ACTIVE":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_ASSET_INVALIDATED"
                    )
                if row["promotion_status"] != "NOT_STARTED":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_LOCKED_BY_PROMOTION"
                    )
                if (expected, target) != ("NOT_REQUIRED", "REQUIRED"):
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_REVIEW_TRANSITION_INVALID"
                    )
                connection.execute(
                    """
                    UPDATE hardware_candidate_asset
                    SET production_review_status=?,row_version=row_version+1,updated_at=?
                    WHERE candidate_id=?
                    """,
                    (target, now, candidate_key),
                )
                updated = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (candidate_key,),
                ).fetchone()
                return self._public_candidate(connection, updated)
        except CandidateAssetRepositoryError:
            raise
        except sqlite3.Error as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            ) from error

    def update_promotion_status(
        self,
        candidate_id: str,
        *,
        expected_status: str,
        new_status: str,
        expected_row_version: int,
        actor: str,
        reason: str,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        candidate_key = str(candidate_id or "").strip()
        expected = str(expected_status or "").strip().upper()
        target = str(new_status or "").strip().upper()
        actor_name = str(actor or "").strip()
        reason_text = str(reason or "").strip()
        if (
            not candidate_key
            or expected not in PROMOTION_STATUSES
            or target not in PROMOTION_STATUSES
            or not actor_name
            or not reason_text
        ):
            raise CandidateAssetRepositoryError(
                "CANDIDATE_PROMOTION_TRANSITION_INVALID"
            )
        if expected == target or target == "NOT_STARTED":
            raise CandidateAssetRepositoryError(
                "CANDIDATE_PROMOTION_TRANSITION_INVALID"
            )
        now = _utc_now()
        try:
            with self._write_transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (candidate_key,),
                ).fetchone()
                if row is None:
                    raise CandidateAssetRepositoryError("CANDIDATE_NOT_FOUND")
                if (
                    row["promotion_status"] != expected
                    or int(row["row_version"]) != int(expected_row_version)
                ):
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_CONCURRENT_UPDATE"
                    )
                if row["asset_status"] != "ACTIVE":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_ASSET_INVALIDATED"
                    )
                if row["production_review_status"] == "REQUIRED":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_LOCKED_BY_REVIEW"
                    )
                if expected == "NOT_STARTED" and target != "PRECHECK_PASS":
                    raise CandidateAssetRepositoryError(
                        "CANDIDATE_PROMOTION_TRANSITION_INVALID"
                    )
                connection.execute(
                    """
                    UPDATE hardware_candidate_asset
                    SET promotion_status=?,row_version=row_version+1,updated_at=?
                    WHERE candidate_id=?
                    """,
                    (target, now, candidate_key),
                )
                if expected == "NOT_STARTED":
                    connection.execute(
                        """
                        INSERT INTO hardware_asset_promotion(
                            asset_candidate_id,promotion_status,source_id,
                            business_case_id,last_action,error_code,retry_count,
                            created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,0,?,?)
                        """,
                        (
                            candidate_key,
                            target,
                            row["source_id"],
                            row["business_case_id"],
                            target,
                            error_code,
                            now,
                            now,
                        ),
                    )
                    self._insert_event(
                        connection,
                        candidate_id=candidate_key,
                        event_type="PROMOTION_STARTED",
                        old_hash=row["candidate_hash"],
                        new_hash=row["candidate_hash"],
                        run_id=row["generation_run_id"],
                        actor=actor_name,
                        reason=reason_text,
                        created_at=now,
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE hardware_asset_promotion
                        SET promotion_status=?,last_action=?,error_code=?,updated_at=?
                        WHERE asset_candidate_id=?
                        """,
                        (target, target, error_code, now, candidate_key),
                    )
                    if cursor.rowcount != 1:
                        raise CandidateAssetRepositoryError(
                            "CANDIDATE_DATA_INTEGRITY_ERROR"
                        )
                updated = connection.execute(
                    "SELECT * FROM hardware_candidate_asset WHERE candidate_id=?",
                    (candidate_key,),
                ).fetchone()
                result = self._public_candidate(connection, updated)
                result["promotion_record"] = dict(
                    connection.execute(
                        "SELECT * FROM hardware_asset_promotion "
                        "WHERE asset_candidate_id=?",
                        (candidate_key,),
                    ).fetchone()
                )
                return result
        except CandidateAssetRepositoryError:
            raise
        except sqlite3.Error as error:
            raise CandidateAssetRepositoryError(
                "CANDIDATE_DATA_INTEGRITY_ERROR"
            ) from error


__all__ = [
    "ASSET_SCHEMA_VERSION",
    "CandidateAssetRepository",
    "CandidateAssetRepositoryError",
    "candidate_content_hash",
    "candidate_identity",
    "deterministic_evidence_id",
]
