"""Independent SQLite repository for the Quality Scenario V1 contract."""
from __future__ import annotations

import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path

from quality_knowledge.quality_scenario_v1 import (
    SCHEMA_VERSION,
    QualityScenarioV1,
    ScenarioActor,
    ScenarioCandidateV1,
    ScenarioStatus,
    scenario_from_candidate,
    validate_status_transition,
)
from quality_knowledge.sqlite_tuning import configure_connection


SCHEMA = """
CREATE TABLE IF NOT EXISTS quality_scenario_v1_meta(
 schema_version TEXT PRIMARY KEY,
 initialized_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS quality_scenario_v1(
 scenario_id TEXT PRIMARY KEY,
 schema_version TEXT NOT NULL,
 scenario_version INTEGER NOT NULL,
 status TEXT NOT NULL,
 product_code TEXT NOT NULL,
 product_name TEXT NOT NULL DEFAULT '',
 lifecycle_stage_code TEXT NOT NULL DEFAULT '',
 lifecycle_stage_name TEXT NOT NULL DEFAULT '',
 business_activity_code TEXT NOT NULL DEFAULT '',
 business_activity_name TEXT NOT NULL DEFAULT '',
 business_goal TEXT NOT NULL DEFAULT '',
 scenario_name TEXT NOT NULL,
 scenario_description TEXT NOT NULL DEFAULT '',
 quality_concern_code TEXT NOT NULL DEFAULT '',
 quality_concern_name TEXT NOT NULL DEFAULT '',
 trigger_source TEXT NOT NULL DEFAULT '',
 trigger_reason TEXT NOT NULL DEFAULT '',
 trigger_condition TEXT NOT NULL DEFAULT '',
 expected_result TEXT NOT NULL DEFAULT '',
 applicability_scope TEXT NOT NULL DEFAULT '',
 blockers_json TEXT NOT NULL DEFAULT '[]',
 missing_information_json TEXT NOT NULL DEFAULT '[]',
 review_status TEXT NOT NULL DEFAULT 'PENDING',
 reviewer TEXT NOT NULL DEFAULT '',
 reviewed_at TEXT NOT NULL DEFAULT '',
 review_comment TEXT NOT NULL DEFAULT '',
 quality_confirmed_by TEXT NOT NULL DEFAULT '',
 quality_confirmed_at TEXT NOT NULL DEFAULT '',
 technical_confirmed_by TEXT NOT NULL DEFAULT '',
 technical_confirmed_at TEXT NOT NULL DEFAULT '',
 confirmation_note TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 published_at TEXT NOT NULL DEFAULT '',
 parent_scenario_version INTEGER,
 change_summary TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS quality_scenario_v1_source(
 scenario_id TEXT NOT NULL REFERENCES quality_scenario_v1(scenario_id) ON DELETE CASCADE,
 source_ref TEXT NOT NULL,
 source_json TEXT NOT NULL,
 PRIMARY KEY(scenario_id, source_ref)
);
CREATE TABLE IF NOT EXISTS quality_scenario_v1_evidence(
 scenario_id TEXT NOT NULL REFERENCES quality_scenario_v1(scenario_id) ON DELETE CASCADE,
 evidence_id TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 PRIMARY KEY(scenario_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS quality_scenario_v1_review(
 review_id TEXT PRIMARY KEY,
 scenario_id TEXT NOT NULL REFERENCES quality_scenario_v1(scenario_id) ON DELETE CASCADE,
 scenario_version INTEGER NOT NULL,
 review_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS quality_scenario_v1_version(
 scenario_id TEXT NOT NULL REFERENCES quality_scenario_v1(scenario_id) ON DELETE CASCADE,
 scenario_version INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(scenario_id, scenario_version)
);
CREATE INDEX IF NOT EXISTS idx_qsv1_product_status
 ON quality_scenario_v1(product_code,status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qsv1_lifecycle
 ON quality_scenario_v1(product_code,lifecycle_stage_code,status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qsv1_activity
 ON quality_scenario_v1(product_code,business_activity_code,status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qsv1_concern
 ON quality_scenario_v1(product_code,quality_concern_code,status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qsv1_source_source_ref
 ON quality_scenario_v1_source(source_ref,scenario_id);
CREATE INDEX IF NOT EXISTS idx_qsv1_evidence_source
 ON quality_scenario_v1_evidence(scenario_id,evidence_id);
"""


class QualityScenarioV1Repository(ABC):
    @abstractmethod
    def create_from_candidate(
        self,
        candidate: ScenarioCandidateV1,
        *,
        scenario_id: str = "",
        created_by: str = "",
    ) -> QualityScenarioV1: ...

    @abstractmethod
    def save(
        self,
        scenario: QualityScenarioV1,
        *,
        actor: ScenarioActor | str = ScenarioActor.HUMAN,
        expected_scenario_version: int | None = None,
    ) -> QualityScenarioV1: ...

    @abstractmethod
    def get(self, scenario_id: str) -> QualityScenarioV1 | None: ...

    @abstractmethod
    def list(
        self,
        *,
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        status: ScenarioStatus | str | None = None,
        q: str = "",
    ) -> list[QualityScenarioV1]: ...

    @abstractmethod
    def history(self, scenario_id: str) -> dict[str, list[dict]]: ...

    @abstractmethod
    def delete(self, scenario_id: str) -> bool: ...


class SQLiteQualityScenarioV1Repository(QualityScenarioV1Repository):
    """SQLite V1 repository.

    This repository uses its own qsv1 table namespace.  Pointing it at an
    existing legacy database is safe: legacy quality_scenario tables are not
    altered, migrated, or treated as V1 source of truth.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        return configure_connection(connection)

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(SCHEMA)
            self._ensure_v02_columns(connection)
            connection.execute(
                "INSERT OR IGNORE INTO quality_scenario_v1_meta(schema_version) VALUES(?)",
                (SCHEMA_VERSION,),
            )

    @staticmethod
    def _ensure_v02_columns(connection: sqlite3.Connection) -> None:
        """Idempotent additive migration from the initial QS-MVP-02 candidate schema."""
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(quality_scenario_v1)")
        }
        additions = {
            "trigger_source": "TEXT NOT NULL DEFAULT ''",
            "trigger_reason": "TEXT NOT NULL DEFAULT ''",
            "quality_confirmed_by": "TEXT NOT NULL DEFAULT ''",
            "quality_confirmed_at": "TEXT NOT NULL DEFAULT ''",
            "technical_confirmed_by": "TEXT NOT NULL DEFAULT ''",
            "technical_confirmed_at": "TEXT NOT NULL DEFAULT ''",
            "confirmation_note": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE quality_scenario_v1 ADD COLUMN {name} {ddl}"
                )

    @contextmanager
    def _transaction(self):
        connection = self.connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            yield connection
            connection.execute("COMMIT")
            begun = False
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def create_from_candidate(
        self,
        candidate: ScenarioCandidateV1,
        *,
        scenario_id: str = "",
        created_by: str = "",
    ) -> QualityScenarioV1:
        scenario = scenario_from_candidate(
            candidate,
            scenario_id or f"QSV1-{uuid.uuid4().hex}",
            created_by=created_by,
        )
        return self.save(scenario, actor=ScenarioActor.AI)

    def _load(self, connection: sqlite3.Connection, scenario_id: str) -> QualityScenarioV1 | None:
        row = connection.execute(
            "SELECT * FROM quality_scenario_v1 WHERE scenario_id=?",
            (scenario_id,),
        ).fetchone()
        if not row:
            return None
        sources = [
            json.loads(item["source_json"])
            for item in connection.execute(
                "SELECT source_json FROM quality_scenario_v1_source WHERE scenario_id=? ORDER BY source_ref",
                (scenario_id,),
            )
        ]
        evidence = [
            json.loads(item["evidence_json"])
            for item in connection.execute(
                "SELECT evidence_json FROM quality_scenario_v1_evidence WHERE scenario_id=? ORDER BY evidence_id",
                (scenario_id,),
            )
        ]
        payload = {
            "scenario_id": row["scenario_id"],
            "schema_version": row["schema_version"],
            "scenario_version": row["scenario_version"],
            "status": row["status"],
            "product_code": row["product_code"],
            "product_name": row["product_name"],
            "lifecycle_stage_code": row["lifecycle_stage_code"],
            "lifecycle_stage_name": row["lifecycle_stage_name"],
            "business_activity_code": row["business_activity_code"],
            "business_activity_name": row["business_activity_name"],
            "business_goal": row["business_goal"],
            "scenario_name": row["scenario_name"],
            "scenario_description": row["scenario_description"],
            "quality_concern_code": row["quality_concern_code"],
            "quality_concern_name": row["quality_concern_name"],
            "trigger_source": row["trigger_source"] or None,
            "trigger_reason": row["trigger_reason"],
            "trigger_condition": row["trigger_condition"],
            "expected_result": row["expected_result"],
            "applicability_scope": row["applicability_scope"],
            "source_problem_refs": sources,
            "evidence_refs": evidence,
            "blockers": json.loads(row["blockers_json"] or "[]"),
            "missing_information": json.loads(row["missing_information_json"] or "[]"),
            "review": {
                "review_status": row["review_status"],
                "reviewer": row["reviewer"],
                "reviewed_at": row["reviewed_at"],
                "comment": row["review_comment"],
            },
            "confirmation": {
                "quality_confirmed_by": row["quality_confirmed_by"],
                "quality_confirmed_at": row["quality_confirmed_at"],
                "technical_confirmed_by": row["technical_confirmed_by"],
                "technical_confirmed_at": row["technical_confirmed_at"],
                "confirmation_note": row["confirmation_note"],
            },
            "version": {
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "published_at": row["published_at"],
                "parent_scenario_version": row["parent_scenario_version"],
                "change_summary": row["change_summary"],
            },
        }
        return QualityScenarioV1.model_validate(payload)

    def save(
        self,
        scenario: QualityScenarioV1,
        *,
        actor: ScenarioActor | str = ScenarioActor.HUMAN,
        expected_scenario_version: int | None = None,
    ) -> QualityScenarioV1:
        scenario = QualityScenarioV1.model_validate(scenario)
        actor_type = ScenarioActor(actor)
        with self._transaction() as connection:
            existing = self._load(connection, scenario.scenario_id)
            if expected_scenario_version is not None:
                if existing is None or existing.scenario_version != expected_scenario_version:
                    raise ValueError("SCENARIO_VERSION_CONFLICT")
            if existing is None:
                if scenario.status != ScenarioStatus.CANDIDATE:
                    raise ValueError("SCENARIO_INITIAL_STATUS_MUST_BE_CANDIDATE")
            elif existing.status != scenario.status:
                validate_status_transition(
                    existing.status,
                    scenario.status,
                    actor=actor_type,
                    blockers=scenario.blockers,
                    missing_information=scenario.missing_information,
                    has_source_problem=bool(scenario.source_problem_refs),
                    has_evidence=bool(scenario.evidence_refs),
                )
            record = {
                "scenario_id": scenario.scenario_id,
                "schema_version": scenario.schema_version,
                "scenario_version": scenario.scenario_version,
                "status": scenario.status.value,
                "product_code": scenario.product_code,
                "product_name": scenario.product_name,
                "lifecycle_stage_code": scenario.lifecycle_stage_code,
                "lifecycle_stage_name": scenario.lifecycle_stage_name,
                "business_activity_code": scenario.business_activity_code,
                "business_activity_name": scenario.business_activity_name,
                "business_goal": scenario.business_goal,
                "scenario_name": scenario.scenario_name,
                "scenario_description": scenario.scenario_description,
                "quality_concern_code": scenario.quality_concern_code,
                "quality_concern_name": scenario.quality_concern_name,
                "trigger_source": scenario.trigger_source.value if scenario.trigger_source else "",
                "trigger_reason": scenario.trigger_reason,
                "trigger_condition": scenario.trigger_condition,
                "expected_result": scenario.expected_result,
                "applicability_scope": scenario.applicability_scope,
                "blockers_json": json.dumps(scenario.blockers, ensure_ascii=False),
                "missing_information_json": json.dumps(
                    [item.model_dump(mode="json") for item in scenario.missing_information],
                    ensure_ascii=False,
                ),
                "review_status": scenario.review.review_status.value,
                "reviewer": scenario.review.reviewer,
                "reviewed_at": scenario.review.reviewed_at,
                "review_comment": scenario.review.comment,
                "quality_confirmed_by": scenario.confirmation.quality_confirmed_by,
                "quality_confirmed_at": scenario.confirmation.quality_confirmed_at,
                "technical_confirmed_by": scenario.confirmation.technical_confirmed_by,
                "technical_confirmed_at": scenario.confirmation.technical_confirmed_at,
                "confirmation_note": scenario.confirmation.confirmation_note,
                "created_by": scenario.version.created_by,
                "created_at": scenario.version.created_at,
                "updated_at": scenario.version.updated_at,
                "published_at": scenario.version.published_at,
                "parent_scenario_version": scenario.version.parent_scenario_version,
                "change_summary": scenario.version.change_summary,
            }
            columns = ",".join(record)
            placeholders = ",".join("?" for _ in record)
            updates = ",".join(
                f"{name}=excluded.{name}" for name in record if name != "scenario_id"
            )
            connection.execute(
                f"""INSERT INTO quality_scenario_v1({columns}) VALUES({placeholders})
                    ON CONFLICT(scenario_id) DO UPDATE SET {updates}""",
                tuple(record.values()),
            )
            connection.execute(
                "DELETE FROM quality_scenario_v1_source WHERE scenario_id=?",
                (scenario.scenario_id,),
            )
            for source in scenario.source_problem_refs:
                connection.execute(
                    "INSERT INTO quality_scenario_v1_source(scenario_id,source_ref,source_json) VALUES(?,?,?)",
                    (
                        scenario.scenario_id,
                        source.source_ref,
                        json.dumps(source.model_dump(mode="json"), ensure_ascii=False),
                    ),
                )
            connection.execute(
                "DELETE FROM quality_scenario_v1_evidence WHERE scenario_id=?",
                (scenario.scenario_id,),
            )
            for evidence in scenario.evidence_refs:
                connection.execute(
                    "INSERT INTO quality_scenario_v1_evidence(scenario_id,evidence_id,evidence_json) VALUES(?,?,?)",
                    (
                        scenario.scenario_id,
                        evidence.evidence_id,
                        json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False),
                    ),
                )
            snapshot = json.dumps(scenario.model_dump(mode="json"), ensure_ascii=False)
            connection.execute(
                """INSERT INTO quality_scenario_v1_version(
                       scenario_id,scenario_version,snapshot_json)
                   VALUES(?,?,?)
                   ON CONFLICT(scenario_id,scenario_version)
                   DO UPDATE SET snapshot_json=excluded.snapshot_json""",
                (scenario.scenario_id, scenario.scenario_version, snapshot),
            )
            if (
                scenario.review.review_status.value != "PENDING"
                or scenario.confirmation.quality_confirmed
                or scenario.confirmation.technical_confirmed
            ):
                connection.execute(
                    """INSERT INTO quality_scenario_v1_review(
                           review_id,scenario_id,scenario_version,review_json)
                       VALUES(?,?,?,?)""",
                    (
                        f"QSRV-{uuid.uuid4().hex}",
                        scenario.scenario_id,
                        scenario.scenario_version,
                        json.dumps(
                            {
                                "review": scenario.review.model_dump(mode="json"),
                                "confirmation": scenario.confirmation.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
        saved = self.get(scenario.scenario_id)
        if saved is None:
            raise RuntimeError("SCENARIO_SAVE_FAILED")
        return saved

    def get(self, scenario_id: str) -> QualityScenarioV1 | None:
        with self.connect() as connection:
            return self._load(connection, scenario_id)

    def list_by_source(self, source_ref: str) -> list[QualityScenarioV1]:
        source_ref = str(source_ref or "").strip()
        if not source_ref:
            return []
        with self.connect() as connection:
            ids = [
                row["scenario_id"]
                for row in connection.execute(
                    """SELECT source.scenario_id
                       FROM quality_scenario_v1_source source
                       JOIN quality_scenario_v1 scenario
                         ON scenario.scenario_id=source.scenario_id
                       WHERE source.source_ref=?
                       ORDER BY scenario.updated_at DESC,source.scenario_id""",
                    (source_ref,),
                )
            ]
            return [
                item
                for item in (self._load(connection, scenario_id) for scenario_id in ids)
                if item is not None
            ]

    def history(self, scenario_id: str) -> dict[str, list[dict]]:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM quality_scenario_v1 WHERE scenario_id=?",
                (scenario_id,),
            ).fetchone()
            if exists is None:
                return {"versions": [], "reviews": []}
            versions = []
            for row in connection.execute(
                """SELECT scenario_version,snapshot_json,created_at
                   FROM quality_scenario_v1_version
                   WHERE scenario_id=?
                   ORDER BY scenario_version DESC""",
                (scenario_id,),
            ):
                versions.append(
                    {
                        "scenario_version": int(row["scenario_version"]),
                        "created_at": row["created_at"],
                        "snapshot": json.loads(row["snapshot_json"] or "{}"),
                    }
                )
            reviews = []
            for row in connection.execute(
                """SELECT review_id,scenario_version,review_json,created_at
                   FROM quality_scenario_v1_review
                   WHERE scenario_id=?
                   ORDER BY scenario_version DESC,created_at DESC""",
                (scenario_id,),
            ):
                payload = json.loads(row["review_json"] or "{}")
                reviews.append(
                    {
                        "review_id": row["review_id"],
                        "scenario_version": int(row["scenario_version"]),
                        "created_at": row["created_at"],
                        "review": payload.get("review") or {},
                        "confirmation": payload.get("confirmation") or {},
                    }
                )
            return {"versions": versions, "reviews": reviews}

    def list(
        self,
        *,
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        status: ScenarioStatus | str | None = None,
        q: str = "",
    ) -> list[QualityScenarioV1]:
        clauses: list[str] = []
        params: list[str] = []
        for column, value in (
            ("product_code", product_code),
            ("lifecycle_stage_code", lifecycle_stage_code),
            ("business_activity_code", business_activity_code),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if quality_concern_code:
            clauses.append("(quality_concern_code=? OR quality_concern_name=?)")
            params.extend([quality_concern_code, quality_concern_code])
        if status:
            clauses.append("status=?")
            params.append(ScenarioStatus(status).value)
        if q:
            clauses.append(
                """LOWER(
                       scenario_name||' '||scenario_description||' '||
                       product_code||' '||product_name||' '||
                       lifecycle_stage_code||' '||lifecycle_stage_name||' '||
                       business_activity_code||' '||business_activity_name||' '||
                       quality_concern_code||' '||quality_concern_name||' '||
                       trigger_condition||' '||expected_result
                   ) LIKE ?"""
            )
            params.append("%" + q.lower() + "%")
        sql = "SELECT scenario_id FROM quality_scenario_v1"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC,scenario_id"
        with self.connect() as connection:
            ids = [row["scenario_id"] for row in connection.execute(sql, params)]
            return [item for item in (self._load(connection, sid) for sid in ids) if item]

    def delete(self, scenario_id: str) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                "DELETE FROM quality_scenario_v1 WHERE scenario_id=?",
                (scenario_id,),
            )
            return bool(result.rowcount)


__all__ = [
    "SCHEMA",
    "QualityScenarioV1Repository",
    "SQLiteQualityScenarioV1Repository",
]
