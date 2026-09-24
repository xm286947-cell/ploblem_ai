"""SQLite persistence and atomic Apply for HC-TREE-IMPORT-001 M1.

The repository shares the Hardware Case SQLite file so an applied tree becomes
immediately visible to the existing P02/P05/backend consumers. Import history
and immutable Tree Version snapshots are stored separately from the current
materialized tree.
"""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
from pathlib import Path
from typing import Any

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_tree_import_contract import (
    CHANGE_DECISIONS,
    CHANGE_TYPES,
    CONTRACT_VERSION,
    HardwareTreeImportContractError,
    MUTATING_CHANGE_TYPES,
    validate_change_item,
    validate_mapping_profile,
    validate_source_filename,
    validate_source_sha256,
    validate_status_transition,
    validate_tree_type,
    version_id_for,
)


IMPORT_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS hardware_tree_import_job (
    job_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    tree_type TEXT NOT NULL,
    import_type TEXT NOT NULL,
    status TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    operator TEXT NOT NULL,
    current_version_id TEXT,
    applied_version_id TEXT,
    mapping_profile_json TEXT NOT NULL DEFAULT '{}',
    counts_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hardware_tree_import_job_type
ON hardware_tree_import_job(tree_type, created_at);

CREATE TABLE IF NOT EXISTS hardware_tree_version (
    version_id TEXT PRIMARY KEY,
    tree_type TEXT NOT NULL,
    version_seq INTEGER NOT NULL,
    status TEXT NOT NULL,
    source_job_id TEXT NOT NULL REFERENCES hardware_tree_import_job(job_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT,
    UNIQUE(tree_type, version_seq)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_hardware_tree_active_version
ON hardware_tree_version(tree_type)
WHERE status='ACTIVE';

CREATE TABLE IF NOT EXISTS hardware_tree_version_node (
    version_id TEXT NOT NULL REFERENCES hardware_tree_version(version_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    tree_type TEXT NOT NULL,
    business_key TEXT,
    name TEXT NOT NULL,
    parent_id TEXT,
    path_json TEXT NOT NULL,
    description TEXT,
    source_ref TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(version_id, node_id)
);

CREATE TABLE IF NOT EXISTS hardware_tree_import_change (
    change_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES hardware_tree_import_job(job_id) ON DELETE CASCADE,
    change_type TEXT NOT NULL,
    node_id TEXT,
    business_key TEXT,
    before_json TEXT,
    after_json TEXT,
    decision TEXT NOT NULL,
    issue_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hardware_tree_import_change_job
ON hardware_tree_import_change(job_id, change_type, decision);

CREATE TABLE IF NOT EXISTS hardware_tree_import_issue (
    issue_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES hardware_tree_import_job(job_id) ON DELETE CASCADE,
    sheet_name TEXT,
    row_number INTEGER,
    column_name TEXT,
    original_value TEXT,
    issue_type TEXT NOT NULL,
    suggested_action TEXT,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hardware_tree_import_issue_job
ON hardware_tree_import_issue(job_id, resolved);
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


class HardwareTreeImportRepository:
    """M1 durable import/version/change-set store and atomic Apply engine."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Also initializes/migrates the existing current-tree tables.
        self.case_repository = HardwareCaseRepository(self.db_path)
        with self.connect() as connection:
            connection.executescript(IMPORT_SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def get_active_version(self, tree_type: str) -> dict[str, Any] | None:
        tree_type = validate_tree_type(tree_type)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM hardware_tree_version
                WHERE tree_type=? AND status='ACTIVE'
                """,
                (tree_type,),
            ).fetchone()
        return self._version(row) if row else None

    @staticmethod
    def _version(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "version_id": row["version_id"],
            "tree_type": row["tree_type"],
            "version_seq": row["version_seq"],
            "status": row["status"],
            "source_job_id": row["source_job_id"],
            "created_at": row["created_at"],
            "activated_at": row["activated_at"],
        }

    def create_job(
        self,
        *,
        job_id: str,
        tree_type: str,
        source_filename: str,
        source_sha256: str,
        operator: str,
    ) -> dict[str, Any]:
        job_id = str(job_id or "").strip()
        operator = str(operator or "").strip()
        if not job_id:
            raise HardwareTreeImportContractError("IMPORT_JOB_ID_REQUIRED")
        if not operator:
            raise HardwareTreeImportContractError("IMPORT_OPERATOR_REQUIRED")
        tree_type = validate_tree_type(tree_type)
        source_filename = validate_source_filename(source_filename)
        source_sha256 = validate_source_sha256(source_sha256)
        active = self.get_active_version(tree_type)
        has_current_nodes = bool(self.case_repository.list_tree_nodes(tree_type))
        import_type = "UPDATE_IMPORT" if active or has_current_nodes else "INITIAL_IMPORT"
        with self.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO hardware_tree_import_job(
                        job_id,contract_version,tree_type,import_type,status,
                        source_filename,source_sha256,operator,current_version_id
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        job_id,
                        CONTRACT_VERSION,
                        tree_type,
                        import_type,
                        "UPLOADED",
                        source_filename,
                        source_sha256,
                        operator,
                        active["version_id"] if active else None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise HardwareTreeImportContractError("IMPORT_JOB_ALREADY_EXISTS") from exc
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_tree_import_job WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise HardwareTreeImportContractError("IMPORT_JOB_NOT_FOUND")
        return {
            "job_id": row["job_id"],
            "contract_version": row["contract_version"],
            "tree_type": row["tree_type"],
            "import_type": row["import_type"],
            "status": row["status"],
            "source_filename": row["source_filename"],
            "source_sha256": row["source_sha256"],
            "operator": row["operator"],
            "current_version_id": row["current_version_id"],
            "applied_version_id": row["applied_version_id"],
            "mapping_profile": _load(row["mapping_profile_json"], {}),
            "counts": _load(row["counts_json"], {}),
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_jobs(self, tree_type: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if tree_type:
                normalized = validate_tree_type(tree_type)
                rows = connection.execute(
                    """
                    SELECT job_id FROM hardware_tree_import_job
                    WHERE tree_type=? ORDER BY created_at,job_id
                    """,
                    (normalized,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT job_id FROM hardware_tree_import_job ORDER BY created_at,job_id"
                ).fetchall()
        return [self.get_job(str(row["job_id"])) for row in rows]

    def advance_status(self, job_id: str, target: str) -> dict[str, Any]:
        target = str(target or "").strip().upper()
        job = self.get_job(job_id)
        validate_status_transition(job["status"], target)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE hardware_tree_import_job
                SET status=?,error_code=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE job_id=?
                """,
                (target, job_id),
            )
        return self.get_job(job_id)

    def set_counts(self, job_id: str, counts: dict[str, Any]) -> dict[str, Any]:
        self.get_job(job_id)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE hardware_tree_import_job
                SET counts_json=?,updated_at=CURRENT_TIMESTAMP
                WHERE job_id=?
                """,
                (_json(counts), job_id),
            )
        return self.get_job(job_id)

    def clear_analysis(self, job_id: str) -> None:
        """Clear generated issues/changes before a same-job re-analysis."""
        self.get_job(job_id)
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM hardware_tree_import_change WHERE job_id=?",
                (job_id,),
            )
            connection.execute(
                "DELETE FROM hardware_tree_import_issue WHERE job_id=?",
                (job_id,),
            )
            connection.execute(
                """
                UPDATE hardware_tree_import_job
                SET counts_json='{}',error_code=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE job_id=?
                """,
                (job_id,),
            )

    def set_mapping_profile(
        self, job_id: str, profile: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = validate_mapping_profile(profile)
        self.get_job(job_id)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE hardware_tree_import_job
                SET mapping_profile_json=?,updated_at=CURRENT_TIMESTAMP
                WHERE job_id=?
                """,
                (_json(normalized), job_id),
            )
        return self.get_job(job_id)

    def add_issue(self, job_id: str, issue: dict[str, Any]) -> dict[str, Any]:
        self.get_job(job_id)
        issue_id = str(issue.get("issue_id") or "").strip()
        issue_type = str(issue.get("issue_type") or "").strip().upper()
        if not issue_id or not issue_type:
            raise HardwareTreeImportContractError("VALIDATION_ISSUE_INVALID")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_tree_import_issue(
                    issue_id,job_id,sheet_name,row_number,column_name,
                    original_value,issue_type,suggested_action,resolved
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    sheet_name=excluded.sheet_name,
                    row_number=excluded.row_number,
                    column_name=excluded.column_name,
                    original_value=excluded.original_value,
                    issue_type=excluded.issue_type,
                    suggested_action=excluded.suggested_action,
                    resolved=excluded.resolved
                """,
                (
                    issue_id,
                    job_id,
                    issue.get("sheet_name"),
                    issue.get("row_number"),
                    issue.get("column_name"),
                    issue.get("original_value"),
                    issue_type,
                    issue.get("suggested_action"),
                    int(bool(issue.get("resolved", False))),
                ),
            )
        return self.get_issue(issue_id)

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_tree_import_issue WHERE issue_id=?",
                (issue_id,),
            ).fetchone()
        if row is None:
            raise HardwareTreeImportContractError("VALIDATION_ISSUE_NOT_FOUND")
        return {
            "issue_id": row["issue_id"],
            "job_id": row["job_id"],
            "sheet_name": row["sheet_name"],
            "row_number": row["row_number"],
            "column_name": row["column_name"],
            "original_value": row["original_value"],
            "issue_type": row["issue_type"],
            "suggested_action": row["suggested_action"],
            "resolved": bool(row["resolved"]),
        }

    def list_issues(self, job_id: str) -> list[dict[str, Any]]:
        self.get_job(job_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT issue_id FROM hardware_tree_import_issue
                WHERE job_id=? ORDER BY row_number,issue_id
                """,
                (job_id,),
            ).fetchall()
        return [self.get_issue(str(row["issue_id"])) for row in rows]

    def resolve_issue(self, issue_id: str) -> dict[str, Any]:
        self.get_issue(issue_id)
        with self.connect() as connection:
            connection.execute(
                "UPDATE hardware_tree_import_issue SET resolved=1 WHERE issue_id=?",
                (issue_id,),
            )
        return self.get_issue(issue_id)

    def save_change(self, job_id: str, item: dict[str, Any]) -> dict[str, Any]:
        self.get_job(job_id)
        normalized = validate_change_item(item)
        change_id = str(item.get("change_id") or "").strip()
        if not change_id:
            raise HardwareTreeImportContractError("CHANGE_ID_REQUIRED")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_tree_import_change(
                    change_id,job_id,change_type,node_id,business_key,
                    before_json,after_json,decision,issue_code
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(change_id) DO UPDATE SET
                    change_type=excluded.change_type,
                    node_id=excluded.node_id,
                    business_key=excluded.business_key,
                    before_json=excluded.before_json,
                    after_json=excluded.after_json,
                    decision=excluded.decision,
                    issue_code=excluded.issue_code,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    change_id,
                    job_id,
                    normalized["change_type"],
                    normalized.get("node_id"),
                    normalized.get("business_key"),
                    _json(item.get("before")) if item.get("before") is not None else None,
                    _json(item.get("after")) if item.get("after") is not None else None,
                    normalized["decision"],
                    item.get("issue_code"),
                ),
            )
        return self.get_change(change_id)

    def get_change(self, change_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_tree_import_change WHERE change_id=?",
                (change_id,),
            ).fetchone()
        if row is None:
            raise HardwareTreeImportContractError("CHANGE_NOT_FOUND")
        return self._change(row)

    @staticmethod
    def _change(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "change_id": row["change_id"],
            "job_id": row["job_id"],
            "change_type": row["change_type"],
            "node_id": row["node_id"],
            "business_key": row["business_key"],
            "before": _load(row["before_json"], None),
            "after": _load(row["after_json"], None),
            "decision": row["decision"],
            "issue_code": row["issue_code"],
        }

    def list_changes(self, job_id: str) -> list[dict[str, Any]]:
        self.get_job(job_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM hardware_tree_import_change
                WHERE job_id=? ORDER BY change_id
                """,
                (job_id,),
            ).fetchall()
        return [self._change(row) for row in rows]

    def set_change_decision(
        self,
        change_id: str,
        decision: str,
        *,
        resolved_after: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.get_change(change_id)
        decision = str(decision or "").strip().upper()
        if decision not in CHANGE_DECISIONS:
            raise HardwareTreeImportContractError("CHANGE_DECISION_INVALID")
        if current["change_type"] == "CONFLICT":
            if decision not in {"RESOLVED", "EXCLUDED"}:
                raise HardwareTreeImportContractError("CONFLICT_DECISION_INVALID")
            if decision == "RESOLVED" and not isinstance(resolved_after, dict):
                raise HardwareTreeImportContractError("RESOLVED_CHANGE_AFTER_REQUIRED")
        elif decision == "RESOLVED":
            raise HardwareTreeImportContractError("RESOLVED_ONLY_FOR_CONFLICT")
        after = resolved_after if decision == "RESOLVED" else current["after"]
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE hardware_tree_import_change
                SET decision=?,after_json=?,updated_at=CURRENT_TIMESTAMP
                WHERE change_id=?
                """,
                (
                    decision,
                    _json(after) if after is not None else None,
                    change_id,
                ),
            )
        return self.get_change(change_id)

    def mark_ready_to_apply(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] != "REVIEW_REQUIRED":
            raise HardwareTreeImportContractError("IMPORT_NOT_IN_REVIEW")
        with self.connect() as connection:
            unresolved_issue = connection.execute(
                """
                SELECT 1 FROM hardware_tree_import_issue
                WHERE job_id=? AND resolved=0 LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if unresolved_issue:
            raise HardwareTreeImportContractError("UNRESOLVED_VALIDATION_ISSUES")
        for change in self.list_changes(job_id):
            change_type = change["change_type"]
            decision = change["decision"]
            if change_type == "NO_CHANGE":
                continue
            if change_type == "CONFLICT" and decision not in {"RESOLVED", "EXCLUDED"}:
                raise HardwareTreeImportContractError("UNRESOLVED_CONFLICT")
            if change_type in MUTATING_CHANGE_TYPES and decision not in {
                "CONFIRMED",
                "EXCLUDED",
            }:
                raise HardwareTreeImportContractError("CHANGE_REVIEW_INCOMPLETE")
        return self.advance_status(job_id, "READY_TO_APPLY")

    @staticmethod
    def _normalized_node(
        tree_type: str,
        node_id: str,
        raw: dict[str, Any],
        *,
        business_key: str | None = None,
    ) -> dict[str, Any]:
        name = str(raw.get("name") or "").strip()
        path = raw.get("path")
        if not name:
            raise HardwareTreeImportContractError("TREE_NODE_NAME_REQUIRED")
        if not isinstance(path, list) or not path or any(not str(x or "").strip() for x in path):
            raise HardwareTreeImportContractError("TREE_PATH_INVALID")
        raw_tree_type = str(raw.get("tree_type") or tree_type).strip().upper()
        if raw_tree_type != tree_type:
            raise HardwareTreeImportContractError("TREE_TYPE_MISMATCH")
        metadata = deepcopy(raw.get("source_metadata") or raw.get("metadata") or {})
        key = str(raw.get("business_key") or business_key or "").strip() or None
        if key:
            metadata["business_key"] = key
        return {
            "node_id": node_id,
            "tree_type": tree_type,
            "business_key": key,
            "name": name,
            "parent_id": raw.get("parent_id"),
            "path": [str(x).strip() for x in path],
            "description": raw.get("description"),
            "source_ref": raw.get("source_ref"),
            "active": bool(raw.get("active", True)),
            "source_metadata": metadata,
        }

    @staticmethod
    def _validate_tree(state: dict[str, dict[str, Any]]) -> None:
        for node_id, node in state.items():
            parent_id = node.get("parent_id")
            if not parent_id:
                continue
            if parent_id == node_id:
                raise HardwareTreeImportContractError("TREE_CYCLE")
            parent = state.get(str(parent_id))
            if parent is None:
                raise HardwareTreeImportContractError("TREE_PARENT_NOT_FOUND")
            if node.get("active", True) and not parent.get("active", True):
                raise HardwareTreeImportContractError("TREE_PARENT_INACTIVE")
            seen = {node_id}
            cursor = parent
            while cursor and cursor.get("parent_id"):
                cursor_id = str(cursor["node_id"])
                if cursor_id in seen:
                    raise HardwareTreeImportContractError("TREE_CYCLE")
                seen.add(cursor_id)
                cursor = state.get(str(cursor.get("parent_id")))
                if cursor is None:
                    raise HardwareTreeImportContractError("TREE_PARENT_NOT_FOUND")

    def apply_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] != "READY_TO_APPLY":
            raise HardwareTreeImportContractError("IMPORT_NOT_READY_TO_APPLY")
        changes = self.list_changes(job_id)
        for change in changes:
            if change["change_type"] == "CONFLICT" and change["decision"] not in {
                "RESOLVED",
                "EXCLUDED",
            }:
                raise HardwareTreeImportContractError("UNRESOLVED_CONFLICT")

        try:
            with self.connect() as connection:
                # BEGIN IMMEDIATE guarantees one all-or-nothing tree switch.
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT status,tree_type FROM hardware_tree_import_job WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None or row["status"] != "READY_TO_APPLY":
                    raise HardwareTreeImportContractError("IMPORT_NOT_READY_TO_APPLY")
                tree_type = str(row["tree_type"])
                connection.execute(
                    """
                    UPDATE hardware_tree_import_job
                    SET status='APPLYING',updated_at=CURRENT_TIMESTAMP
                    WHERE job_id=?
                    """,
                    (job_id,),
                )

                current_rows = connection.execute(
                    "SELECT * FROM hardware_tree_node WHERE tree_type=?",
                    (tree_type,),
                ).fetchall()
                state: dict[str, dict[str, Any]] = {}
                for item in current_rows:
                    metadata = _load(item["source_metadata_json"], {})
                    state[str(item["node_id"])] = {
                        "node_id": item["node_id"],
                        "tree_type": item["tree_type"],
                        "business_key": (
                            item["business_key"]
                            if "business_key" in item.keys()
                            else metadata.get("business_key")
                        ),
                        "name": item["name"],
                        "parent_id": item["parent_id"],
                        "path": _load(item["path_json"], []),
                        "description": item["description"],
                        "source_ref": item["source_ref"],
                        "active": bool(item["active"]),
                        "source_metadata": metadata,
                    }

                excluded = False
                for change in changes:
                    change_type = change["change_type"]
                    decision = change["decision"]
                    if change_type == "NO_CHANGE":
                        continue
                    if decision == "EXCLUDED":
                        excluded = True
                        continue
                    node_id = str(
                        change.get("node_id")
                        or (change.get("after") or {}).get("node_id")
                        or ""
                    ).strip()
                    if not node_id:
                        raise HardwareTreeImportContractError("CHANGE_NODE_ID_REQUIRED")

                    if change_type == "ADD":
                        if node_id in state:
                            raise HardwareTreeImportContractError("ADD_NODE_ALREADY_EXISTS")
                        state[node_id] = self._normalized_node(
                            tree_type,
                            node_id,
                            change["after"],
                            business_key=change.get("business_key"),
                        )
                    elif change_type in {"UPDATE", "RENAME", "MOVE"}:
                        if node_id not in state:
                            raise HardwareTreeImportContractError("UPDATE_NODE_NOT_FOUND")
                        merged = dict(state[node_id])
                        merged.update(change["after"] or {})
                        state[node_id] = self._normalized_node(
                            tree_type,
                            node_id,
                            merged,
                            business_key=change.get("business_key"),
                        )
                    elif change_type == "DEPRECATE":
                        if node_id not in state:
                            raise HardwareTreeImportContractError("DEPRECATE_NODE_NOT_FOUND")
                        state[node_id] = dict(state[node_id])
                        state[node_id]["active"] = False
                    elif change_type == "CONFLICT" and decision == "RESOLVED":
                        merged = dict(state.get(node_id) or {})
                        merged.update(change["after"] or {})
                        state[node_id] = self._normalized_node(
                            tree_type,
                            node_id,
                            merged,
                            business_key=change.get("business_key"),
                        )
                    else:
                        raise HardwareTreeImportContractError("CHANGE_NOT_APPLICABLE")

                self._validate_tree(state)

                seq_row = connection.execute(
                    """
                    SELECT COALESCE(MAX(version_seq),0)+1 AS next_seq
                    FROM hardware_tree_version WHERE tree_type=?
                    """,
                    (tree_type,),
                ).fetchone()
                sequence = int(seq_row["next_seq"])
                version_id = version_id_for(tree_type, sequence)

                connection.execute(
                    """
                    UPDATE hardware_tree_version
                    SET status='SUPERSEDED'
                    WHERE tree_type=? AND status='ACTIVE'
                    """,
                    (tree_type,),
                )
                connection.execute(
                    """
                    INSERT INTO hardware_tree_version(
                        version_id,tree_type,version_seq,status,source_job_id,activated_at
                    ) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                    """,
                    (version_id, tree_type, sequence, "ACTIVE", job_id),
                )

                for node in sorted(state.values(), key=lambda value: value["node_id"]):
                    connection.execute(
                        """
                        INSERT INTO hardware_tree_version_node(
                            version_id,node_id,tree_type,business_key,name,parent_id,
                            path_json,description,source_ref,active,metadata_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            version_id,
                            node["node_id"],
                            tree_type,
                            node.get("business_key"),
                            node["name"],
                            node.get("parent_id"),
                            _json(node["path"]),
                            node.get("description"),
                            node.get("source_ref"),
                            int(node.get("active", True)),
                            _json(node.get("source_metadata") or {}),
                        ),
                    )

                # Materialized current tree switch occurs in the same transaction.
                connection.execute(
                    "DELETE FROM hardware_tree_node WHERE tree_type=?",
                    (tree_type,),
                )
                for node in sorted(state.values(), key=lambda value: value["node_id"]):
                    connection.execute(
                        """
                        INSERT INTO hardware_tree_node(
                            node_id,tree_type,business_key,name,parent_id,path_json,
                            description,source_ref,active,source_metadata_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            node["node_id"],
                            tree_type,
                            node.get("business_key"),
                            node["name"],
                            node.get("parent_id"),
                            _json(node["path"]),
                            node.get("description"),
                            node.get("source_ref"),
                            int(node.get("active", True)),
                            _json(node.get("source_metadata") or {}),
                        ),
                    )

                result_status = (
                    "APPLIED_WITH_EXCLUSIONS" if excluded else "APPLIED"
                )
                counts = {
                    "change_count": len(changes),
                    "excluded_count": sum(
                        item["decision"] == "EXCLUDED" for item in changes
                    ),
                    "node_count": len(state),
                }
                connection.execute(
                    """
                    UPDATE hardware_tree_import_job
                    SET status=?,applied_version_id=?,counts_json=?,
                        error_code=NULL,updated_at=CURRENT_TIMESTAMP
                    WHERE job_id=?
                    """,
                    (result_status, version_id, _json(counts), job_id),
                )
        except HardwareTreeImportContractError as exc:
            with self.connect() as recovery:
                recovery.execute(
                    """
                    UPDATE hardware_tree_import_job
                    SET status='APPLY_FAILED',error_code=?,updated_at=CURRENT_TIMESTAMP
                    WHERE job_id=?
                    """,
                    (exc.code, job_id),
                )
            raise
        except Exception as exc:
            with self.connect() as recovery:
                recovery.execute(
                    """
                    UPDATE hardware_tree_import_job
                    SET status='APPLY_FAILED',error_code='APPLY_INTERNAL_ERROR',
                        updated_at=CURRENT_TIMESTAMP
                    WHERE job_id=?
                    """,
                    (job_id,),
                )
            raise HardwareTreeImportContractError("APPLY_INTERNAL_ERROR") from exc

        return {
            "job": self.get_job(job_id),
            "active_version": self.get_active_version(job["tree_type"]),
        }

    def list_version_nodes(self, version_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM hardware_tree_version_node
                WHERE version_id=? ORDER BY node_id
                """,
                (version_id,),
            ).fetchall()
        return [
            {
                "version_id": row["version_id"],
                "node_id": row["node_id"],
                "tree_type": row["tree_type"],
                "business_key": row["business_key"],
                "name": row["name"],
                "parent_id": row["parent_id"],
                "path": _load(row["path_json"], []),
                "description": row["description"],
                "source_ref": row["source_ref"],
                "active": bool(row["active"]),
                "metadata": _load(row["metadata_json"], {}),
            }
            for row in rows
        ]
