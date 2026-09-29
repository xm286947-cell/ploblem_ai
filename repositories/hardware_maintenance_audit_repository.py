"""Durable maintenance audit for Hardware Case domain."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS hardware_maintenance_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hardware_maintenance_audit_target
ON hardware_maintenance_audit(target_type, target_id, occurred_at);
"""


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"), default=str)


class HardwareMaintenanceAuditRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(AUDIT_SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def record(
        self,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = str(actor or "").strip()
        action = str(action or "").strip().upper()
        target_type = str(target_type or "").strip().upper()
        target_id = str(target_id or "").strip()
        if not actor or not action or not target_type or not target_id:
            raise ValueError("HARDWARE_MAINTENANCE_AUDIT_FIELDS_REQUIRED")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO hardware_maintenance_audit(
                    actor, action, target_type, target_id, details_json
                ) VALUES(?,?,?,?,?)
                """,
                (actor, action, target_type, target_id, _json(details)),
            )
            audit_id = int(cursor.lastrowid)
        return self.get(audit_id)

    def get(self, audit_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_maintenance_audit WHERE audit_id=?",
                (int(audit_id),),
            ).fetchone()
        if row is None:
            raise KeyError("HARDWARE_MAINTENANCE_AUDIT_NOT_FOUND")
        return self._row(row)

    def list(
        self,
        *,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        values: list[Any] = []
        if action:
            where.append("action=?")
            values.append(str(action).strip().upper())
        if target_type:
            where.append("target_type=?")
            values.append(str(target_type).strip().upper())
        if target_id:
            where.append("target_id=?")
            values.append(str(target_id).strip())
        sql = "SELECT * FROM hardware_maintenance_audit"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY audit_id"
        with self.connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
        return {
            "audit_id": int(row["audit_id"]),
            "actor": row["actor"],
            "action": row["action"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "details": details,
            "occurred_at": row["occurred_at"],
        }
