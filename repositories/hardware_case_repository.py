"""SQLite persistence for Hardware Case MVP V0.1.

The repository stores contract-shaped data but deliberately contains no AI,
Runtime, Word parsing, or product-visibility decisions. Those stay in the
hardware-case/v1 service boundary.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS hardware_case (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    case_status TEXT NOT NULL,
    processing_status TEXT NOT NULL,
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
    review_disposition TEXT NOT NULL,
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
    active INTEGER NOT NULL DEFAULT 1,
    source_metadata_json TEXT NOT NULL DEFAULT '{}'
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
    basis_refs_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_hardware_mapping_case
ON hardware_case_mapping(case_id, tree_type, mapping_status);

CREATE INDEX IF NOT EXISTS idx_hardware_mapping_node
ON hardware_case_mapping(node_id, mapping_status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_hardware_primary_mapping
ON hardware_case_mapping(case_id, tree_type)
WHERE relation_role='PRIMARY';

CREATE TABLE IF NOT EXISTS hardware_case_evidence (
    evidence_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES hardware_case(case_id) ON DELETE CASCADE,
    source_ref TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    excerpt_or_caption TEXT,
    evidence_status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hardware_evidence_case
ON hardware_case_evidence(case_id, evidence_status);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class HardwareCaseRepository:
    """Durable repository used by M2 Backend Core."""

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

    def save_case(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = str(case.get("case_id") or "").strip()
        title = str(case.get("title") or "").strip()
        if not case_id or not title:
            raise ValueError("CASE_ID_AND_TITLE_REQUIRED")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_case(
                    case_id,title,case_status,processing_status,
                    source_refs_json,product_context_json
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
                    _json(case.get("source_refs") or []),
                    _json(case.get("product_context") or {}),
                ),
            )
            for field_name, field in (case.get("facts") or {}).items():
                self._save_fact(connection, case_id, str(field_name), field)
        return self.get_case(case_id) or {}

    @staticmethod
    def _save_fact(
        connection: sqlite3.Connection,
        case_id: str,
        field_name: str,
        field: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO hardware_case_fact(
                case_id,field_name,candidate_json,confirmed_json,
                review_disposition,evidence_refs_json
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(case_id,field_name) DO UPDATE SET
                candidate_json=excluded.candidate_json,
                confirmed_json=excluded.confirmed_json,
                review_disposition=excluded.review_disposition,
                evidence_refs_json=excluded.evidence_refs_json
            """,
            (
                case_id,
                field_name,
                _json(field.get("candidate_value")),
                _json(field.get("confirmed_value")),
                field.get("review_disposition", "UNREVIEWED"),
                _json(field.get("evidence_refs") or []),
            ),
        )

    def review_fact(
        self,
        case_id: str,
        field_name: str,
        *,
        disposition: str,
        confirmed_value: Any = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM hardware_case_fact
                WHERE case_id=? AND field_name=?
                """,
                (case_id, field_name),
            ).fetchone()
            candidate = _load(existing["candidate_json"], None) if existing else None
            evidence_refs = (
                _load(existing["evidence_refs_json"], []) if existing else []
            )
            field = {
                "candidate_value": candidate,
                "confirmed_value": (
                    confirmed_value if disposition == "CONFIRMED" else None
                ),
                "review_disposition": disposition,
                "evidence_refs": evidence_refs,
            }
            self._save_fact(connection, case_id, field_name, field)
            connection.execute(
                "UPDATE hardware_case SET updated_at=CURRENT_TIMESTAMP WHERE case_id=?",
                (case_id,),
            )
        return self.get_case(case_id)["facts"][field_name]

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_case WHERE case_id=?", (case_id,)
            ).fetchone()
            if not row:
                return None
            fact_rows = connection.execute(
                """
                SELECT * FROM hardware_case_fact
                WHERE case_id=? ORDER BY field_name
                """,
                (case_id,),
            ).fetchall()
        facts = {
            item["field_name"]: {
                "candidate_value": _load(item["candidate_json"], None),
                "confirmed_value": _load(item["confirmed_json"], None),
                "review_disposition": item["review_disposition"],
                "evidence_refs": _load(item["evidence_refs_json"], []),
            }
            for item in fact_rows
        }
        return {
            "case_id": row["case_id"],
            "title": row["title"],
            "case_status": row["case_status"],
            "processing_status": row["processing_status"],
            "source_refs": _load(row["source_refs_json"], []),
            "product_context": _load(row["product_context_json"], {}),
            "facts": facts,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row["published_at"],
        }

    def list_cases(self, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        status_list = list(statuses or [])
        with self.connect() as connection:
            if status_list:
                placeholders = ",".join("?" for _ in status_list)
                rows = connection.execute(
                    f"SELECT case_id FROM hardware_case WHERE case_status IN ({placeholders}) ORDER BY created_at, case_id",
                    status_list,
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT case_id FROM hardware_case ORDER BY created_at, case_id"
                ).fetchall()
        return [
            case
            for row in rows
            if (case := self.get_case(str(row["case_id"]))) is not None
        ]

    def update_case_status(self, case_id: str, status: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hardware_case
                SET case_status=?,
                    updated_at=CURRENT_TIMESTAMP,
                    published_at=CASE
                        WHEN ?='PUBLISHED' THEN COALESCE(published_at,CURRENT_TIMESTAMP)
                        ELSE published_at
                    END
                WHERE case_id=?
                """,
                (status, status, case_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("CASE_NOT_FOUND")

    def update_processing_status(self, case_id: str, status: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hardware_case
                SET processing_status=?,updated_at=CURRENT_TIMESTAMP
                WHERE case_id=?
                """,
                (status, case_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("CASE_NOT_FOUND")

    def save_tree_node(self, node: dict[str, Any]) -> dict[str, Any]:
        node_id = str(node.get("node_id") or "").strip()
        if not node_id:
            raise ValueError("NODE_ID_REQUIRED")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_tree_node(
                    node_id,tree_type,name,parent_id,path_json,description,
                    source_ref,active,source_metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                    tree_type=excluded.tree_type,
                    name=excluded.name,
                    parent_id=excluded.parent_id,
                    path_json=excluded.path_json,
                    description=excluded.description,
                    source_ref=excluded.source_ref,
                    active=excluded.active,
                    source_metadata_json=excluded.source_metadata_json
                """,
                (
                    node_id,
                    node["tree_type"],
                    node["name"],
                    node.get("parent_id"),
                    _json(node.get("path") or []),
                    node.get("description"),
                    node.get("source_ref"),
                    int(node.get("active", True)),
                    _json(node.get("source_metadata") or {}),
                ),
            )
        return self.get_tree_node(node_id) or {}

    def get_tree_node(self, node_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_tree_node WHERE node_id=?", (node_id,)
            ).fetchone()
        if not row:
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
            "source_metadata": _load(row["source_metadata_json"], {}),
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
                    "SELECT node_id FROM hardware_tree_node ORDER BY node_id"
                ).fetchall()
        return [
            node
            for row in rows
            if (node := self.get_tree_node(str(row["node_id"]))) is not None
        ]

    def save_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            if mapping.get("relation_role") == "PRIMARY":
                connection.execute(
                    """
                    UPDATE hardware_case_mapping
                    SET relation_role='SECONDARY'
                    WHERE case_id=? AND tree_type=? AND relation_role='PRIMARY'
                    """,
                    (mapping["case_id"], mapping["tree_type"]),
                )
            connection.execute(
                """
                INSERT INTO hardware_case_mapping(
                    mapping_id,case_id,tree_type,node_id,node_path,relation_role,
                    mapping_status,confidence,basis_refs_json
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
                    mapping["mapping_id"],
                    mapping["case_id"],
                    mapping["tree_type"],
                    mapping["node_id"],
                    mapping.get("node_path"),
                    mapping["relation_role"],
                    mapping["mapping_status"],
                    mapping.get("confidence"),
                    _json(mapping.get("basis_refs") or []),
                ),
            )
        return self.get_mapping(mapping["mapping_id"]) or {}

    def get_mapping(self, mapping_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_case_mapping WHERE mapping_id=?",
                (mapping_id,),
            ).fetchone()
        return self._mapping(row) if row else None

    @staticmethod
    def _mapping(row: sqlite3.Row) -> dict[str, Any]:
        return {
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

    def list_mappings(
        self, *, case_id: str | None = None, node_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        values: list[Any] = []
        if case_id:
            where.append("case_id=?")
            values.append(case_id)
        if node_id:
            where.append("node_id=?")
            values.append(node_id)
        sql = "SELECT * FROM hardware_case_mapping"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY mapping_id"
        with self.connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        return [self._mapping(row) for row in rows]

    def save_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_case_evidence(
                    evidence_id,case_id,source_ref,evidence_type,locator_json,
                    excerpt_or_caption,evidence_status
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
                    evidence["evidence_id"],
                    evidence["case_id"],
                    evidence["source_ref"],
                    evidence["evidence_type"],
                    _json(evidence["locator"]),
                    evidence.get("excerpt_or_caption"),
                    evidence["evidence_status"],
                ),
            )
        return self.get_evidence(evidence["evidence_id"]) or {}

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_case_evidence WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
        return self._evidence(row) if row else None

    @staticmethod
    def _evidence(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "evidence_id": row["evidence_id"],
            "case_id": row["case_id"],
            "source_ref": row["source_ref"],
            "evidence_type": row["evidence_type"],
            "locator": _load(row["locator_json"], {}),
            "excerpt_or_caption": row["excerpt_or_caption"],
            "evidence_status": row["evidence_status"],
        }

    def list_evidence(self, case_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if case_id:
                rows = connection.execute(
                    """
                    SELECT * FROM hardware_case_evidence
                    WHERE case_id=? ORDER BY evidence_id
                    """,
                    (case_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM hardware_case_evidence ORDER BY evidence_id"
                ).fetchall()
        return [self._evidence(row) for row in rows]
