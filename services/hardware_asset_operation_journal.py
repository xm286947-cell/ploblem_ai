"""Durable local-operation journal shared by Source safety and Recovery."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


NONTERMINAL_STATES = frozenset(
    {"PREPARED", "LOCAL_COMMITTED", "REMOTE_SENT", "OUTCOME_UNKNOWN", "RECONCILING"}
)
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_ALLOWED_TRANSITIONS = {
    "PREPARED": NONTERMINAL_STATES | TERMINAL_STATES,
    "LOCAL_COMMITTED": NONTERMINAL_STATES | TERMINAL_STATES,
    "REMOTE_SENT": NONTERMINAL_STATES | TERMINAL_STATES,
    "OUTCOME_UNKNOWN": NONTERMINAL_STATES | TERMINAL_STATES,
    "RECONCILING": NONTERMINAL_STATES | TERMINAL_STATES,
}


class HardwareAssetOperationJournalError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _fingerprint(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise HardwareAssetOperationJournalError("OPERATION_FINGERPRINT_INVALID") from error


class HardwareAssetOperationJournal:
    """Persist source-operation intent in the versioned Asset database."""

    def __init__(self, db_path: str | Path, *, timeout_seconds: float = 5.0):
        self.db_path = Path(db_path).expanduser()
        self.timeout_seconds = float(timeout_seconds)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        try:
            fingerprint = json.loads(str(row["request_fingerprint"]))
        except (TypeError, ValueError) as error:
            raise HardwareAssetOperationJournalError("OPERATION_JOURNAL_INVALID") from error
        return {
            "operation_id": str(row["operation_id"]),
            "operation_type": str(row["operation_type"]),
            "business_case_id": row["business_case_id"],
            "candidate_id": row["candidate_id"],
            "source_id": row["source_id"],
            "desired_action": str(row["desired_action"]),
            "operation_state": str(row["operation_state"]),
            "request_fingerprint": fingerprint,
            "remote_idempotency_key": row["remote_idempotency_key"],
            "started_at": str(row["started_at"]),
            "updated_at": str(row["updated_at"]),
            "completed_at": row["completed_at"],
            "error_code": row["error_code"],
            "recovery_action": row["recovery_action"],
        }

    def prepare(
        self,
        *,
        operation_id: str,
        operation_type: str,
        business_case_id: str | None,
        candidate_id: str | None,
        source_id: str | None,
        desired_action: str,
        request_fingerprint: Mapping[str, Any],
        remote_idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        op_id = str(operation_id or "").strip()
        op_type = str(operation_type or "").strip().upper()
        action = str(desired_action or "").strip().upper()
        if not op_id or not op_type or not action:
            raise HardwareAssetOperationJournalError("OPERATION_INPUT_INVALID")
        now = _now()
        values = (
            op_id,
            op_type,
            str(business_case_id).strip() if business_case_id is not None else None,
            str(candidate_id).strip() if candidate_id is not None else None,
            str(source_id).strip().lower() if source_id is not None else None,
            action,
            _fingerprint(request_fingerprint),
            str(remote_idempotency_key).strip() if remote_idempotency_key else None,
            now,
            now,
        )
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM hardware_asset_operation_journal WHERE operation_id=?",
                    (op_id,),
                ).fetchone()
                if existing is not None:
                    result = self._public(existing)
                    if (
                        result["operation_type"] != op_type
                        or result["request_fingerprint"] != dict(request_fingerprint)
                        or result["remote_idempotency_key"]
                        != (str(remote_idempotency_key).strip() if remote_idempotency_key else None)
                    ):
                        raise HardwareAssetOperationJournalError(
                            "OPERATION_IDEMPOTENCY_CONFLICT"
                        )
                    connection.commit()
                    return result
                connection.execute(
                    """
                    INSERT INTO hardware_asset_operation_journal(
                        operation_id,operation_type,business_case_id,candidate_id,
                        source_id,desired_action,operation_state,request_fingerprint,
                        remote_idempotency_key,started_at,updated_at,completed_at,
                        error_code,recovery_action
                    ) VALUES(?,?,?,?,?,?,'PREPARED',?,?,?,?,NULL,NULL,NULL)
                    """,
                    values,
                )
                row = connection.execute(
                    "SELECT * FROM hardware_asset_operation_journal WHERE operation_id=?",
                    (op_id,),
                ).fetchone()
                connection.commit()
                return self._public(row)
        except HardwareAssetOperationJournalError:
            raise
        except sqlite3.Error as error:
            raise HardwareAssetOperationJournalError("OPERATION_JOURNAL_UNAVAILABLE") from error

    def get(self, operation_id: str) -> dict[str, Any] | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM hardware_asset_operation_journal WHERE operation_id=?",
                    (str(operation_id),),
                ).fetchone()
                return None if row is None else self._public(row)
        except HardwareAssetOperationJournalError:
            raise
        except sqlite3.Error as error:
            raise HardwareAssetOperationJournalError("OPERATION_JOURNAL_UNAVAILABLE") from error

    def transition(
        self,
        operation_id: str,
        new_state: str,
        *,
        error_code: str | None = None,
        recovery_action: str | None = None,
    ) -> dict[str, Any]:
        target = str(new_state or "").strip().upper()
        if target not in NONTERMINAL_STATES | TERMINAL_STATES:
            raise HardwareAssetOperationJournalError("OPERATION_STATE_INVALID")
        now = _now()
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM hardware_asset_operation_journal WHERE operation_id=?",
                    (str(operation_id),),
                ).fetchone()
                if row is None:
                    raise HardwareAssetOperationJournalError("OPERATION_NOT_FOUND")
                current = str(row["operation_state"])
                retrying_failed = current == "FAILED" and target == "PREPARED"
                if target != current and not retrying_failed and target not in _ALLOWED_TRANSITIONS.get(current, set()):
                    raise HardwareAssetOperationJournalError("OPERATION_STATE_TRANSITION_INVALID")
                completed_at = (
                    now if target in TERMINAL_STATES
                    else None if retrying_failed
                    else row["completed_at"]
                )
                connection.execute(
                    """
                    UPDATE hardware_asset_operation_journal
                    SET operation_state=?,updated_at=?,completed_at=?,error_code=?,recovery_action=?
                    WHERE operation_id=?
                    """,
                    (
                        target,
                        now,
                        completed_at,
                        error_code,
                        recovery_action,
                        str(operation_id),
                    ),
                )
                updated = connection.execute(
                    "SELECT * FROM hardware_asset_operation_journal WHERE operation_id=?",
                    (str(operation_id),),
                ).fetchone()
                connection.commit()
                return self._public(updated)
        except HardwareAssetOperationJournalError:
            raise
        except sqlite3.Error as error:
            raise HardwareAssetOperationJournalError("OPERATION_JOURNAL_UNAVAILABLE") from error

    def list_nonterminal(self, *, operation_type: str | None = None) -> list[dict[str, Any]]:
        states = tuple(sorted(NONTERMINAL_STATES))
        sql = (
            "SELECT * FROM hardware_asset_operation_journal "
            f"WHERE operation_state IN ({','.join('?' for _ in states)})"
        )
        params: list[Any] = list(states)
        if operation_type is not None:
            sql += " AND operation_type=?"
            params.append(str(operation_type).strip().upper())
        sql += " ORDER BY started_at,operation_id"
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(sql, params).fetchall()
                return [self._public(row) for row in rows]
        except HardwareAssetOperationJournalError:
            raise
        except sqlite3.Error as error:
            raise HardwareAssetOperationJournalError("OPERATION_JOURNAL_UNAVAILABLE") from error

    def list_operations(
        self,
        *,
        candidate_id: str | None = None,
        operation_type: str | None = None,
        states: set[str] | frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List journal entries for recovery without exposing database rows."""
        clauses: list[str] = []
        params: list[Any] = []
        if candidate_id is not None:
            clauses.append("candidate_id=?")
            params.append(str(candidate_id))
        if operation_type is not None:
            clauses.append("operation_type=?")
            params.append(str(operation_type).strip().upper())
        if states:
            normalized = tuple(sorted(str(item).strip().upper() for item in states))
            clauses.append(f"operation_state IN ({','.join('?' for _ in normalized)})")
            params.extend(normalized)
        sql = "SELECT * FROM hardware_asset_operation_journal"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at,operation_id"
        try:
            with closing(self._connect()) as connection:
                return [self._public(row) for row in connection.execute(sql, params).fetchall()]
        except HardwareAssetOperationJournalError:
            raise
        except sqlite3.Error as error:
            raise HardwareAssetOperationJournalError("OPERATION_JOURNAL_UNAVAILABLE") from error

    def has_nonterminal_asset_operation(
        self,
        *,
        business_case_id: str,
        excluding_operation_id: str | None = None,
    ) -> bool:
        """Return whether any pending operation locks this business case."""
        states = tuple(sorted(NONTERMINAL_STATES))
        sql = (
            "SELECT 1 FROM hardware_asset_operation_journal "
            f"WHERE operation_state IN ({','.join('?' for _ in states)}) "
            "AND business_case_id=?"
        )
        params: list[Any] = [*states, str(business_case_id)]
        if excluding_operation_id:
            sql += " AND operation_id<>?"
            params.append(str(excluding_operation_id))
        sql += " LIMIT 1"
        try:
            with closing(self._connect()) as connection:
                return connection.execute(sql, params).fetchone() is not None
        except sqlite3.Error as error:
            raise HardwareAssetOperationJournalError("OPERATION_JOURNAL_UNAVAILABLE") from error


__all__ = [
    "HardwareAssetOperationJournal",
    "HardwareAssetOperationJournalError",
    "NONTERMINAL_STATES",
    "TERMINAL_STATES",
]
