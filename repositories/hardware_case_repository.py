"""SQLite persistence for Hardware Case MVP Backend Core (M2)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS hardware_case_schema_version (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hardware_case (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    case_status TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'READY',
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    product_context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS hardware_case_fact (
    case_id TEXT NOT NULL REFERENCES hardware_case(case_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    candidate_json TEXT,
    confirmed_json TEXT,
    review_disposition TEXT NOT NULL DEFAULT 'UNREVIEWED',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(case_id, field_name)
);

CREATE TABLE IF NOT EXISTS hardware_tree_node (
    node_id TEXT PRIMARY KEY,
    tree_type TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_id TEXT,
    path_json TEXT NOT NULL,
    description TEXT,
    source_ref TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_hardware_tree_type
    ON hardware_tree_node(tree_type, active);

CREATE TABLE IF NOT EXISTS hardware_case_mapping (
    mapping_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES hardware_case(case_id) ON DELETE CASCADE,
    tree_type TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_path TEXT,
    relation_role TEXT NOT NULL,
    mapping_status TEXT NOT NULL,
    confidence REAL,
    basis_refs_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(case_id, tree_type, node_id)
);
CREATE INDEX IF NOT EXISTS idx_hardware_mapping_case
    ON hardware_case_mapping(case_id, tree_type, mapping_status);
CREATE INDEX IF NOT EXISTS idx_hardware_mapping_node
    ON hardware_case_mapping(node_id, mapping_status);

CREATE TABLE IF NOT EXISTS hardware_evidence (
    evidence_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES hardware_case(case_id) ON DELETE CASCADE,
    source_ref TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    excerpt_or_caption TEXT,
    evidence_status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hardware_evidence_case
    ON hardware_evidence(case_id, evidence_status);

CREATE TABLE IF NOT EXISTS hardware_maintenance_anomaly (
    case_id TEXT NOT NULL REFERENCES hardware_case(case_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(case_id, code)
);
"""


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class HardwareCaseRepository:
    """Repository abstraction for M2; callers do not depend on table layout."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                """
                INSERT INTO hardware_case_schema_version(component, version)
                VALUES('hardware_case', ?)
                ON CONFLICT(component) DO UPDATE SET
                    version=MAX(version, excluded.version),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (SCHEMA_VERSION,),
            )

    def schema_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT version FROM hardware_case_schema_version WHERE component='hardware_case'"
            ).fetchone()
        return int(row["version"])

    def save_case(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = str(case.get("case_id") or "").strip()
        title = str(case.get("title") or "").strip()
        if not case_id or not title:
            raise ValueError("CASE_CONTRACT_INVALID")

        facts = case.get("facts") if isinstance(case.get("facts"), dict) else {}
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_case(
                    case_id, title, case_status, processing_status,
                    source_refs_json, product_context_json
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(case_id) DO UPDATE SET
                    title=excluded.title,
                    case_status=excluded.case_status,
                    processing_status=excluded.processing_status,
                    source_refs_json=excluded.source_refs_json,
                    product_context_json=excluded.product_context_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    case_id,
                    title,
                    case.get("case_status", "PENDING_ANALYSIS"),
                    case.get("processing_status", "READY"),
                    _dump(case.get("source_refs") or []),
                    _dump(case.get("product_context") or {}),
                ),
            )
            for field_name, field in facts.items():
                if not isinstance(field, dict):
                    continue
                connection.execute(
                    """
                    INSERT INTO hardware_case_fact(
                        case_id, field_name, candidate_json, confirmed_json,
                        review_disposition, evidence_refs_json
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(case_id, field_name) DO UPDATE SET
                        candidate_json=excluded.candidate_json,
                        confirmed_json=excluded.confirmed_json,
                        review_disposition=excluded.review_disposition,
                        evidence_refs_json=excluded.evidence_refs_json
                    """,
                    (
                        case_id,
                        str(field_name),
                        _dump(field.get("candidate_value")),
                        _dump(field.get("confirmed_value")),
                        field.get("review_disposition", "UNREVIEWED"),
                        _dump(field.get("evidence_refs") or []),
                    ),
                )
        loaded = self.get_case(case_id)
        if loaded is None:
            raise RuntimeError("CASE_WRITE_FAILED")
        return loaded

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_case WHERE case_id=?", (case_id,)
            ).fetchone()
            if row is None:
                return None
            fact_rows = connection.execute(
                """
                SELECT * FROM hardware_case_fact
                WHERE case_id=? ORDER BY field_name
                """,
                (case_id,),
            ).fetchall()
        return {
            "case_id": row["case_id"],
            "title": row["title"],
            "case_status": row["case_status"],
            "processing_status": row["processing_status"],
            "source_refs": _load(row["source_refs_json"], []),
            "product_context": _load(row["product_context_json"], {}),
            "facts": {
                item["field_name"]: {
                    "candidate_value": _load(item["candidate_json"], None),
                    "confirmed_value": _load(item["confirmed_json"], None),
                    "review_disposition": item["review_disposition"],
                    "evidence_refs": _load(item["evidence_refs_json"], []),
                }
                for item in fact_rows
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row["published_at"],
        }

    def list_cases(self, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        requested = [str(item) for item in (statuses or [])]
        with self.connect() as connection:
            if requested:
                placeholders = ",".join("?" for _ in requested)
                rows = connection.execute(
                    f"SELECT case_id FROM hardware_case WHERE case_status IN ({placeholders}) ORDER BY case_id",
                    requested,
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT case_id FROM hardware_case ORDER BY case_id"
                ).fetchall()
        return [case for row in rows if (case := self.get_case(row["case_id"])) is not None]

    def set_case_status(self, case_id: str, status: str) -> dict[str, Any]:
        with self.connect() as connection:
            published = ", published_at=CURRENT_TIMESTAMP" if status == "PUBLISHED" else ""
            cursor = connection.execute(
                f"""
                UPDATE hardware_case
                SET case_status=?, updated_at=CURRENT_TIMESTAMP{published}
                WHERE case_id=?
                """,
                (status, case_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(case_id)
        self.refresh_source_anomaly(case_id)
        loaded = self.get_case(case_id)
        if loaded is None:
            raise KeyError(case_id)
        return loaded

    def save_tree_node(self, node: dict[str, Any]) -> dict[str, Any]:
        node_id = str(node.get("node_id") or "").strip()
        if not node_id:
            raise ValueError("TREE_NODE_INVALID")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_tree_node(
                    node_id, tree_type, name, parent_id, path_json,
                    description, source_ref, active
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                    tree_type=excluded.tree_type,
                    name=excluded.name,
                    parent_id=excluded.parent_id,
                    path_json=excluded.path_json,
                    description=excluded.description,
                    source_ref=excluded.source_ref,
                    active=excluded.active
                """,
                (
                    node_id,
                    node.get("tree_type"),
                    node.get("name"),
                    node.get("parent_id"),
                    _dump(node.get("path") or []),
                    node.get("description"),
                    node.get("source_ref"),
                    int(node.get("active", True)),
                ),
            )
        return self.get_tree_node(node_id) or {}

    def get_tree_node(self, node_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_tree_node WHERE node_id=?", (node_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "node_id": row["node_id"],
            "tree_type": row["tree_type"],
            "name": row["name"],
            "parent_id": row["parent_id"],
            "path": _load(row["path_json"], []),
            "description": row["description"],
            "source_ref": row["source_ref"],
            "active": bool(row["active"]),
        }

    def list_tree_nodes(self, tree_type: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if tree_type:
                rows = connection.execute(
                    """
                    SELECT node_id FROM hardware_tree_node
                    WHERE tree_type=? ORDER BY node_id
                    """,
                    (tree_type,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT node_id FROM hardware_tree_node ORDER BY tree_type, node_id"
                ).fetchall()
        return [node for row in rows if (node := self.get_tree_node(row["node_id"])) is not None]

    def save_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        mapping_id = str(mapping.get("mapping_id") or "").strip()
        case_id = str(mapping.get("case_id") or "").strip()
        tree_type = str(mapping.get("tree_type") or "").strip()
        if not mapping_id or not case_id or not tree_type:
            raise ValueError("MAPPING_INVALID")
        with self.connect() as connection:
            if mapping.get("relation_role") == "PRIMARY":
                connection.execute(
                    """
                    UPDATE hardware_case_mapping
                    SET relation_role='SECONDARY'
                    WHERE case_id=? AND tree_type=? AND relation_role='PRIMARY'
                    """,
                    (case_id, tree_type),
                )
            connection.execute(
                """
                INSERT INTO hardware_case_mapping(
                    mapping_id, case_id, tree_type, node_id, node_path,
                    relation_role, mapping_status, confidence, basis_refs_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mapping_id) DO UPDATE SET
                    case_id=excluded.case_id,
                    tree_type=excluded.tree_type,
                    node_id=excluded.node_id,
                    node_path=excluded.node_path,
                    relation_role=excluded.relation_role,
                    mapping_status=excluded.mapping_status,
                    confidence=excluded.confidence,
                    basis_refs_json=excluded.basis_refs_json
                """,
                (
                    mapping_id,
                    case_id,
                    tree_type,
                    mapping.get("node_id"),
                    mapping.get("node_path"),
                    mapping.get("relation_role"),
                    mapping.get("mapping_status"),
                    mapping.get("confidence"),
                    _dump(mapping.get("basis_refs") or []),
                ),
            )
        return next(
            item
            for item in self.list_mappings(case_id=case_id)
            if item["mapping_id"] == mapping_id
        )

    def list_mappings(
        self, *, case_id: str | None = None, node_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if case_id is not None:
            clauses.append("case_id=?")
            values.append(case_id)
        if node_id is not None:
            clauses.append("node_id=?")
            values.append(node_id)
        sql = "SELECT * FROM hardware_case_mapping"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY mapping_id"
        with self.connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        return [
            {
                "mapping_id": row["mapping_id"],
                "case_id": row["case_id"],
                "tree_type": row["tree_type"],
                "node_id": row["node_id"],
                "node_path": row["node_path"],
                "relation_role": row["relation_role"],
                "mapping_status": row["mapping_status"],
                "confidence": row["confidence"],
                "basis_refs": _load(row["basis_refs_json"], []),
            }
            for row in rows
        ]

    def save_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        evidence_id = str(evidence.get("evidence_id") or "").strip()
        case_id = str(evidence.get("case_id") or "").strip()
        if not evidence_id or not case_id:
            raise ValueError("EVIDENCE_INVALID")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_evidence(
                    evidence_id, case_id, source_ref, evidence_type,
                    locator_json, excerpt_or_caption, evidence_status
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    case_id=excluded.case_id,
                    source_ref=excluded.source_ref,
                    evidence_type=excluded.evidence_type,
                    locator_json=excluded.locator_json,
                    excerpt_or_caption=excluded.excerpt_or_caption,
                    evidence_status=excluded.evidence_status
                """,
                (
                    evidence_id,
                    case_id,
                    evidence.get("source_ref"),
                    evidence.get("evidence_type"),
                    _dump(evidence.get("locator") or {}),
                    evidence.get("excerpt_or_caption"),
                    evidence.get("evidence_status"),
                ),
            )
        self.refresh_source_anomaly(case_id)
        return next(
            item
            for item in self.list_evidence(case_id=case_id)
            if item["evidence_id"] == evidence_id
        )

    def list_evidence(self, *, case_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if case_id is None:
                rows = connection.execute(
                    "SELECT * FROM hardware_evidence ORDER BY evidence_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM hardware_evidence
                    WHERE case_id=? ORDER BY evidence_id
                    """,
                    (case_id,),
                ).fetchall()
        return [
            {
                "evidence_id": row["evidence_id"],
                "case_id": row["case_id"],
                "source_ref": row["source_ref"],
                "evidence_type": row["evidence_type"],
                "locator": _load(row["locator_json"], {}),
                "excerpt_or_caption": row["excerpt_or_caption"],
                "evidence_status": row["evidence_status"],
            }
            for row in rows
        ]

    def refresh_source_anomaly(self, case_id: str) -> None:
        case = self.get_case(case_id)
        if case is None:
            return
        evidence = self.list_evidence(case_id=case_id)
        unavailable = (
            case.get("case_status") == "PUBLISHED"
            and bool(evidence)
            and not any(item.get("evidence_status") == "AVAILABLE" for item in evidence)
        )
        with self.connect() as connection:
            if unavailable:
                connection.execute(
                    """
                    INSERT INTO hardware_maintenance_anomaly(case_id, code, active)
                    VALUES(?, 'SOURCE_UNAVAILABLE', 1)
                    ON CONFLICT(case_id, code) DO UPDATE SET
                        active=1, updated_at=CURRENT_TIMESTAMP
                    """,
                    (case_id,),
                )
            else:
                connection.execute(
                    """
                    UPDATE hardware_maintenance_anomaly
                    SET active=0, updated_at=CURRENT_TIMESTAMP
                    WHERE case_id=? AND code='SOURCE_UNAVAILABLE'
                    """,
                    (case_id,),
                )

    def list_anomalies(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if active_only:
                rows = connection.execute(
                    """
                    SELECT case_id, code, active, updated_at
                    FROM hardware_maintenance_anomaly
                    WHERE active=1 ORDER BY case_id, code
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT case_id, code, active, updated_at
                    FROM hardware_maintenance_anomaly
                    ORDER BY case_id, code
                    """
                ).fetchall()
        return [
            {
                "case_id": row["case_id"],
                "code": row["code"],
                "active": bool(row["active"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
