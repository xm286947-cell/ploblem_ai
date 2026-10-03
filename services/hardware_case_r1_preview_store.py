"""Local persistence for Hardware R1 Golden Preview results.

This store is deliberately separate from mature Hardware Case and formal
Knowledge persistence. It exists only to make preview results reloadable after
browser refresh and to support source_id/run_id lookup during R1 validation.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any


PREVIEW_STORE_VERSION = "hardware-r1-preview-store/v1"


class HardwareR1PreviewStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hardware_r1_preview_result (
                    preview_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    runtime_run_id TEXT,
                    business_case_id TEXT,
                    raw_title TEXT,
                    created_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hardware_r1_preview_source
                ON hardware_r1_preview_result(source_id, preview_id DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hardware_r1_preview_run
                ON hardware_r1_preview_result(runtime_run_id)
                """
            )

    @staticmethod
    def _metadata(snapshot: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        identity = snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {}
        runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        source_id = str(source.get("source_id") or identity.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("R1_PREVIEW_SOURCE_ID_REQUIRED")
        return {
            "source_id": source_id,
            "runtime_run_id": str(runtime.get("run_id") or "").strip() or None,
            "business_case_id": str(identity.get("business_case_id") or "").strip() or None,
            "raw_title": str(identity.get("raw_title") or "").strip() or None,
        }

    def save(self, snapshot: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        meta = self._metadata(snapshot, result)
        created_at = datetime.now(timezone.utc).isoformat()
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO hardware_r1_preview_result(
                    source_id, runtime_run_id, business_case_id, raw_title,
                    created_at, snapshot_json, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta["source_id"],
                    meta["runtime_run_id"],
                    meta["business_case_id"],
                    meta["raw_title"],
                    created_at,
                    snapshot_json,
                    result_json,
                ),
            )
            preview_id = int(cursor.lastrowid)

            # V1.3 trace includes Preview persistence itself. Update the same
            # immutable history row after measuring the insert; no older row is
            # overwritten and failed/partial runs remain independently visible.
            preview_save_ms = max(0, int((perf_counter() - started) * 1000))
            trace = result.setdefault("latency_trace", {})
            previous_save = trace.get("PREVIEW_SAVE_MS")
            trace["PREVIEW_SAVE_MS"] = preview_save_ms
            if previous_save is None:
                trace["TOTAL_MS"] = int(trace.get("TOTAL_MS") or 0) + preview_save_ms
            required = {
                "PARSE_MS", "MARKDOWN_MS",
                "STAGE_A_TOTAL_MS", "STAGE_A_PROVIDER_CALL_COUNT",
                "STAGE_A_PROVIDER_CALL_MS", "STAGE_A_PROMPT_TOKENS",
                "STAGE_A_COMPLETION_TOKENS", "STAGE_A_VALIDATION_RETRY_COUNT",
                "CASE_VALIDATION_MS", "CONFLICT_MS",
                "STAGE_B_TOTAL_MS", "STAGE_B_PROVIDER_CALL_COUNT",
                "STAGE_B_PROVIDER_CALL_MS", "STAGE_B_PROMPT_TOKENS",
                "STAGE_B_COMPLETION_TOKENS", "STAGE_B_VALIDATION_RETRY_COUNT",
                "REUSABLE_VALIDATION_MS", "GOLDEN_BUILD_MS",
                "PREVIEW_SAVE_MS", "TOTAL_MS",
            }
            result["latency_trace_complete"] = (
                required.issubset(trace)
                and trace.get("PREVIEW_SAVE_MS") is not None
            )
            connection.execute(
                """
                UPDATE hardware_r1_preview_result
                SET result_json=?
                WHERE preview_id=?
                """,
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    preview_id,
                ),
            )
        return {
            "preview_id": preview_id,
            "store_version": PREVIEW_STORE_VERSION,
            "created_at": created_at,
            "pipeline_status": result.get("pipeline_status"),
            **meta,
        }


    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "preview_id": int(row["preview_id"]),
            "store_version": PREVIEW_STORE_VERSION,
            "source_id": row["source_id"],
            "runtime_run_id": row["runtime_run_id"],
            "business_case_id": row["business_case_id"],
            "raw_title": row["raw_title"],
            "created_at": row["created_at"],
            "snapshot": json.loads(row["snapshot_json"]),
            "result": json.loads(row["result_json"]),
        }

    def latest(self, *, source_id: str | None = None) -> dict[str, Any] | None:
        query = """
            SELECT * FROM hardware_r1_preview_result
        """
        params: tuple[Any, ...] = ()
        if source_id:
            query += " WHERE source_id = ?"
            params = (str(source_id),)
        query += " ORDER BY preview_id DESC LIMIT 1"
        with self._connect() as connection:
            return self._row(connection.execute(query, params).fetchone())

    def delete_source(self, source_id: str) -> int:
        value = str(source_id or "").strip()
        if not value:
            return 0
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM hardware_r1_preview_result WHERE source_id=?",
                (value,),
            )
            return int(cursor.rowcount or 0)

    def by_id(self, preview_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    "SELECT * FROM hardware_r1_preview_result WHERE preview_id = ?",
                    (int(preview_id),),
                ).fetchone()
            )

    def by_run_id(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    """
                    SELECT * FROM hardware_r1_preview_result
                    WHERE runtime_run_id = ?
                    ORDER BY preview_id DESC LIMIT 1
                    """,
                    (str(run_id),),
                ).fetchone()
            )

    def list(self, *, source_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        query = """
            SELECT preview_id, source_id, runtime_run_id, business_case_id,
                   raw_title, created_at
            FROM hardware_r1_preview_result
        """
        params: list[Any] = []
        if source_id:
            query += " WHERE source_id = ?"
            params.append(str(source_id))
        query += " ORDER BY preview_id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            return [
                {
                    "preview_id": int(row["preview_id"]),
                    "store_version": PREVIEW_STORE_VERSION,
                    "source_id": row["source_id"],
                    "runtime_run_id": row["runtime_run_id"],
                    "business_case_id": row["business_case_id"],
                    "raw_title": row["raw_title"],
                    "created_at": row["created_at"],
                }
                for row in connection.execute(query, tuple(params)).fetchall()
            ]

    def delete(self, preview_id: int) -> bool:
        """Delete exactly one local Golden Preview row.

        This store owns only the dedicated *_r1_preview.db history table. It
        never deletes source files, DocumentSnapshot persistence, formal case /
        Knowledge data, Tree mappings, or Unified Runtime audit records.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM hardware_r1_preview_result WHERE preview_id = ?",
                (int(preview_id),),
            )
            return int(cursor.rowcount or 0) == 1

    def clear(self) -> int:
        """Delete all local Golden Preview rows; repeated calls are idempotent."""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM hardware_r1_preview_result")
            return max(0, int(cursor.rowcount or 0))


__all__ = ["PREVIEW_STORE_VERSION", "HardwareR1PreviewStore"]
