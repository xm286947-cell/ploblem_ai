"""One-shot initialization for the clean Quality Capability P0 database."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quality_knowledge.standard_fields.repository import StandardFieldRepository
from quality_knowledge.standard_fields.service import PlcSeedValidationError, StandardFieldService


P0_SCHEMA_VERSION = "2.1.0"
READY_STATE = "READY"
BLOCKED_STATE = "INITIALIZATION_BLOCKED"


class P0InitializationError(RuntimeError):
    """Initialization error with a stable diagnostic payload."""

    def __init__(self, code: str, diagnostic: "InitializationDiagnostic"):
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class InitializationDiagnostic:
    diagnostic_id: str
    initialization_state: str
    blocker_code: str | None
    artifact_path: str | None
    expected_hash: str | None
    actual_hash: str | None
    recommendation: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class P0Initializer:
    """Creates a new P0 database and verifies its immutable seed lineage."""

    def __init__(
        self,
        *,
        manifest_path: str | Path,
        plc_seed_path: str | Path,
        schema_path: str | Path | None = None,
    ):
        self.manifest_path = Path(manifest_path)
        self.plc_seed_path = Path(plc_seed_path)
        self.schema_path = Path(schema_path) if schema_path else Path(__file__).with_name("schema.sql")

    def initialize(
        self,
        db_path: str | Path,
        *,
        legacy_backup_source: str | Path | None = None,
        backup_directory: str | Path | None = None,
    ) -> dict[str, Any]:
        target = Path(db_path)
        manifest = self._load_manifest()
        expected_hash = self._plc_seed_hash(manifest)
        actual_hash = self._sha256_file(self.plc_seed_path) if self.plc_seed_path.exists() else None
        if actual_hash != expected_hash:
            raise self._blocked(
                target,
                "PLC_SEED_HASH_MISMATCH",
                artifact_path=self.plc_seed_path,
                expected_hash=expected_hash,
                actual_hash=actual_hash,
                recommendation="恢复与 Release Manifest 一致的 plc_fields.yaml 后重新初始化。",
            )
        self._verify_analysis_seed_artifacts(target, manifest)
        if target.exists() and not self._is_p0_database(target):
            raise self._blocked(
                target,
                "P0_TARGET_MUST_BE_EMPTY_OR_P0_DATABASE",
                artifact_path=target,
                expected_hash=None,
                actual_hash=None,
                recommendation="请选择不存在的 P0 新库路径；不得以旧验证库作为 P0 目标。",
            )

        backup = self._backup_legacy_file(legacy_backup_source, backup_directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        is_new = not target.exists()
        connection = self._connect(target)
        run_id = f"INIT-{uuid.uuid4().hex}"
        try:
            if is_new:
                self._create_schema(connection)
                database_instance_id = f"P0DB-{uuid.uuid4().hex}"
                connection.execute(
                    """INSERT INTO system_database_metadata(
                            database_instance_id, schema_version, initialization_state
                        ) VALUES (?, ?, 'NEW')""",
                    (database_instance_id, P0_SCHEMA_VERSION),
                )
                connection.commit()
            metadata = self._metadata(connection)
            self._start_run(connection, run_id, metadata["database_instance_id"], backup)

            if metadata["initialization_state"] == READY_STATE:
                self._record_seeds(connection, manifest)
                self._verify_ready_connection(connection, manifest)
                self._finish_run(connection, run_id, READY_STATE, "ALREADY_APPLIED", {"backup": backup})
                return self._result(connection, "ALREADY_APPLIED", backup)

            self._set_state(connection, "LEGACY_BACKUP_VERIFIED" if backup["status"] == "VERIFIED" else "LEGACY_BACKUP_NOT_APPLICABLE")
            self._record_seeds(connection, manifest)
            self._set_state(connection, "STATIC_SEEDS_APPLIED")
            self._seed_static_config(connection)
            field_service = StandardFieldService(
                StandardFieldRepository(target),
                self.plc_seed_path,
                technical_field_exclusions=manifest.get("technical_field_exclusions", {}),
            )
            seed_result = field_service.seed_plc_mapping_and_catalog(connection, expected_hash=expected_hash)
            self._set_state(connection, "PLC_MAPPING_ACTIVE")
            self._set_state(connection, "STANDARD_FIELD_CATALOG_ACTIVE")
            self._verify_ready_connection(connection, manifest)
            self._set_state(connection, "INTEGRITY_VERIFIED")
            self._set_state(connection, READY_STATE)
            self._finish_run(connection, run_id, READY_STATE, "APPLIED", {"backup": backup, "seed": seed_result})
            return self._result(connection, "APPLIED", backup)
        except P0InitializationError:
            raise
        except (sqlite3.Error, PlcSeedValidationError, ValueError, RuntimeError) as error:
            diagnostic = self._diagnostic(
                BLOCKED_STATE,
                getattr(error, "args", ["INITIALIZATION_FAILED"])[0],
                self.plc_seed_path,
                expected_hash,
                actual_hash,
                "修复初始化诊断后使用同一入口重试；未 READY 的数据库不会被业务 Repository 使用。",
            )
            self._mark_blocked(connection, run_id, diagnostic, backup)
            raise P0InitializationError(diagnostic.blocker_code or "INITIALIZATION_FAILED", diagnostic) from error
        finally:
            connection.close()

    def verify_ready(self, db_path: str | Path) -> dict[str, Any]:
        target = Path(db_path)
        manifest = self._load_manifest()
        if not target.exists() or not self._is_p0_database(target):
            raise self._blocked(
                target,
                "P0_DATABASE_NOT_INITIALIZED",
                artifact_path=target,
                expected_hash=None,
                actual_hash=None,
                recommendation="先通过 P0Initializer.initialize 创建干净 P0 数据库。",
            )
        with self._connect(target) as connection:
            try:
                self._verify_ready_connection(connection, manifest)
            except RuntimeError as error:
                raise self._blocked(
                    target,
                    str(error),
                    artifact_path=target,
                    expected_hash=self._plc_seed_hash(manifest),
                    actual_hash=self._sha256_file(self.plc_seed_path) if self.plc_seed_path.exists() else None,
                    recommendation="依据初始化诊断修复 P0 基线后重新初始化或恢复干净新库。",
                ) from error
            return self._result(connection, "READY", {"status": "NOT_APPLICABLE"})

    def _load_manifest(self) -> dict[str, Any]:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            diagnostic = self._diagnostic(
                BLOCKED_STATE,
                "SEED_MANIFEST_INVALID",
                self.manifest_path,
                None,
                None,
                "修复 p0_seed_manifest.json 后重试。",
            )
            raise P0InitializationError("SEED_MANIFEST_INVALID", diagnostic) from error
        if manifest.get("schema_version") != P0_SCHEMA_VERSION or not isinstance(manifest.get("seeds"), list):
            diagnostic = self._diagnostic(
                BLOCKED_STATE,
                "SEED_MANIFEST_CONTRACT_INVALID",
                self.manifest_path,
                P0_SCHEMA_VERSION,
                str(manifest.get("schema_version")),
                "恢复 P1 Schema 2.1.0 对应的 Seed Manifest。",
            )
            raise P0InitializationError("SEED_MANIFEST_CONTRACT_INVALID", diagnostic)
        exclusions = manifest.get("technical_field_exclusions", {})
        if not isinstance(exclusions, dict) or not all(
            isinstance(key, str) and isinstance(value, dict) for key, value in exclusions.items()
        ):
            diagnostic = self._diagnostic(
                BLOCKED_STATE,
                "SEED_MANIFEST_TECHNICAL_EXCLUSIONS_INVALID",
                self.manifest_path,
                None,
                None,
                "为技术列排除提供路径到处理规则的对象映射。",
            )
            raise P0InitializationError("SEED_MANIFEST_TECHNICAL_EXCLUSIONS_INVALID", diagnostic)
        return manifest

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(self.schema_path.read_text(encoding="utf-8"))
        connection.commit()

    def _is_p0_database(self, path: Path) -> bool:
        try:
            with self._connect(path) as connection:
                row = connection.execute(
                    "SELECT schema_version FROM system_database_metadata LIMIT 1"
                ).fetchone()
                return bool(row and row["schema_version"] == P0_SCHEMA_VERSION)
        except sqlite3.Error:
            return False

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _backup_legacy_file(
        self,
        source: str | Path | None,
        backup_directory: str | Path | None,
    ) -> dict[str, Any]:
        if source is None:
            return {"status": "NOT_APPLICABLE"}
        source_path = Path(source)
        if not source_path.exists():
            return {"status": "NOT_APPLICABLE", "source_path": str(source_path)}
        source_hash = self._sha256_file(source_path)
        directory = Path(backup_directory) if backup_directory else source_path.parent / "backup" / "legacy_validation"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = directory / f"{source_path.stem}.{timestamp}.{source_hash[:8]}.backup{source_path.suffix}"
        shutil.copyfile(source_path, backup_path)
        backup_hash = self._sha256_file(backup_path)
        if backup_hash != source_hash:
            raise RuntimeError("LEGACY_BACKUP_HASH_MISMATCH")
        return {
            "status": "VERIFIED",
            "source_path": str(source_path.resolve()),
            "backup_path": str(backup_path.resolve()),
            "source_sha256": source_hash,
            "backup_sha256": backup_hash,
        }

    @staticmethod
    def _plc_seed_hash(manifest: dict[str, Any]) -> str:
        for seed in manifest["seeds"]:
            if seed.get("seed_key") == "PLC_MAPPING_SEED_V1" and seed.get("version") == 1:
                return str(seed["sha256"])
        raise ValueError("PLC_MAPPING_SEED_MANIFEST_MISSING")

    def _record_seeds(self, connection: sqlite3.Connection, manifest: dict[str, Any]) -> None:
        existing = {
            (row["seed_key"], row["version"]): row["sha256"]
            for row in connection.execute("SELECT seed_key, version, sha256 FROM seed_manifest")
        }
        for seed in manifest["seeds"]:
            key = (seed["seed_key"], seed["version"])
            old_hash = existing.get(key)
            if old_hash and old_hash != seed["sha256"]:
                raise RuntimeError("SEED_MANIFEST_HASH_CONFLICT")
            if old_hash is None:
                connection.execute(
                    """INSERT INTO seed_manifest(seed_key, version, sha256, release_id)
                       VALUES (?, ?, ?, ?)""",
                    (seed["seed_key"], seed["version"], seed["sha256"], manifest["release_id"]),
                )
        manifest_hash = self._manifest_content_hash(manifest)
        manifest_key = ("P0_SEED_MANIFEST_V1", 1)
        old_manifest_hash = existing.get(manifest_key)
        if old_manifest_hash and old_manifest_hash != manifest_hash:
            raise RuntimeError("SEED_MANIFEST_HASH_CONFLICT")
        if old_manifest_hash is None:
            connection.execute(
                """INSERT INTO seed_manifest(seed_key, version, sha256, release_id)
                   VALUES ('P0_SEED_MANIFEST_V1', 1, ?, ?)""",
                (manifest_hash, manifest["release_id"]),
            )
        connection.commit()

    @staticmethod
    def _seed_static_config(connection: sqlite3.Connection) -> None:
        from quality_knowledge.taxonomy.seed import TAXONOMY_VERSION_ID, TERMS, content_hash
        from quality_knowledge.p0.prompt_seed import build_prompt_seeds, build_scoring_seed, canonical_json

        connection.execute(
            """INSERT OR IGNORE INTO product_config(
                    product_id, product_code, product_name, product_type, metadata_json
                ) VALUES ('PRODUCT-PLC', 'PLC', 'PLC', 'EMBEDDED_SOFTWARE', '{}')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO analysis_taxonomy_version(
                    taxonomy_version_id, version_no, status, content_hash, activated_at
                ) VALUES (?, 1, 'ACTIVE', ?, CURRENT_TIMESTAMP)""",
            (TAXONOMY_VERSION_ID, content_hash()),
        )
        for taxonomy_type, codes in TERMS.items():
            for order, code in enumerate(codes):
                connection.execute(
                    """INSERT OR IGNORE INTO analysis_taxonomy_term(
                            term_id, taxonomy_version_id, taxonomy_type, code,
                            label_zh, description_zh, sort_order
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"{TAXONOMY_VERSION_ID}:{taxonomy_type}:{code}",
                        TAXONOMY_VERSION_ID,
                        taxonomy_type,
                        code,
                        code,
                        code,
                        order,
                    ),
                )
        for seed in build_prompt_seeds():
            existing = connection.execute(
                """SELECT content_hash FROM analysis_prompt_version
                     WHERE stage = ? AND version_no = ?""",
                (seed["stage"], seed["version_no"]),
            ).fetchone()
            if existing is not None and existing["content_hash"] != seed["content_hash"]:
                raise RuntimeError("P0_PROMPT_SEED_HASH_CONFLICT")
            connection.execute(
                """INSERT OR IGNORE INTO analysis_prompt_version(
                        prompt_version_id, stage, version_no, status, prompt_text,
                        content_hash, output_contract_json, taxonomy_version_id,
                        model_params_json, activated_at
                    ) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    seed["prompt_version_id"],
                    seed["stage"],
                    seed["version_no"],
                    seed["prompt_text"],
                    seed["content_hash"],
                    canonical_json(seed["output_contract"]),
                    seed["taxonomy_version_id"],
                    canonical_json(seed["model_params"]),
                ),
            )
        scoring = build_scoring_seed()
        existing_scoring = connection.execute(
            "SELECT content_hash FROM insight_scoring_version WHERE version_no = ?",
            (scoring["version_no"],),
        ).fetchone()
        if existing_scoring is not None and existing_scoring["content_hash"] != scoring["content_hash"]:
            raise RuntimeError("P0_SCORING_SEED_HASH_CONFLICT")
        connection.execute(
            """INSERT OR IGNORE INTO insight_scoring_version(
                    scoring_version_id, version_no, status, weights_json, content_hash, activated_at
                ) VALUES (?, ?, 'ACTIVE', ?, ?, CURRENT_TIMESTAMP)""",
            (
                scoring["scoring_version_id"],
                scoring["version_no"],
                canonical_json(scoring["weights"]),
                scoring["content_hash"],
            ),
        )
        connection.commit()

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM system_database_metadata LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("P0_DATABASE_METADATA_MISSING")
        return dict(row)

    def _verify_ready_connection(self, connection: sqlite3.Connection, manifest: dict[str, Any]) -> None:
        metadata = self._metadata(connection)
        if metadata["initialization_state"] not in {READY_STATE, "STANDARD_FIELD_CATALOG_ACTIVE", "INTEGRITY_VERIFIED", "STATIC_SEEDS_APPLIED", "PLC_MAPPING_ACTIVE", "NEW", "LEGACY_BACKUP_VERIFIED", "LEGACY_BACKUP_NOT_APPLICABLE"}:
            raise RuntimeError("P0_INITIALIZATION_STATE_INVALID")
        expected_hash = self._plc_seed_hash(manifest)
        if self._sha256_file(self.plc_seed_path) != expected_hash:
            raise RuntimeError("PLC_SEED_HASH_MISMATCH")
        self._verify_analysis_seed_artifacts(self._database_path(connection), manifest)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError("P0_DATABASE_INTEGRITY_CHECK_FAILED")
        mapping_count = connection.execute(
            "SELECT COUNT(*) FROM mapping_config WHERE business_type = 'PLC' AND status = 'ACTIVE'"
        ).fetchone()[0]
        catalog_count = connection.execute(
            "SELECT COUNT(*) FROM standard_field_catalog_version WHERE status = 'ACTIVE'"
        ).fetchone()[0]
        if mapping_count != 1 or catalog_count != 1:
            raise RuntimeError("PLC_ACTIVE_MAPPING_OR_CATALOG_MISSING")
        field_count = connection.execute(
            """SELECT COUNT(*) FROM standard_field_definition AS definition
               JOIN standard_field_catalog_version AS catalog
                 ON catalog.catalog_version_id = definition.catalog_version_id
              WHERE catalog.status = 'ACTIVE' AND definition.enabled = 1"""
        ).fetchone()[0]
        if field_count != 59:
            raise RuntimeError("PLC_ACTIVE_BUSINESS_FIELD_COUNT_INVALID")
        mapping_fields = {
            tuple(row)
            for row in connection.execute(
                """SELECT target_domain, target_field, required FROM mapping_item
                     WHERE config_id = (SELECT config_id FROM mapping_config
                                         WHERE business_type = 'PLC' AND status = 'ACTIVE')
                       AND enabled = 1"""
            )
        }
        catalog_fields = {
            tuple(row)
            for row in connection.execute(
                """SELECT definition.target_domain, definition.target_field, definition.required
                     FROM standard_field_definition AS definition
                     JOIN standard_field_catalog_version AS catalog
                       ON catalog.catalog_version_id = definition.catalog_version_id
                    WHERE catalog.status = 'ACTIVE' AND definition.enabled = 1"""
            )
        }
        if mapping_fields != catalog_fields:
            raise RuntimeError("PLC_MAPPING_CATALOG_FIELDSET_MISMATCH")
        required_paths = connection.execute(
            """SELECT target_domain || '.' || target_field FROM standard_field_definition AS definition
               JOIN standard_field_catalog_version AS catalog
                 ON catalog.catalog_version_id = definition.catalog_version_id
              WHERE catalog.status = 'ACTIVE' AND definition.required = 1 AND definition.enabled = 1"""
        ).fetchall()
        if [row[0] for row in required_paths] != ["ISSUE_FACT.business_issue_id"]:
            raise RuntimeError("PLC_REQUIRED_FIELD_INVALID")
        labels = connection.execute(
            """SELECT label_zh FROM standard_field_definition AS definition
               JOIN standard_field_catalog_version AS catalog
                 ON catalog.catalog_version_id = definition.catalog_version_id
              WHERE catalog.status = 'ACTIVE' AND definition.enabled = 1"""
        ).fetchall()
        if len(labels) != 59 or any(not self._contains_chinese(row[0]) for row in labels):
            raise RuntimeError("PLC_ACTIVE_LABEL_ZH_INVALID")
        technical_columns = connection.execute(
            """SELECT details_json FROM mapping_validation_result
                 WHERE config_id = (SELECT config_id FROM mapping_config
                                      WHERE business_type = 'PLC' AND status = 'ACTIVE')
                   AND code = 'KNOWN_TECHNICAL_COLUMN'"""
        ).fetchall()
        if len(technical_columns) != 1:
            raise RuntimeError("PLC_TECHNICAL_COLUMN_EXCLUSION_INVALID")
        technical_detail = json.loads(technical_columns[0][0])
        if technical_detail.get("field_path") != "PRODUCT_EXTENSION.source_id" or technical_detail.get("handling") != "RAW_ONLY":
            raise RuntimeError("PLC_TECHNICAL_COLUMN_EXCLUSION_INVALID")
        self._verify_analysis_seed_database(connection)

    def _verify_analysis_seed_artifacts(self, target: Path, manifest: dict[str, Any]) -> None:
        from quality_knowledge.p0.prompt_seed import prompt_set_hash, scoring_seed_hash

        expected = {seed["seed_key"]: seed["sha256"] for seed in manifest["seeds"]}
        checks = {
            "PROMPT_SET_P0_V1": prompt_set_hash(),
            "INSIGHT_SCORING_P0_V1": scoring_seed_hash(),
        }
        for seed_key, actual in checks.items():
            if expected.get(seed_key) != actual:
                raise self._blocked(
                    target,
                    f"{seed_key}_HASH_MISMATCH",
                    artifact_path=self.manifest_path,
                    expected_hash=expected.get(seed_key),
                    actual_hash=actual,
                    recommendation="恢复与 Release Manifest 一致的 P0 Prompt/评分 Seed 后重新初始化。",
                )

    @staticmethod
    def _verify_analysis_seed_database(connection: sqlite3.Connection) -> None:
        from quality_knowledge.p0.prompt_seed import build_prompt_seeds, build_scoring_seed, canonical_json

        expected_prompts = {seed["stage"]: seed for seed in build_prompt_seeds()}
        rows = connection.execute(
            "SELECT * FROM analysis_prompt_version WHERE status = 'ACTIVE' ORDER BY stage"
        ).fetchall()
        if len(rows) != 4 or {row["stage"] for row in rows} != set(expected_prompts):
            raise RuntimeError("P0_ACTIVE_PROMPT_SET_INVALID")
        for row in rows:
            expected = expected_prompts[row["stage"]]
            if (
                row["prompt_version_id"] != expected["prompt_version_id"]
                or row["content_hash"] != expected["content_hash"]
                or row["prompt_text"] != expected["prompt_text"]
                or row["taxonomy_version_id"] != expected["taxonomy_version_id"]
                or row["output_contract_json"] != canonical_json(expected["output_contract"])
                or row["model_params_json"] != canonical_json(expected["model_params"])
            ):
                raise RuntimeError("P0_ACTIVE_PROMPT_CONTENT_INVALID")
        scoring = build_scoring_seed()
        scoring_rows = connection.execute(
            "SELECT * FROM insight_scoring_version WHERE status = 'ACTIVE'"
        ).fetchall()
        if len(scoring_rows) != 1:
            raise RuntimeError("P0_ACTIVE_SCORING_SET_INVALID")
        row = scoring_rows[0]
        if (
            row["scoring_version_id"] != scoring["scoring_version_id"]
            or row["content_hash"] != scoring["content_hash"]
            or row["weights_json"] != canonical_json(scoring["weights"])
        ):
            raise RuntimeError("P0_ACTIVE_SCORING_CONTENT_INVALID")

    @staticmethod
    def _set_state(connection: sqlite3.Connection, state: str) -> None:
        connection.execute(
            "UPDATE system_database_metadata SET initialization_state = ?, updated_at = CURRENT_TIMESTAMP",
            (state,),
        )
        connection.commit()

    @staticmethod
    def _start_run(connection: sqlite3.Connection, run_id: str, database_id: str, backup: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO system_initialization_run(
                    initialization_run_id, database_instance_id, state, outcome,
                    diagnostic_id, details_json
                ) VALUES (?, ?, 'NEW', 'RUNNING', ?, ?)""",
            (run_id, database_id, f"DIAG-{uuid.uuid4().hex}", json.dumps({"backup": backup}, ensure_ascii=False)),
        )
        connection.commit()

    @staticmethod
    def _finish_run(connection: sqlite3.Connection, run_id: str, state: str, outcome: str, details: dict[str, Any]) -> None:
        connection.execute(
            """UPDATE system_initialization_run
                  SET state = ?, outcome = ?, details_json = ?, completed_at = CURRENT_TIMESTAMP
                WHERE initialization_run_id = ?""",
            (state, outcome, json.dumps(details, ensure_ascii=False), run_id),
        )
        connection.commit()

    def _mark_blocked(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        diagnostic: InitializationDiagnostic,
        backup: dict[str, Any],
    ) -> None:
        try:
            self._set_state(connection, BLOCKED_STATE)
            self._finish_run(
                connection,
                run_id,
                BLOCKED_STATE,
                "BLOCKED",
                {"diagnostic": diagnostic.as_dict(), "backup": backup},
            )
        except sqlite3.Error:
            pass

    def _blocked(
        self,
        target: Path,
        code: str,
        *,
        artifact_path: Path | None,
        expected_hash: str | None,
        actual_hash: str | None,
        recommendation: str,
    ) -> P0InitializationError:
        diagnostic = self._diagnostic(
            BLOCKED_STATE,
            code,
            artifact_path or target,
            expected_hash,
            actual_hash,
            recommendation,
        )
        return P0InitializationError(code, diagnostic)

    @staticmethod
    def _diagnostic(
        state: str,
        code: str | None,
        artifact_path: Path | None,
        expected_hash: str | None,
        actual_hash: str | None,
        recommendation: str,
    ) -> InitializationDiagnostic:
        return InitializationDiagnostic(
            diagnostic_id=f"DIAG-{uuid.uuid4().hex}",
            initialization_state=state,
            blocker_code=code,
            artifact_path=str(artifact_path.resolve()) if artifact_path else None,
            expected_hash=expected_hash,
            actual_hash=actual_hash,
            recommendation=recommendation,
        )

    def _result(self, connection: sqlite3.Connection, outcome: str, backup: dict[str, Any]) -> dict[str, Any]:
        metadata = self._metadata(connection)
        mapping = connection.execute(
            "SELECT config_id, version FROM mapping_config WHERE business_type = 'PLC' AND status = 'ACTIVE'"
        ).fetchone()
        catalog = connection.execute(
            "SELECT catalog_version_id, version_no FROM standard_field_catalog_version WHERE status = 'ACTIVE'"
        ).fetchone()
        return {
            "outcome": outcome,
            "database_path": str(self._database_path(connection)),
            "database_instance_id": metadata["database_instance_id"],
            "schema_version": metadata["schema_version"],
            "initialization_state": metadata["initialization_state"],
            "backup": backup,
            "plc_mapping": dict(mapping) if mapping else None,
            "standard_catalog": dict(catalog) if catalog else None,
        }

    @staticmethod
    def _database_path(connection: sqlite3.Connection) -> Path:
        return Path(connection.execute("PRAGMA database_list").fetchone()[2]).resolve()

    @staticmethod
    def _manifest_content_hash(manifest: dict[str, Any]) -> str:
        payload = {
            "release_id": manifest["release_id"],
            "schema_version": manifest["schema_version"],
            "seeds": manifest["seeds"],
            "technical_field_exclusions": manifest.get("technical_field_exclusions", {}),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _contains_chinese(value: str | None) -> bool:
        return bool(value and any("\u4e00" <= character <= "\u9fff" for character in value))
