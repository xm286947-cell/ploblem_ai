from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class _ClosingSQLiteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class RepeatQueryTraceRepository:
    """Repeat-domain persistence for immutable query snapshots only.

    This store deliberately does not own or copy ITR master data.  It persists
    exactly what was used for one Repeat query so the result can later answer
    "what information was queried at that time?".
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            factory=_ClosingSQLiteConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _load(value: str | None) -> Any:
        if value is None or value == "":
            return None
        return json.loads(value)

    def save(self, trace: dict[str, Any]) -> dict[str, Any]:
        required = {
            "query_id",
            "subject_ref",
            "itr_snapshot",
            "query_time",
            "algorithm_version",
            "correlation_id",
        }
        missing = sorted(key for key in required if not trace.get(key))
        if missing:
            raise ValueError("QUERY_TRACE_FIELDS_REQUIRED:" + ",".join(missing))

        with self.connect() as connection:
            connection.execute(
                """INSERT INTO repeat_query_trace(
                       query_id, subject_ref, itr_version, itr_snapshot_json,
                       include_missed_test, missed_test_ref, optional_context_json,
                       query_time, algorithm_version, correlation_id
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    trace["query_id"],
                    trace["subject_ref"],
                    str(trace.get("itr_version") or ""),
                    self._dump(trace["itr_snapshot"]),
                    1 if trace.get("include_missed_test") else 0,
                    trace.get("missed_test_ref"),
                    self._dump(trace.get("optional_context"))
                    if trace.get("optional_context") is not None
                    else None,
                    trace["query_time"],
                    trace["algorithm_version"],
                    trace["correlation_id"],
                ),
            )
        return self.get(trace["query_id"]) or {}

    def get(self, query_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM repeat_query_trace WHERE query_id=?",
                (query_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["include_missed_test"] = bool(result.pop("include_missed_test"))
        result["itr_snapshot"] = self._load(result.pop("itr_snapshot_json"))
        result["optional_context"] = self._load(result.pop("optional_context_json"))
        return result

    def latest_for_subject(self, subject_ref: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT query_id FROM repeat_query_trace
                   WHERE subject_ref=?
                   ORDER BY query_time DESC, rowid DESC LIMIT 1""",
                (subject_ref,),
            ).fetchone()
        return self.get(row["query_id"]) if row else None


    def save_result(self, result: dict[str, Any]) -> dict[str, Any]:
        required = {
            "query_id",
            "contract_version",
            "result_status",
            "search_status",
            "generated_at",
        }
        missing = sorted(key for key in required if not result.get(key))
        if missing:
            raise ValueError("REPEAT_RESULT_FIELDS_REQUIRED:" + ",".join(missing))

        with self.connect() as connection:
            exists = connection.execute(
                "SELECT query_id FROM repeat_result_snapshot WHERE query_id=?",
                (result["query_id"],),
            ).fetchone()
            if exists:
                raise ValueError("REPEAT_RESULT_ALREADY_EXISTS")
            connection.execute(
                """INSERT INTO repeat_result_snapshot(
                       query_id,result_version,result_status,search_status,result_json,
                       human_decision,generated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    result["query_id"],
                    result["contract_version"],
                    result["result_status"],
                    result["search_status"],
                    self._dump(result),
                    "PENDING",
                    result["generated_at"],
                ),
            )
        return self.get_result(result["query_id"]) or {}

    def get_result(self, query_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM repeat_result_snapshot WHERE query_id=?",
                (query_id,),
            ).fetchone()
        if row is None:
            return None
        raw = dict(row)
        result = self._load(raw["result_json"]) or {}
        result["human_decision"] = {
            "decision": raw["human_decision"],
            "decided_by": raw["decided_by"],
            "reason": raw["decision_reason"],
            "decided_at": raw["decided_at"],
        }
        return result

    def save_human_decision(
        self,
        query_id: str,
        decision: str,
        *,
        decided_by: str,
        reason: str = "",
        decided_at: str,
    ) -> dict[str, Any]:
        allowed = {"REPEAT", "SIMILAR", "NOT_REPEAT", "INSUFFICIENT_EVIDENCE"}
        if decision not in allowed:
            raise ValueError("INVALID_REPEAT_DECISION")
        if not str(decided_by or "").strip():
            raise ValueError("DECIDED_BY_REQUIRED")
        with self.connect() as connection:
            updated = connection.execute(
                """UPDATE repeat_result_snapshot
                   SET human_decision=?,decided_by=?,decision_reason=?,decided_at=?
                   WHERE query_id=?""",
                (decision, decided_by.strip(), reason.strip(), decided_at, query_id),
            ).rowcount
        if not updated:
            raise KeyError(query_id)
        return self.get_result(query_id) or {}
