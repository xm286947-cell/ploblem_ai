"""Offline, fail-closed import of trusted legacy R1 Golden Candidates.

Workbench and preview stores remain operational history. This module reads the
legacy Workbench result JSON without changing it, validates its source binding,
and produces a deterministic plan for a staged ``hardware_asset.db`` import.
It never invokes Runtime, a Provider, or Unified Knowledge.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from services.hardware_asset_repository import (
    ASSET_SCHEMA_VERSION,
    CandidateAssetRepository,
    CandidateAssetRepositoryError,
    _canonical_json,
    _utc_now,
    candidate_content_hash,
    candidate_identity,
)
from services.hardware_data_reliability import (
    HardwareDataReliabilityError,
    HardwareDataReliabilityManager,
)


WORKBENCH_ITEM_TABLE = "hardware_r1_batch_item"
WORKBENCH_PROMOTION_TABLE = "hardware_r1_knowledge_promotion"
MIGRATION_MARKER_NAME = "hardware_asset_migration_recovery.json"
_UNRESOLVED = {"OPEN", "NEEDS_REVIEW"}
_PROMOTION_STATES = {
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
_PUBLISHED_STATES = {"PUBLISHED_PENDING_QUERY_BACK", "VERIFIED"}


class LegacyAssetMigrationError(RuntimeError):
    """Stable D4 migration error contract."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LegacyMigrationPaths:
    hardware_db: Path
    workbench_db: Path
    source_root: Path
    target_data_root: Path
    preview_db: Path | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "hardware_db",
            "workbench_db",
            "source_root",
            "target_data_root",
            "preview_db",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, Path(value))

    @classmethod
    def from_legacy_layout(
        cls,
        legacy_root: str | Path,
        target_data_root: str | Path,
    ) -> "LegacyMigrationPaths":
        legacy = Path(legacy_root)
        return cls(
            hardware_db=legacy / "hardware_case_mvp.db",
            workbench_db=legacy / "hardware_case_mvp_r1_workbench.db",
            source_root=legacy / "hardware_case_mvp_sources",
            preview_db=legacy / "hardware_case_mvp_r1_preview.db",
            target_data_root=Path(target_data_root),
        )

    @property
    def asset_db(self) -> Path:
        return self.target_data_root / "db" / "hardware_asset.db"

    @property
    def backup_root(self) -> Path:
        return self.target_data_root / "backups"

    @property
    def recovery_marker(self) -> Path:
        return self.target_data_root / "manifest" / MIGRATION_MARKER_NAME


@dataclass(frozen=True)
class LegacyCandidate:
    item_id: str
    batch_id: str
    business_case_id: str
    source_id: str
    source_ref: str
    updated_at: str
    orchestration_status: str
    knowledge_object: dict[str, Any]
    candidate_hash: str
    review_status: str
    generation_run_id: str | None
    pipeline_version: str
    agent_config_version: str
    knowledge_schema_version: str
    validator_version: str
    result_status: str


@dataclass(frozen=True)
class LegacyCanonicalGroup:
    business_case_id: str
    source_id: str
    canonical: LegacyCandidate
    variants: tuple[LegacyCandidate, ...]
    promotion: Mapping[str, Any] | None
    promotion_records: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class LegacyScan:
    candidates: tuple[LegacyCandidate, ...]
    promotions: tuple[dict[str, Any], ...]
    source_count: int
    source_hash_set: tuple[str, ...]
    formal_references: tuple[dict[str, str], ...]
    skipped_item_count: int


def _read_only_connection(
    path: Path,
    *,
    invalid_code: str = "LEGACY_WORKBENCH_DB_INVALID",
) -> sqlite3.Connection:
    if not path.is_file():
        raise LegacyAssetMigrationError("LEGACY_LAYOUT_NOT_FOUND")
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            connection.close()
            raise LegacyAssetMigrationError(invalid_code)
        return connection
    except LegacyAssetMigrationError:
        raise
    except sqlite3.Error as error:
        raise LegacyAssetMigrationError(invalid_code) from error


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_unresolved(conflict: Mapping[str, Any]) -> bool:
    return (
        str(conflict.get("status") or "").upper() == "OPEN"
        or str(conflict.get("resolution_status") or "").upper() == "NEEDS_REVIEW"
    )


class LegacyCandidatePreflightScanner:
    """Read-only D4 scanner. It never opens the Preview DB."""

    def __init__(self, paths: LegacyMigrationPaths):
        self.paths = paths

    def scan(self) -> LegacyScan:
        if not self.paths.hardware_db.is_file() or not self.paths.workbench_db.is_file():
            raise LegacyAssetMigrationError("LEGACY_LAYOUT_NOT_FOUND")
        candidates: list[LegacyCandidate] = []
        promotions: list[dict[str, Any]] = []
        formal_refs: list[dict[str, str]] = []
        skipped = 0

        try:
            hardware = _read_only_connection(
                self.paths.hardware_db,
                invalid_code="LEGACY_CANDIDATE_SOURCE_UNAVAILABLE",
            )
            workbench = _read_only_connection(self.paths.workbench_db)
        except LegacyAssetMigrationError:
            raise
        try:
            self._validate_hardware_schema(hardware)
            self._validate_workbench_schema(workbench)
            sources = hardware.execute(
                "SELECT source_id,sha256 FROM hardware_case_source_registry "
                "ORDER BY source_id,source_ref"
            ).fetchall()
            source_count = len(sources)
            source_hash_set = tuple(
                sorted({str(row["sha256"]).lower() for row in sources if row["sha256"]})
            )
            if "hardware_r1_source_formal_reference" in _table_names(hardware):
                formal_columns = _table_columns(
                    hardware, "hardware_r1_source_formal_reference"
                )
                if not {
                    "business_case_id", "source_id", "knowledge_id"
                }.issubset(formal_columns):
                    raise LegacyAssetMigrationError(
                        "LEGACY_FORMAL_REFERENCE_INCONSISTENT"
                    )
                formal_refs = [
                    {
                        "business_case_id": str(row["business_case_id"]),
                        "source_id": str(row["source_id"]),
                        "knowledge_id": str(row["knowledge_id"]),
                    }
                    for row in hardware.execute(
                        "SELECT business_case_id,source_id,knowledge_id "
                        "FROM hardware_r1_source_formal_reference "
                        "ORDER BY business_case_id,source_id,knowledge_id"
                    ).fetchall()
                ]

            item_rows = workbench.execute(
                f"SELECT item_id,batch_id,business_case_id,source_id,"
                f"orchestration_status,result_json,updated_at FROM {WORKBENCH_ITEM_TABLE} "
                "ORDER BY item_id"
            ).fetchall()
            for row in item_rows:
                candidate, was_skipped = self._candidate_from_row(hardware, row)
                if candidate is not None:
                    candidates.append(candidate)
                elif was_skipped:
                    skipped += 1

            if WORKBENCH_PROMOTION_TABLE in _table_names(workbench):
                required = {
                    "item_id", "batch_id", "business_case_id", "source_id",
                    "golden_hash", "candidate_id", "status", "last_action",
                    "error_code", "retry_count", "review_status", "knowledge_id",
                    "public_ref", "created_at", "updated_at",
                }
                if not required.issubset(_table_columns(workbench, WORKBENCH_PROMOTION_TABLE)):
                    raise LegacyAssetMigrationError("LEGACY_WORKBENCH_DB_INVALID")
                promotions = [
                    dict(row)
                    for row in workbench.execute(
                        f"SELECT {','.join(sorted(required))} "
                        f"FROM {WORKBENCH_PROMOTION_TABLE} ORDER BY item_id"
                    ).fetchall()
                ]
            return LegacyScan(
                candidates=tuple(candidates),
                promotions=tuple(promotions),
                source_count=source_count,
                source_hash_set=source_hash_set,
                formal_references=tuple(formal_refs),
                skipped_item_count=skipped,
            )
        except LegacyAssetMigrationError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise LegacyAssetMigrationError("LEGACY_WORKBENCH_DB_INVALID") from error
        finally:
            hardware.close()
            workbench.close()

    @staticmethod
    def _validate_hardware_schema(connection: sqlite3.Connection) -> None:
        required = {
            "hardware_case_source_registry": {
                "source_ref", "source_id", "relative_path", "sha256", "source_status",
            },
            "hardware_r1_source_binding": {
                "business_case_id", "source_ref", "source_id", "source_status",
            },
        }
        names = _table_names(connection)
        for table, columns in required.items():
            if table not in names or not columns.issubset(_table_columns(connection, table)):
                raise LegacyAssetMigrationError("LEGACY_CANDIDATE_SOURCE_UNAVAILABLE")

    @staticmethod
    def _validate_workbench_schema(connection: sqlite3.Connection) -> None:
        required = {
            "item_id", "batch_id", "business_case_id", "source_id",
            "orchestration_status", "result_json", "updated_at",
        }
        if (
            WORKBENCH_ITEM_TABLE not in _table_names(connection)
            or not required.issubset(_table_columns(connection, WORKBENCH_ITEM_TABLE))
        ):
            raise LegacyAssetMigrationError("LEGACY_WORKBENCH_DB_INVALID")

    def _candidate_from_row(
        self,
        hardware: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> tuple[LegacyCandidate | None, bool]:
        if str(row["orchestration_status"] or "").upper() == "QUEUED":
            return None, True
        raw_json = row["result_json"]
        if raw_json in (None, ""):
            return None, True
        try:
            result = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError) as error:
            raise LegacyAssetMigrationError("LEGACY_RESULT_JSON_INVALID") from error
        if result is None:
            return None, True
        if not isinstance(result, dict):
            raise LegacyAssetMigrationError("LEGACY_RESULT_JSON_INVALID")
        if (
            result.get("pipeline_status") is not None
            and str(result.get("pipeline_status")).upper()
            != "GOLDEN_PREVIEW_READY"
        ):
            return None, True
        obj = result.get("knowledge_object")
        if not isinstance(obj, dict):
            return None, True
        validation = result.get("evidence_validation")
        if not isinstance(validation, Mapping) or validation.get("status") != "PASS":
            return None, True
        try:
            fabricated_fact_count = int(validation.get("fabricated_fact_count") or 0)
            fabricated_block_count = int(validation.get("fabricated_block_id_count") or 0)
        except (TypeError, ValueError) as error:
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_CONTRACT_INVALID") from error
        if fabricated_fact_count or fabricated_block_count:
            return None, True

        case_id = str(row["business_case_id"] or "").strip()
        source_id = str(row["source_id"] or "").strip().lower()
        if not case_id or len(source_id) != 64:
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_CONTRACT_INVALID")
        identity = obj.get("identity")
        source_fact = obj.get("source_fact")
        evidence = obj.get("evidence")
        review = obj.get("review")
        conflicts = obj.get("conflicts")
        if (
            obj.get("contract_version") != "hardware-case-knowledge-object/v1"
            or not isinstance(identity, Mapping)
            or not isinstance(source_fact, Mapping)
            or str(identity.get("business_case_id") or "").strip() != case_id
            or str(source_fact.get("source_id") or "").strip().lower() != source_id
        ):
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_SOURCE_MISMATCH")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not isinstance(review, Mapping)
            or review.get("object_status") != "CANDIDATE"
            or not isinstance(conflicts, list)
            or any(not isinstance(value, Mapping) for value in conflicts)
        ):
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_CONTRACT_INVALID")
        seen_blocks: set[str] = set()
        for item in evidence:
            if not isinstance(item, Mapping):
                raise LegacyAssetMigrationError("LEGACY_CANDIDATE_CONTRACT_INVALID")
            block_id = str(item.get("block_id") or "").strip()
            if (
                not block_id
                or block_id in seen_blocks
                or not isinstance(item.get("source_locator"), Mapping)
            ):
                raise LegacyAssetMigrationError("LEGACY_CANDIDATE_CONTRACT_INVALID")
            seen_blocks.add(block_id)

        unresolved = any(
            isinstance(value, Mapping) and _is_unresolved(value)
            for value in conflicts
        )
        result_status = str(result.get("status") or "").upper()
        if result_status not in {"PASS", "NEEDS_REVIEW"}:
            return None, True
        if result_status == "PASS" and unresolved:
            raise LegacyAssetMigrationError("LEGACY_REVIEW_STATE_INCONSISTENT")
        decisions = review.get("field_decisions", [])
        if not isinstance(decisions, list) or any(
            not isinstance(item, Mapping)
            or not str(item.get("conflict_id") or "").strip()
            or not str(item.get("decision_source") or "").strip()
            or "selected_value" not in item
            or not str(item.get("reviewer") or "").strip()
            or not str(item.get("reviewed_at") or "").strip()
            for item in decisions
        ):
            raise LegacyAssetMigrationError("LEGACY_REVIEW_STATE_INCONSISTENT")
        if result_status == "NEEDS_REVIEW" and not unresolved:
            raise LegacyAssetMigrationError("LEGACY_REVIEW_STATE_INCONSISTENT")
        review_status = (
            "REQUIRED" if unresolved else "RESOLVED" if decisions else "NOT_REQUIRED"
        )

        source_ref = self._verified_source_ref(
            hardware,
            business_case_id=case_id,
            source_id=source_id,
        )
        try:
            candidate_hash = candidate_content_hash(obj)
        except CandidateAssetRepositoryError as error:
            raise LegacyAssetMigrationError(
                "LEGACY_CANDIDATE_CONTRACT_INVALID"
            ) from error
        runtime = result.get("runtime") if isinstance(result.get("runtime"), Mapping) else {}
        stage_a = runtime.get("stage_a") if isinstance(runtime.get("stage_a"), Mapping) else {}
        stage_b = runtime.get("stage_b") if isinstance(runtime.get("stage_b"), Mapping) else {}
        generation_run_id = str(
            result.get("run_id") or runtime.get("run_id") or ""
        ).strip() or None
        return (
            LegacyCandidate(
                item_id=str(row["item_id"]),
                batch_id=str(row["batch_id"]),
                business_case_id=case_id,
                source_id=source_id,
                source_ref=source_ref,
                updated_at=str(row["updated_at"] or ""),
                orchestration_status=str(row["orchestration_status"] or ""),
                knowledge_object=obj,
                candidate_hash=candidate_hash,
                review_status=review_status,
                generation_run_id=generation_run_id,
                pipeline_version=str(result.get("pipeline_version") or "LEGACY_UNKNOWN"),
                agent_config_version=str(
                    runtime.get("agent_config_version")
                    or stage_b.get("agent_config_version")
                    or stage_a.get("agent_config_version")
                    or "LEGACY_UNKNOWN"
                ),
                knowledge_schema_version=str(obj["contract_version"]),
                validator_version=str(
                    validation.get("validator_version") or "LEGACY_UNKNOWN"
                ),
                result_status=result_status,
            ),
            False,
        )

    def _verified_source_ref(
        self,
        hardware: sqlite3.Connection,
        *,
        business_case_id: str,
        source_id: str,
    ) -> str:
        row = hardware.execute(
            """
            SELECT b.source_ref,b.source_id AS binding_source_id,
                   b.source_status AS binding_status,
                   r.source_id AS registry_source_id,r.relative_path,
                   r.sha256,r.source_status AS registry_status
            FROM hardware_r1_source_binding b
            JOIN hardware_case_source_registry r ON r.source_ref=b.source_ref
            WHERE b.business_case_id=? AND b.source_id=?
            """,
            (business_case_id, source_id),
        ).fetchone()
        if row is None:
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_SOURCE_UNAVAILABLE")
        if (
            str(row["binding_status"] or "") != "ACTIVE"
            or str(row["registry_status"] or "") != "AVAILABLE"
            or str(row["binding_source_id"] or "").lower() != source_id
            or str(row["registry_source_id"] or "").lower() != source_id
            or str(row["sha256"] or "").lower() != source_id
        ):
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_SOURCE_UNAVAILABLE")
        source_root = self.paths.source_root
        if not source_root.is_dir():
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_SOURCE_UNAVAILABLE")

        # HardwareCaseSourceStore's path boundary and metadata contract are
        # reused. Validate the bytes first so its resolver's status repair is
        # never exercised against the read-only legacy Source Registry.
        from services.hardware_case_source_store import (
            HardwareCaseSourceError,
            HardwareCaseSourceStore,
        )

        source_store = HardwareCaseSourceStore(
            self.paths.hardware_db,
            source_root,
            initialize_schema=False,
        )
        try:
            path = source_store._inside_root(str(row["relative_path"]))
        except HardwareCaseSourceError as error:
            raise LegacyAssetMigrationError(
                "LEGACY_CANDIDATE_SOURCE_UNAVAILABLE"
            ) from error
        if not path.is_file():
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_SOURCE_UNAVAILABLE")
        try:
            actual_hash = _hash_file(path)
        except OSError as error:
            raise LegacyAssetMigrationError(
                "LEGACY_CANDIDATE_SOURCE_UNAVAILABLE"
            ) from error
        if actual_hash != source_id:
            raise LegacyAssetMigrationError("LEGACY_CANDIDATE_SOURCE_UNAVAILABLE")
        return str(row["source_ref"])


def select_legacy_canonical_groups(scan: LegacyScan) -> tuple[LegacyCanonicalGroup, ...]:
    """Apply D4's promotion > reviewed > latest machine-result precedence."""
    groups: dict[tuple[str, str], list[LegacyCandidate]] = {}
    for candidate in scan.candidates:
        groups.setdefault(
            (candidate.business_case_id, candidate.source_id), []
        ).append(candidate)
    promotions_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for promotion in scan.promotions:
        key = (
            str(promotion.get("business_case_id") or ""),
            str(promotion.get("source_id") or "").lower(),
        )
        promotions_by_identity.setdefault(key, []).append(promotion)

    output: list[LegacyCanonicalGroup] = []
    for identity in sorted(set(groups) | set(promotions_by_identity)):
        candidates = groups.get(identity, [])
        promotions = promotions_by_identity.get(identity, [])
        if not candidates:
            if promotions:
                raise LegacyAssetMigrationError("LEGACY_PROMOTION_HASH_MISMATCH")
            continue
        promotion_hashes = {
            str(item.get("golden_hash") or "").lower() for item in promotions
        }
        if promotions and any(
            not isinstance(item.get("golden_hash"), str)
            or len(str(item.get("golden_hash"))) != 64
            for item in promotions
        ):
            raise LegacyAssetMigrationError("LEGACY_PROMOTION_HASH_MISMATCH")
        if len(promotion_hashes) > 1:
            raise LegacyAssetMigrationError("LEGACY_PROMOTION_CONFLICT")

        selected_promotion: dict[str, Any] | None = None
        if promotions:
            expected_hash = next(iter(promotion_hashes))
            matching = [item for item in candidates if item.candidate_hash == expected_hash]
            if not matching:
                raise LegacyAssetMigrationError("LEGACY_PROMOTION_HASH_MISMATCH")
            for promotion in promotions:
                status = str(promotion.get("status") or "").upper()
                if status not in _PROMOTION_STATES:
                    raise LegacyAssetMigrationError("LEGACY_PROMOTION_CONFLICT")
                if status in _PUBLISHED_STATES:
                    knowledge_id = str(promotion.get("knowledge_id") or "").strip()
                    if not knowledge_id or not any(
                        ref["business_case_id"] == identity[0]
                        and ref["source_id"] == identity[1]
                        and ref["knowledge_id"] == knowledge_id
                        for ref in scan.formal_references
                    ):
                        raise LegacyAssetMigrationError(
                            "LEGACY_FORMAL_REFERENCE_INCONSISTENT"
                        )
            selected_promotion = sorted(
                promotions,
                key=lambda item: (
                    str(item.get("updated_at") or ""),
                    str(item.get("item_id") or ""),
                ),
                reverse=True,
            )[0]
            preferred = [
                item for item in matching
                if item.item_id == str(selected_promotion.get("item_id") or "")
            ]
            canonical = (preferred or sorted(
                matching,
                key=lambda item: (item.updated_at, item.item_id),
                reverse=True,
            ))[0]
        else:
            reviewed = [
                item for item in candidates if item.review_status == "RESOLVED"
            ]
            if reviewed:
                reviewed_hashes = {item.candidate_hash for item in reviewed}
                if len(reviewed_hashes) > 1:
                    raise LegacyAssetMigrationError(
                        "LEGACY_REVIEWED_CANDIDATE_CONFLICT"
                    )
                canonical = sorted(
                    reviewed,
                    key=lambda item: (item.updated_at, item.item_id),
                    reverse=True,
                )[0]
            else:
                canonical = sorted(
                    candidates,
                    key=lambda item: (item.updated_at, item.item_id),
                    reverse=True,
                )[0]
        output.append(
            LegacyCanonicalGroup(
                business_case_id=identity[0],
                source_id=identity[1],
                canonical=canonical,
                variants=tuple(sorted(candidates, key=lambda item: item.item_id)),
                promotion=selected_promotion,
                promotion_records=tuple(
                    sorted(
                        (dict(item) for item in promotions),
                        key=lambda item: (
                            str(item.get("updated_at") or ""),
                            str(item.get("item_id") or ""),
                        ),
                    )
                ),
            )
        )
    return tuple(output)


class LegacyAssetMigrationRunner:
    """D4 staged, fingerprinted and recoverable legacy Candidate migration."""

    def __init__(
        self,
        paths: LegacyMigrationPaths,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.fault_injector = fault_injector

    def run(self) -> dict[str, Any]:
        self._validate_roots()
        with self._freeze_legacy_writes():
            marker = self._read_marker()
            try:
                scan = LegacyCandidatePreflightScanner(self.paths).scan()
            except LegacyAssetMigrationError as error:
                if marker and marker.get("state") != "COMPLETED":
                    raise LegacyAssetMigrationError(
                        "LEGACY_DATA_CHANGED_DURING_MIGRATION"
                    ) from error
                raise
            groups = select_legacy_canonical_groups(scan)
            fingerprint = self._fingerprint(scan)
            migration_id = self._migration_id(fingerprint)
            self._validate_prior_marker(marker, migration_id, fingerprint)
            if self.paths.asset_db.exists():
                return self._recover_target(
                    migration_id, fingerprint, scan, groups
                )
            self._write_marker(
                self._marker_payload(
                    migration_id,
                    "PREFLIGHT",
                    fingerprint,
                    scan,
                    groups,
                )
            )
            self._inject("PREFLIGHT")

            stage = self._stage_path(migration_id)
            stage_exists = self._stage_files_exist(stage)
            stage_state = (
                self._journal_state(stage, migration_id)
                if stage.is_file() and self._has_journal(stage, migration_id)
                else ""
            )
            if stage.is_file() and stage_state in {"VERIFIED", "ACTIVATING"}:
                self._verify_database(stage, migration_id, fingerprint, scan, groups)
                return self._activate_and_complete(
                    stage, migration_id, fingerprint, scan, groups
                )
            if stage_exists:
                self._quarantine_stage(stage, migration_id)

            try:
                backup_ids = self._create_backups()
                payload = self._marker_payload(
                    migration_id,
                    "BACKUP_READY",
                    fingerprint,
                    scan,
                    groups,
                    backup_ids=backup_ids,
                )
                self._write_marker(payload)
                self._inject("BACKUP_READY")

                payload["state"] = "STAGING"
                self._write_marker(payload)
                self._build_stage(
                    stage,
                    migration_id,
                    fingerprint,
                    scan,
                    groups,
                    backup_ids,
                )
                self._inject("STAGING")
                payload["state"] = "VERIFYING"
                self._write_marker(payload)
                self._set_journal_state(stage, migration_id, "VERIFYING")
                self._inject("VERIFYING")
                self._assert_legacy_fingerprint(fingerprint)
                self._verify_database(stage, migration_id, fingerprint, scan, groups)
                self._set_journal_state(stage, migration_id, "VERIFIED")
                payload["state"] = "VERIFIED"
                self._write_marker(payload)
                self._inject("VERIFIED")
                return self._activate_and_complete(
                    stage, migration_id, fingerprint, scan, groups
                )
            except BaseException as error:
                # Simulated process crashes and interrupts deliberately leave
                # the durable journal/staging file at its last checkpoint.
                if isinstance(error, Exception):
                    code = self._error_code(error)
                    self._mark_failed(migration_id, code)
                raise

    def _validate_roots(self) -> None:
        if (
            not self.paths.hardware_db.is_file()
            or not self.paths.workbench_db.is_file()
        ):
            raise LegacyAssetMigrationError("LEGACY_LAYOUT_NOT_FOUND")
        for directory in (
            self.paths.target_data_root,
            self.paths.asset_db.parent,
            self.paths.recovery_marker.parent,
            self.paths.backup_root,
        ):
            if not directory.is_dir():
                raise LegacyAssetMigrationError("ASSET_MIGRATION_STAGING_FAILED")

    @contextmanager
    def _freeze_legacy_writes(self) -> Iterator[None]:
        connections: list[sqlite3.Connection] = []
        try:
            for path in sorted(
                (self.paths.hardware_db, self.paths.workbench_db),
                key=lambda item: str(item.resolve()),
            ):
                connection = sqlite3.connect(path, timeout=10.0)
                connections.append(connection)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("PRAGMA query_only=ON")
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or str(integrity[0]).lower() != "ok":
                    code = (
                        "LEGACY_CANDIDATE_SOURCE_UNAVAILABLE"
                        if path == self.paths.hardware_db
                        else "LEGACY_WORKBENCH_DB_INVALID"
                    )
                    raise LegacyAssetMigrationError(code)
            yield
        except sqlite3.OperationalError as error:
            raise LegacyAssetMigrationError(
                "LEGACY_DATA_CHANGED_DURING_MIGRATION"
            ) from error
        except sqlite3.Error as error:
            raise LegacyAssetMigrationError(
                "LEGACY_DATA_CHANGED_DURING_MIGRATION"
            ) from error
        finally:
            for connection in reversed(connections):
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    def _fingerprint(self, scan: LegacyScan) -> dict[str, Any]:
        try:
            value = {
                "legacy_hardware_db_sha256": _hash_file(self.paths.hardware_db),
                "legacy_workbench_db_sha256": _hash_file(self.paths.workbench_db),
                "legacy_hardware_wal_sha256": self._optional_file_hash(
                    Path(str(self.paths.hardware_db) + "-wal")
                ),
                "legacy_workbench_wal_sha256": self._optional_file_hash(
                    Path(str(self.paths.workbench_db) + "-wal")
                ),
                "source_count": scan.source_count,
                "source_hash_set": list(scan.source_hash_set),
                "eligible_item_count": len(scan.candidates),
                "promotion_record_count": len(scan.promotions),
                "formal_reference_count": len(scan.formal_references),
            }
        except OSError as error:
            raise LegacyAssetMigrationError(
                "LEGACY_DATA_CHANGED_DURING_MIGRATION"
            ) from error
        value["fingerprint_sha256"] = hashlib.sha256(
            _canonical_json(value).encode("utf-8")
        ).hexdigest()
        return value

    @staticmethod
    def _optional_file_hash(path: Path) -> str | None:
        return _hash_file(path) if path.is_file() else None

    @staticmethod
    def _migration_id(fingerprint: Mapping[str, Any]) -> str:
        return "HAM-" + str(fingerprint["fingerprint_sha256"])[:32]

    def _read_marker(self) -> dict[str, Any] | None:
        path = self.paths.recovery_marker
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_RECOVERY_REQUIRED"
            ) from error
        if not isinstance(value, dict) or value.get("contract_version") != "hardware-asset-migration-recovery/v1":
            raise LegacyAssetMigrationError("ASSET_MIGRATION_RECOVERY_REQUIRED")
        return value

    @staticmethod
    def _validate_prior_marker(
        marker: Mapping[str, Any] | None,
        migration_id: str,
        fingerprint: Mapping[str, Any],
    ) -> None:
        if marker is None:
            return
        if marker.get("migration_id") != migration_id:
            raise LegacyAssetMigrationError(
                "LEGACY_DATA_CHANGED_DURING_MIGRATION"
            )
        if marker.get("fingerprint") != dict(fingerprint):
            raise LegacyAssetMigrationError(
                "LEGACY_DATA_CHANGED_DURING_MIGRATION"
            )

    def _marker_payload(
        self,
        migration_id: str,
        state: str,
        fingerprint: Mapping[str, Any],
        scan: LegacyScan,
        groups: tuple[LegacyCanonicalGroup, ...],
        *,
        backup_ids: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        prior = self._read_marker() or {}
        return {
            "contract_version": "hardware-asset-migration-recovery/v1",
            "migration_id": migration_id,
            "state": state,
            "fingerprint": dict(fingerprint),
            "legacy_hardware_db": str(self.paths.hardware_db.resolve()),
            "legacy_workbench_db": str(self.paths.workbench_db.resolve()),
            "asset_db": str(self.paths.asset_db.resolve()),
            "staging_db": str(self._stage_path(migration_id).resolve()),
            "hardware_backup_id": (backup_ids or {}).get(
                "hardware", prior.get("hardware_backup_id")
            ),
            "workbench_backup_id": (backup_ids or {}).get(
                "workbench", prior.get("workbench_backup_id")
            ),
            "eligible_item_count": len(scan.candidates),
            "canonical_group_count": len(groups),
            "promotion_record_count": len(scan.promotions),
            "formal_reference_count": len(scan.formal_references),
            "skipped_item_count": scan.skipped_item_count,
            "updated_at": _utc_now(),
        }

    def _write_marker(self, payload: Mapping[str, Any]) -> None:
        target = self.paths.recovery_marker
        temp = target.with_name(target.name + ".tmp-" + uuid4().hex)
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
            self._fsync_directory(target.parent)
        except OSError as error:
            try:
                temp.unlink()
            except OSError:
                pass
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_STAGING_FAILED"
            ) from error

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _create_backups(self) -> dict[str, str]:
        try:
            hardware = HardwareDataReliabilityManager(
                self.paths.hardware_db,
                backup_root=self.paths.backup_root / "legacy-hardware",
            ).create_backup(reason="HARDWARE_ASSET_LEGACY_MIGRATION")
            workbench = HardwareDataReliabilityManager(
                self.paths.workbench_db,
                backup_root=self.paths.backup_root / "legacy-workbench",
            ).create_backup(reason="HARDWARE_ASSET_LEGACY_MIGRATION")
        except (HardwareDataReliabilityError, OSError) as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_STAGING_FAILED"
            ) from error
        return {
            "hardware": str(hardware["backup_id"]),
            "workbench": str(workbench["backup_id"]),
        }

    def _stage_path(self, migration_id: str) -> Path:
        return self.paths.asset_db.with_name(
            self.paths.asset_db.name + ".migrating-" + migration_id
        )

    def _quarantine_stage(self, stage: Path, migration_id: str) -> None:
        quarantine = stage.with_name(
            stage.name + ".quarantine-" + uuid4().hex
        )
        try:
            for suffix in ("", "-wal", "-shm", "-journal"):
                source = Path(str(stage) + suffix)
                if source.exists():
                    os.replace(source, Path(str(quarantine) + suffix))
        except OSError as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_RECOVERY_REQUIRED"
            ) from error

    @staticmethod
    def _stage_files_exist(stage: Path) -> bool:
        return any(
            Path(str(stage) + suffix).exists()
            for suffix in ("", "-wal", "-shm", "-journal")
        )

    def _build_stage(
        self,
        stage: Path,
        migration_id: str,
        fingerprint: Mapping[str, Any],
        scan: LegacyScan,
        groups: tuple[LegacyCanonicalGroup, ...],
        backup_ids: Mapping[str, str],
    ) -> None:
        repository = CandidateAssetRepository(stage)
        try:
            repository.initialize()
            with closing(repository._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                now = _utc_now()
                connection.execute(
                    """
                    INSERT INTO hardware_asset_migration(
                        migration_id,state,fingerprint_json,
                        legacy_hardware_db_sha256,legacy_workbench_db_sha256,
                        source_count,source_hash_set_json,eligible_item_count,
                        promotion_record_count,formal_reference_count,
                        hardware_backup_id,workbench_backup_id,skipped_item_count,
                        error_code,created_at,updated_at,completed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,NULL)
                    """,
                    (
                        migration_id,
                        "STAGING",
                        _canonical_json(dict(fingerprint)),
                        fingerprint["legacy_hardware_db_sha256"],
                        fingerprint["legacy_workbench_db_sha256"],
                        int(fingerprint["source_count"]),
                        _canonical_json(fingerprint["source_hash_set"]),
                        int(fingerprint["eligible_item_count"]),
                        int(fingerprint["promotion_record_count"]),
                        int(fingerprint["formal_reference_count"]),
                        backup_ids["hardware"],
                        backup_ids["workbench"],
                        scan.skipped_item_count,
                        now,
                        now,
                    ),
                )
                connection.commit()

            for group in groups:
                canonical = group.canonical
                repository.create_or_commit_candidate(
                    business_case_id=canonical.business_case_id,
                    source_id=canonical.source_id,
                    source_ref=canonical.source_ref,
                    knowledge_object=canonical.knowledge_object,
                    generation_run_id=canonical.generation_run_id,
                    pipeline_version=canonical.pipeline_version,
                    agent_config_version=canonical.agent_config_version,
                    knowledge_schema_version=canonical.knowledge_schema_version,
                    validator_version=canonical.validator_version,
                )
                self._import_group_history(repository, migration_id, group)
        except LegacyAssetMigrationError:
            raise
        except (CandidateAssetRepositoryError, OSError, sqlite3.Error, ValueError) as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_STAGING_FAILED"
            ) from error

    def _import_group_history(
        self,
        repository: CandidateAssetRepository,
        migration_id: str,
        group: LegacyCanonicalGroup,
    ) -> None:
        candidate_id = candidate_identity(
            group.business_case_id, group.source_id
        )
        now = _utc_now()
        with closing(repository._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE hardware_candidate_asset SET production_review_status=? "
                "WHERE candidate_id=?",
                (group.canonical.review_status, candidate_id),
            )
            for variant in group.variants:
                decisions = variant.knowledge_object.get("review", {}).get(
                    "field_decisions", []
                )
                for index, decision in enumerate(decisions):
                    before_hash = decision.get("before_candidate_hash") or None
                    if before_hash is not None and (
                        not isinstance(before_hash, str)
                        or len(before_hash) != 64
                        or any(
                            character not in "0123456789abcdefABCDEF"
                            for character in before_hash
                        )
                    ):
                        raise LegacyAssetMigrationError(
                            "LEGACY_CANDIDATE_CONTRACT_INVALID"
                        )
                    record = {
                        "legacy_batch_id": variant.batch_id,
                        "legacy_item_id": variant.item_id,
                        "legacy_candidate_hash": variant.candidate_hash,
                        "conflict_id": decision["conflict_id"],
                        "decision_source": decision["decision_source"],
                        "selected_value": decision["selected_value"],
                        "reviewer": decision["reviewer"],
                        "reviewed_at": decision["reviewed_at"],
                        "legacy_before_hash_unavailable": before_hash is None,
                        "legacy_before_hash_status": (
                            "LEGACY_BEFORE_HASH_UNAVAILABLE"
                            if before_hash is None
                            else "AVAILABLE"
                        ),
                    }
                    review_id = "HCRV-" + hashlib.sha256(
                        _canonical_json(
                            [migration_id, variant.item_id, index, record]
                        ).encode("utf-8")
                    ).hexdigest()[:32]
                    connection.execute(
                        """
                        INSERT INTO hardware_candidate_review(
                            review_id,candidate_id,before_candidate_hash,
                            after_candidate_hash,reviewer,reason,
                            review_record_json,created_at
                        ) VALUES(?,?,?, ?,?,?,?,?)
                        """,
                        (
                            review_id,
                            candidate_id,
                            before_hash,
                            variant.candidate_hash,
                            str(decision["reviewer"]),
                            str(decision["decision_source"]),
                            _canonical_json(record),
                            str(decision["reviewed_at"]),
                        ),
                    )
                matching_promotion = (
                    dict(group.promotion)
                    if group.promotion
                    and str(group.promotion.get("item_id") or "") == variant.item_id
                    else None
                )
                outcome = {
                    "result": (
                        "CANONICAL"
                        if variant.item_id == group.canonical.item_id
                        else "VARIANT_AUDITED"
                    ),
                    "promotion": matching_promotion,
                    "promotion_provenance": (
                        list(group.promotion_records)
                        if variant.item_id == group.canonical.item_id
                        else []
                    ),
                }
                connection.execute(
                    """
                    INSERT INTO hardware_candidate_legacy_origin(
                        migration_id,legacy_batch_id,legacy_item_id,
                        business_case_id,source_id,candidate_id,
                        legacy_candidate_hash,selected_as_canonical,
                        migration_outcome,legacy_updated_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        migration_id,
                        variant.batch_id,
                        variant.item_id,
                        variant.business_case_id,
                        variant.source_id,
                        candidate_id,
                        variant.candidate_hash,
                        int(variant.item_id == group.canonical.item_id),
                        _canonical_json(outcome),
                        variant.updated_at,
                        now,
                    ),
                )
            if group.promotion:
                self._import_promotion(connection, candidate_id, group, now)
            connection.commit()

    @staticmethod
    def _import_promotion(
        connection: sqlite3.Connection,
        candidate_id: str,
        group: LegacyCanonicalGroup,
        now: str,
    ) -> None:
        promotion = dict(group.promotion or {})
        status = str(promotion.get("status") or "").upper()
        item_id = str(promotion.get("item_id") or "")
        candidate = next(
            (item for item in group.variants if item.item_id == item_id),
            group.canonical,
        )
        created_at = str(promotion.get("created_at") or now)
        updated_at = str(promotion.get("updated_at") or now)
        connection.execute(
            "UPDATE hardware_candidate_asset SET promotion_status=?,row_version=row_version+1 "
            "WHERE candidate_id=?",
            (status, candidate_id),
        )
        connection.execute(
            """
            INSERT INTO hardware_asset_promotion(
                asset_candidate_id,knowledge_candidate_id,knowledge_id,public_ref,
                promotion_status,formal_review_status,source_id,business_case_id,
                last_action,error_code,retry_count,origin_batch_id,origin_item_id,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                candidate_id,
                promotion.get("candidate_id"),
                promotion.get("knowledge_id"),
                promotion.get("public_ref"),
                status,
                promotion.get("review_status"),
                candidate.source_id,
                candidate.business_case_id,
                str(promotion.get("last_action") or status),
                promotion.get("error_code"),
                int(promotion.get("retry_count") or 0),
                promotion.get("batch_id"),
                promotion.get("item_id"),
                created_at,
                updated_at,
            ),
        )

    def _set_journal_state(
        self,
        db_path: Path,
        migration_id: str,
        state: str,
        *,
        error_code: str | None = None,
    ) -> None:
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE hardware_asset_migration
                SET state=?,error_code=?,updated_at=?,
                    completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE completed_at END
                WHERE migration_id=?
                """,
                (state, error_code, _utc_now(), state, _utc_now(), migration_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise LegacyAssetMigrationError(
                    "ASSET_MIGRATION_RECOVERY_REQUIRED"
                )
            connection.commit()

    def _verify_database(
        self,
        db_path: Path,
        migration_id: str,
        fingerprint: Mapping[str, Any],
        scan: LegacyScan,
        groups: tuple[LegacyCanonicalGroup, ...],
    ) -> None:
        try:
            repository = CandidateAssetRepository(db_path)
            if repository.schema_version() != ASSET_SCHEMA_VERSION:
                raise LegacyAssetMigrationError("ASSET_MIGRATION_VERIFY_FAILED")
            with closing(repository._connect()) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or str(integrity[0]).lower() != "ok":
                    raise LegacyAssetMigrationError(
                        "ASSET_MIGRATION_VERIFY_FAILED"
                    )
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise LegacyAssetMigrationError(
                        "ASSET_MIGRATION_VERIFY_FAILED"
                    )
                journal = connection.execute(
                    "SELECT * FROM hardware_asset_migration WHERE migration_id=?",
                    (migration_id,),
                ).fetchone()
                if (
                    journal is None
                    or json.loads(journal["fingerprint_json"]) != dict(fingerprint)
                ):
                    raise LegacyAssetMigrationError(
                        "ASSET_MIGRATION_VERIFY_FAILED"
                    )
                counts = {
                    "hardware_candidate_asset": len(groups),
                    "hardware_candidate_legacy_origin": len(scan.candidates),
                    "hardware_candidate_review": sum(
                        len(
                            candidate.knowledge_object.get("review", {}).get(
                                "field_decisions", []
                            )
                        )
                        for candidate in scan.candidates
                    ),
                    "hardware_candidate_evidence_ref": sum(
                        len(group.canonical.knowledge_object.get("evidence", []))
                        for group in groups
                    ),
                    "hardware_asset_promotion": sum(
                        1 for group in groups if group.promotion
                    ),
                }
                for table, expected in counts.items():
                    actual = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                    )
                    if actual != expected:
                        raise LegacyAssetMigrationError(
                            "ASSET_MIGRATION_VERIFY_FAILED"
                        )
                if db_path.stat().st_size <= 0:
                    raise LegacyAssetMigrationError(
                        "ASSET_MIGRATION_VERIFY_FAILED"
                    )
        except LegacyAssetMigrationError:
            raise
        except (OSError, sqlite3.Error, CandidateAssetRepositoryError, ValueError) as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_VERIFY_FAILED"
            ) from error

    def _assert_legacy_fingerprint(self, expected: Mapping[str, Any]) -> None:
        try:
            current_scan = LegacyCandidatePreflightScanner(self.paths).scan()
            actual = self._fingerprint(current_scan)
        except LegacyAssetMigrationError as error:
            raise LegacyAssetMigrationError(
                "LEGACY_DATA_CHANGED_DURING_MIGRATION"
            ) from error
        if dict(actual) != dict(expected):
            raise LegacyAssetMigrationError(
                "LEGACY_DATA_CHANGED_DURING_MIGRATION"
            )

    def _recover_target(
        self,
        migration_id: str,
        fingerprint: Mapping[str, Any],
        scan: LegacyScan,
        groups: tuple[LegacyCanonicalGroup, ...],
    ) -> dict[str, Any]:
        self._verify_database(
            self.paths.asset_db, migration_id, fingerprint, scan, groups
        )
        self._assert_legacy_fingerprint(fingerprint)
        state = self._journal_state(self.paths.asset_db, migration_id)
        if state not in {"ACTIVATING", "COMPLETED"}:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_RECOVERY_REQUIRED"
            )
        if state != "COMPLETED":
            self._set_journal_state(
                self.paths.asset_db, migration_id, "COMPLETED"
            )
        stage = self._stage_path(migration_id)
        try:
            if stage.exists() and os.path.samefile(stage, self.paths.asset_db):
                stage.unlink()
        except OSError:
            pass
        marker = self._marker_payload(
            migration_id, "COMPLETED", fingerprint, scan, groups
        )
        self._write_marker(marker)
        self._inject("COMPLETED")
        return self._success_result(migration_id, fingerprint, scan, groups)

    def _activate_and_complete(
        self,
        stage: Path,
        migration_id: str,
        fingerprint: Mapping[str, Any],
        scan: LegacyScan,
        groups: tuple[LegacyCanonicalGroup, ...],
    ) -> dict[str, Any]:
        target = self.paths.asset_db
        if target.exists():
            return self._recover_target(
                migration_id, fingerprint, scan, groups
            )
        if any(Path(str(stage) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise LegacyAssetMigrationError("ASSET_MIGRATION_VERIFY_FAILED")
        self._set_journal_state(stage, migration_id, "ACTIVATING")
        marker = self._marker_payload(
            migration_id, "ACTIVATING", fingerprint, scan, groups
        )
        self._write_marker(marker)
        self._inject("ACTIVATING")
        try:
            # A hard-link publish makes the complete staged inode visible in a
            # single operation and, unlike replace, cannot overwrite a DB that
            # appeared concurrently. Stage and target share the same directory.
            os.link(stage, target)
            self._fsync_directory(target.parent)
            self._inject("AFTER_ACTIVATION_BEFORE_COMPLETION")
            self._set_journal_state(target, migration_id, "COMPLETED")
            try:
                stage.unlink()
            except OSError:
                pass
        except FileExistsError as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_RECOVERY_REQUIRED"
            ) from error
        except LegacyAssetMigrationError:
            raise
        except OSError as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_ACTIVATE_FAILED"
            ) from error
        marker["state"] = "COMPLETED"
        marker["updated_at"] = _utc_now()
        self._write_marker(marker)
        self._inject("COMPLETED")
        return self._success_result(migration_id, fingerprint, scan, groups)

    def _journal_state(self, db_path: Path, migration_id: str) -> str:
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                row = connection.execute(
                    "SELECT state FROM hardware_asset_migration WHERE migration_id=?",
                    (migration_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_RECOVERY_REQUIRED"
            ) from error
        if row is None:
            raise LegacyAssetMigrationError(
                "ASSET_MIGRATION_RECOVERY_REQUIRED"
            )
        return str(row[0])

    @staticmethod
    def _has_journal(db_path: Path, migration_id: str) -> bool:
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                return connection.execute(
                    "SELECT 1 FROM hardware_asset_migration WHERE migration_id=?",
                    (migration_id,),
                ).fetchone() is not None
        except sqlite3.Error:
            return False

    def _mark_failed(self, migration_id: str, code: str) -> None:
        if self.paths.asset_db.is_file() and self._has_journal(
            self.paths.asset_db, migration_id
        ):
            try:
                if self._journal_state(self.paths.asset_db, migration_id) in {
                    "ACTIVATING", "COMPLETED"
                }:
                    return
            except LegacyAssetMigrationError:
                return
        for path in (self._stage_path(migration_id), self.paths.asset_db):
            if path.is_file():
                try:
                    self._set_journal_state(
                        path, migration_id, "FAILED", error_code=code
                    )
                    break
                except LegacyAssetMigrationError:
                    continue
        marker = self._read_marker()
        if marker and marker.get("migration_id") == migration_id:
            marker["state"] = "FAILED"
            marker["error_code"] = code
            marker["updated_at"] = _utc_now()
            self._write_marker(marker)

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, LegacyAssetMigrationError):
            return error.code
        return "ASSET_MIGRATION_STAGING_FAILED"

    def _success_result(
        self,
        migration_id: str,
        fingerprint: Mapping[str, Any],
        scan: LegacyScan,
        groups: tuple[LegacyCanonicalGroup, ...],
    ) -> dict[str, Any]:
        return {
            "migration_id": migration_id,
            "state": "COMPLETED",
            "asset_db": str(self.paths.asset_db),
            "eligible_item_count": len(scan.candidates),
            "canonical_group_count": len(groups),
            "promotion_record_count": len(scan.promotions),
            "formal_reference_count": len(scan.formal_references),
            "skipped_item_count": scan.skipped_item_count,
            "fingerprint_sha256": fingerprint["fingerprint_sha256"],
            "status": "PASS",
        }

    def _inject(self, checkpoint: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(checkpoint)


__all__ = [
    "LegacyAssetMigrationError",
    "LegacyCandidate",
    "LegacyCandidatePreflightScanner",
    "LegacyCanonicalGroup",
    "LegacyMigrationPaths",
    "LegacyScan",
    "select_legacy_canonical_groups",
]
