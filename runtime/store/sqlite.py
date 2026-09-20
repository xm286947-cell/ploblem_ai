from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from runtime.contracts import (
    AgentRequest,
    AgentResult,
    AttemptRecord,
    CheckpointRecord,
    RuntimeErrorInfo,
    RuntimeStatus,
    StepRunRecord,
    TaskProgress,
    TaskRecord,
    TaskSnapshot,
    TaskType,
    WorkflowRequest,
    WorkflowResult,
    WorkflowRunRecord,
)


class SqliteTaskStore:
    """Persistent P0 TaskStore.

    D1 deliberately keeps storage simple: each canonical record is persisted as
    JSON while stable identifiers/status columns remain queryable. Later D3+
    migrations can add commit/execution-key tables without changing contracts.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_task (
                    task_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    request_fingerprint TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_run_id TEXT,
                    record_json TEXT NOT NULL,
                    request_json TEXT,
                    result_json TEXT,
                    error_json TEXT
                );

                CREATE TABLE IF NOT EXISTS runtime_run (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES runtime_task(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_run_task
                    ON runtime_run(task_id);

                CREATE TABLE IF NOT EXISTS runtime_step_run (
                    step_run_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runtime_run(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_step_run_run
                    ON runtime_step_run(run_id);

                CREATE TABLE IF NOT EXISTS runtime_attempt (
                    attempt_id TEXT PRIMARY KEY,
                    step_run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(step_run_id) REFERENCES runtime_step_run(step_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_attempt_step
                    ON runtime_attempt(step_run_id);

                CREATE TABLE IF NOT EXISTS runtime_checkpoint (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES runtime_task(task_id),
                    FOREIGN KEY(run_id) REFERENCES runtime_run(run_id),
                    FOREIGN KEY(step_run_id) REFERENCES runtime_step_run(step_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_checkpoint_task
                    ON runtime_checkpoint(task_id);
                """
            )

    @staticmethod
    def _json(value: BaseModel | dict[str, Any] | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        import json
        return json.dumps(value, ensure_ascii=False, default=str)

    def save_task(
        self,
        record: TaskRecord,
        *,
        request: AgentRequest | WorkflowRequest | None = None,
        result: AgentResult | WorkflowResult | None = None,
        error: RuntimeErrorInfo | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_task(
                    task_id, request_id, request_fingerprint, task_type, status,
                    current_run_id, record_json, request_json, result_json, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    request_id=excluded.request_id,
                    request_fingerprint=excluded.request_fingerprint,
                    task_type=excluded.task_type,
                    status=excluded.status,
                    current_run_id=excluded.current_run_id,
                    record_json=excluded.record_json,
                    request_json=COALESCE(excluded.request_json, runtime_task.request_json),
                    result_json=COALESCE(excluded.result_json, runtime_task.result_json),
                    error_json=COALESCE(excluded.error_json, runtime_task.error_json)
                """,
                (
                    record.task_id,
                    record.request_id,
                    record.request_fingerprint,
                    record.task_type.value,
                    record.status.value,
                    record.current_run_id,
                    record.model_dump_json(),
                    self._json(request),
                    self._json(result),
                    self._json(error),
                ),
            )

    def save_run(self, record: WorkflowRunRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_run(run_id, task_id, status, record_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    status=excluded.status,
                    record_json=excluded.record_json
                """,
                (record.run_id, record.task_id, record.status.value, record.model_dump_json()),
            )

    def save_step_run(self, record: StepRunRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_step_run(step_run_id, run_id, step_id, status, record_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(step_run_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    step_id=excluded.step_id,
                    status=excluded.status,
                    record_json=excluded.record_json
                """,
                (
                    record.step_run_id,
                    record.run_id,
                    record.step_id,
                    record.status.value,
                    record.model_dump_json(),
                ),
            )

    def save_attempt(self, record: AttemptRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_attempt(attempt_id, step_run_id, status, record_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    step_run_id=excluded.step_run_id,
                    status=excluded.status,
                    record_json=excluded.record_json
                """,
                (
                    record.attempt_id,
                    record.step_run_id,
                    record.status.value,
                    record.model_dump_json(),
                ),
            )

    def save_checkpoint(self, record: CheckpointRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_checkpoint(
                    checkpoint_id, task_id, run_id, step_run_id, status, record_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    status=excluded.status,
                    record_json=excluded.record_json
                """,
                (
                    record.checkpoint_id,
                    record.task_id,
                    record.run_id,
                    record.step_run_id,
                    record.status.value,
                    record.model_dump_json(),
                ),
            )

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM runtime_task WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return TaskRecord.model_validate_json(row["record_json"]) if row else None

    def get_task_by_request_id(self, request_id: str) -> TaskRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM runtime_task WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return TaskRecord.model_validate_json(row["record_json"]) if row else None

    def load_request(self, task_id: str) -> AgentRequest | WorkflowRequest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT task_type, request_json FROM runtime_task WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if not row or not row["request_json"]:
            return None
        if row["task_type"] == TaskType.AGENT.value:
            return AgentRequest.model_validate_json(row["request_json"])
        return WorkflowRequest.model_validate_json(row["request_json"])

    def list_runs(self, task_id: str) -> list[WorkflowRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM runtime_run WHERE task_id=? ORDER BY rowid",
                (task_id,),
            ).fetchall()
        return [WorkflowRunRecord.model_validate_json(row["record_json"]) for row in rows]

    def list_step_runs(self, run_id: str) -> list[StepRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM runtime_step_run WHERE run_id=? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        return [StepRunRecord.model_validate_json(row["record_json"]) for row in rows]

    def list_attempts(self, step_run_id: str) -> list[AttemptRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM runtime_attempt WHERE step_run_id=? ORDER BY rowid",
                (step_run_id,),
            ).fetchall()
        return [AttemptRecord.model_validate_json(row["record_json"]) for row in rows]

    def list_checkpoints(self, task_id: str) -> list[CheckpointRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM runtime_checkpoint WHERE task_id=? ORDER BY rowid",
                (task_id,),
            ).fetchall()
        return [CheckpointRecord.model_validate_json(row["record_json"]) for row in rows]

    def get_task_snapshot(self, task_id: str) -> TaskSnapshot:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_task WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"task not found: {task_id}")

        task = TaskRecord.model_validate_json(row["record_json"])
        result = None
        if row["result_json"]:
            if task.task_type == TaskType.AGENT:
                result = AgentResult.model_validate_json(row["result_json"])
            else:
                result = WorkflowResult.model_validate_json(row["result_json"])
        error = RuntimeErrorInfo.model_validate_json(row["error_json"]) if row["error_json"] else None

        steps = self.list_step_runs(task.current_run_id) if task.current_run_id else []
        progress = TaskProgress(
            total_steps=len(steps),
            completed_steps=sum(s.status == RuntimeStatus.COMPLETED for s in steps),
            failed_steps=sum(s.status == RuntimeStatus.FAILED for s in steps),
            running_steps=sum(s.status == RuntimeStatus.RUNNING for s in steps),
            pending_steps=sum(s.status in {RuntimeStatus.QUEUED, RuntimeStatus.WAITING} for s in steps),
        )
        return TaskSnapshot(
            task_id=task.task_id,
            request_id=task.request_id,
            status=task.status,
            current_run_id=task.current_run_id,
            progress=progress,
            result=result,
            error=error,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
