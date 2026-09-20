from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from runtime.contracts import (
    AgentRequest,
    AgentResult,
    CommittedPartialResult,
    AttemptRecord,
    CheckpointRecord,
    CommitResult,
    CommittedExecution,
    ExecutionCommit,
    ExecutionDefinitionSnapshot,
    LegacyProjectionBinding,
    MergeResult,
    ProjectionOutboxEvent,
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
from runtime.reliability.errors import IdempotencyConflictError


FaultInjector = Callable[[str], None]


class SqliteTaskStore:
    """Persistent TaskStore for P0.3 D1-D3.

    D3 extends the original record store with:
    - request_id + fingerprint idempotent task creation;
    - provider-call accounting that survives process restart;
    - atomic execution commit marker + result + checkpoint + attempt/step terminal state;
    - committed execution lookup for exactly-once committed consumption;
    - same Task + new Run resume bookkeeping.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        fault_injector: FaultInjector | None = None,
    ):
        self.db_path = str(db_path)
        self.fault_injector = fault_injector
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
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
                    execution_key TEXT,
                    provider_call_seq INTEGER,
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

                CREATE TABLE IF NOT EXISTS runtime_execution_commit (
                    commit_id TEXT PRIMARY KEY,
                    execution_key TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    coverage_json TEXT,
                    evidence_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES runtime_task(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_execution_commit_task
                    ON runtime_execution_commit(task_id);

                CREATE TABLE IF NOT EXISTS runtime_merge_commit (
                    merge_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_partial_commit (
                    partial_id TEXT PRIMARY KEY,
                    execution_key TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_run_id TEXT NOT NULL,
                    partition_key TEXT,
                    partial_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_partial_task_partition
                    ON runtime_partial_commit(task_id, partition_key, created_at);

                CREATE TABLE IF NOT EXISTS runtime_execution_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_projection_outbox (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_run_id TEXT,
                    commit_id TEXT NOT NULL,
                    projection_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    UNIQUE(commit_id, projection_type)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_projection_outbox_pending
                    ON runtime_projection_outbox(status, task_id, run_id);

                CREATE TABLE IF NOT EXISTS runtime_legacy_projection_binding (
                    binding_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    legacy_analysis_set_id TEXT NOT NULL,
                    step_run_id TEXT,
                    legacy_stage TEXT,
                    projection_version TEXT NOT NULL,
                    last_applied_commit_id TEXT,
                    projection_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_legacy_binding_run
                    ON runtime_legacy_projection_binding(run_id)
                    WHERE step_run_id IS NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_legacy_binding_step
                    ON runtime_legacy_projection_binding(step_run_id)
                    WHERE step_run_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS legacy_quality_analysis_set_projection (
                    legacy_analysis_set_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    knowledge_id TEXT,
                    issue_version_id TEXT,
                    status TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS legacy_quality_stage_projection (
                    legacy_analysis_set_id TEXT NOT NULL,
                    legacy_stage TEXT NOT NULL,
                    step_run_id TEXT NOT NULL,
                    commit_id TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    evidence_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(legacy_analysis_set_id, legacy_stage)
                );
                """
            )
            self._ensure_column(conn, "runtime_attempt", "execution_key", "TEXT")
            self._ensure_column(conn, "runtime_attempt", "provider_call_seq", "INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_attempt_execution_key "
                "ON runtime_attempt(execution_key)"
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        ddl_type: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

    @staticmethod
    def _json(value: BaseModel | dict[str, Any] | list[Any] | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _upsert_task(
        conn: sqlite3.Connection,
        record: TaskRecord,
        *,
        request_json: str | None,
        result_json: str | None,
        error_json: str | None,
    ) -> None:
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
                error_json=excluded.error_json
            """,
            (
                record.task_id,
                record.request_id,
                record.request_fingerprint,
                record.task_type.value,
                record.status.value,
                record.current_run_id,
                record.model_dump_json(),
                request_json,
                result_json,
                error_json,
            ),
        )

    def save_task(
        self,
        record: TaskRecord,
        *,
        request: AgentRequest | WorkflowRequest | None = None,
        result: AgentResult | WorkflowResult | None = None,
        error: RuntimeErrorInfo | None = None,
    ) -> None:
        with self._connect() as conn:
            self._upsert_task(
                conn,
                record,
                request_json=self._json(request),
                result_json=self._json(result),
                error_json=self._json(error),
            )

    def create_or_get_task(
        self,
        record: TaskRecord,
        *,
        request: AgentRequest | WorkflowRequest,
    ) -> tuple[TaskRecord, bool]:
        request_json = self._json(request)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT record_json, request_fingerprint
                FROM runtime_task
                WHERE request_id=?
                """,
                (record.request_id,),
            ).fetchone()
            if row:
                existing = TaskRecord.model_validate_json(row["record_json"])
                if row["request_fingerprint"] != record.request_fingerprint:
                    raise IdempotencyConflictError(
                        record.request_id,
                        existing.task_id,
                    )
                return existing, False

            try:
                self._upsert_task(
                    conn,
                    record,
                    request_json=request_json,
                    result_json=None,
                    error_json=None,
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    """
                    SELECT record_json, request_fingerprint
                    FROM runtime_task
                    WHERE request_id=?
                    """,
                    (record.request_id,),
                ).fetchone()
                if not row:
                    raise
                existing = TaskRecord.model_validate_json(row["record_json"])
                if row["request_fingerprint"] != record.request_fingerprint:
                    raise IdempotencyConflictError(
                        record.request_id,
                        existing.task_id,
                    )
                return existing, False
            return record, True

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
                INSERT INTO runtime_attempt(
                    attempt_id, step_run_id, status, execution_key,
                    provider_call_seq, record_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    step_run_id=excluded.step_run_id,
                    status=excluded.status,
                    execution_key=excluded.execution_key,
                    provider_call_seq=excluded.provider_call_seq,
                    record_json=excluded.record_json
                """,
                (
                    record.attempt_id,
                    record.step_run_id,
                    record.status.value,
                    record.execution_key,
                    record.provider_call_seq,
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

    def create_resume_run(
        self,
        *,
        task_id: str,
        resume_of_run_id: str,
        workflow_id: str | None,
        workflow_version: str | None,
        input_hash: str,
        started_at: datetime,
    ) -> WorkflowRunRecord:
        existing_runs = self.list_runs(task_id)
        run_sequence = max(
            (item.run_sequence for item in existing_runs),
            default=0,
        ) + 1
        task = self.get_task(task_id)
        run = WorkflowRunRecord(
            run_id=f"run-{uuid4().hex}",
            task_id=task_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            status=RuntimeStatus.RUNNING,
            input_hash=input_hash,
            run_sequence=run_sequence,
            resume_of_run_id=resume_of_run_id,
            execution_snapshot_id=(
                task.execution_snapshot_id if task is not None else None
            ),
            started_at=started_at,
        )
        self.save_run(run)
        return run

    def commit_execution_progress(self, commit: ExecutionCommit) -> CommitResult:
        if self.fault_injector:
            self.fault_injector("before_atomic_commit")

        try:
            with self._connect() as conn:
                existing = conn.execute(
                    """
                    SELECT commit_id
                    FROM runtime_execution_commit
                    WHERE execution_key=?
                    """,
                    (commit.execution_key,),
                ).fetchone()
                if existing:
                    return CommitResult(
                        commit_id=existing["commit_id"],
                        execution_key=commit.execution_key,
                        inserted=False,
                    )

                conn.execute(
                    """
                    INSERT INTO runtime_attempt(
                        attempt_id, step_run_id, status, execution_key,
                        provider_call_seq, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(attempt_id) DO UPDATE SET
                        step_run_id=excluded.step_run_id,
                        status=excluded.status,
                        execution_key=excluded.execution_key,
                        provider_call_seq=excluded.provider_call_seq,
                        record_json=excluded.record_json
                    """,
                    (
                        commit.attempt.attempt_id,
                        commit.attempt.step_run_id,
                        commit.attempt.status.value,
                        commit.attempt.execution_key,
                        commit.attempt.provider_call_seq,
                        commit.attempt.model_dump_json(),
                    ),
                )
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
                        commit.step_run.step_run_id,
                        commit.step_run.run_id,
                        commit.step_run.step_id,
                        commit.step_run.status.value,
                        commit.step_run.model_dump_json(),
                    ),
                )
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
                        commit.checkpoint.checkpoint_id,
                        commit.checkpoint.task_id,
                        commit.checkpoint.run_id,
                        commit.checkpoint.step_run_id,
                        commit.checkpoint.status.value,
                        commit.checkpoint.model_dump_json(),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO runtime_execution_commit(
                        commit_id, execution_key, task_id, run_id, step_run_id,
                        status, result_json, coverage_json, evidence_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        commit.commit_id,
                        commit.execution_key,
                        commit.task_id,
                        commit.run_id,
                        commit.step_run.step_run_id,
                        commit.status.value,
                        self._json(commit.result_data),
                        self._json(commit.coverage),
                        self._json(commit.evidence),
                        commit.checkpoint.created_at.isoformat(),
                    ),
                )
                outbox_event = ProjectionOutboxEvent(
                    event_id=f"outbox-{commit.commit_id}",
                    task_id=commit.task_id,
                    run_id=commit.run_id,
                    step_run_id=commit.step_run.step_run_id,
                    commit_id=commit.commit_id,
                    projection_type="EXECUTION_COMMITTED",
                    payload={
                        "execution_key": commit.execution_key,
                        "status": commit.status.value,
                        "result_data": commit.result_data,
                        "evidence": [
                            item.model_dump(mode="json")
                            for item in commit.evidence
                        ],
                    },
                    created_at=commit.checkpoint.created_at,
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO runtime_projection_outbox(
                        event_id, task_id, run_id, step_run_id, commit_id,
                        projection_type, payload_json, status, attempt_count,
                        last_error, created_at, applied_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outbox_event.event_id,
                        outbox_event.task_id,
                        outbox_event.run_id,
                        outbox_event.step_run_id,
                        outbox_event.commit_id,
                        outbox_event.projection_type,
                        self._json(outbox_event.payload),
                        outbox_event.status,
                        outbox_event.attempt_count,
                        outbox_event.last_error,
                        outbox_event.created_at.isoformat(),
                        None,
                    ),
                )

                if self.fault_injector:
                    self.fault_injector("after_atomic_writes_before_commit")
        except sqlite3.IntegrityError:
            with self._connect() as conn:
                existing = conn.execute(
                    """
                    SELECT commit_id
                    FROM runtime_execution_commit
                    WHERE execution_key=?
                    """,
                    (commit.execution_key,),
                ).fetchone()
            if existing:
                return CommitResult(
                    commit_id=existing["commit_id"],
                    execution_key=commit.execution_key,
                    inserted=False,
                )
            raise

        return CommitResult(
            commit_id=commit.commit_id,
            execution_key=commit.execution_key,
            inserted=True,
        )

    def get_committed_execution(
        self,
        execution_key: str,
    ) -> CommittedExecution | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_execution_commit
                WHERE execution_key=?
                """,
                (execution_key,),
            ).fetchone()
        if not row:
            return None

        from runtime.contracts import Coverage, EvidenceReference

        coverage = (
            Coverage.model_validate_json(row["coverage_json"])
            if row["coverage_json"]
            else None
        )
        evidence = []
        if row["evidence_json"]:
            evidence = [
                EvidenceReference.model_validate(item)
                for item in json.loads(row["evidence_json"])
            ]
        return CommittedExecution(
            commit_id=row["commit_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            step_run_id=row["step_run_id"],
            execution_key=row["execution_key"],
            result_data=json.loads(row["result_json"]) if row["result_json"] else None,
            coverage=coverage,
            evidence=evidence,
            status=RuntimeStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_committed_execution_keys(
        self,
        task_id: str,
        step_run_id: str | None = None,
    ) -> set[str]:
        sql = "SELECT execution_key FROM runtime_execution_commit WHERE task_id=?"
        params: list[Any] = [task_id]
        if step_run_id is not None:
            sql += " AND step_run_id=?"
            params.append(step_run_id)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {row["execution_key"] for row in rows}

    def count_provider_calls(self, execution_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM runtime_attempt
                WHERE execution_key=? AND provider_call_seq IS NOT NULL
                """,
                (execution_key,),
            ).fetchone()
        return int(row["n"] or 0)

    def count_task_provider_calls(self, task_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM runtime_attempt a
                JOIN runtime_step_run s ON s.step_run_id=a.step_run_id
                JOIN runtime_run r ON r.run_id=s.run_id
                WHERE r.task_id=? AND a.provider_call_seq IS NOT NULL
                """,
                (task_id,),
            ).fetchone()
        return int(row["n"] or 0)

    def has_incomplete_attempt(self, execution_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runtime_attempt
                WHERE execution_key=? AND status=?
                LIMIT 1
                """,
                (execution_key, RuntimeStatus.RUNNING.value),
            ).fetchone()
        return row is not None

    def save_execution_snapshot(
        self,
        snapshot: ExecutionDefinitionSnapshot,
    ) -> ExecutionDefinitionSnapshot:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT snapshot_json FROM runtime_execution_snapshot WHERE snapshot_id=?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if existing:
                current = ExecutionDefinitionSnapshot.model_validate_json(
                    existing["snapshot_json"]
                )
                if current.fingerprint != snapshot.fingerprint:
                    raise ValueError(
                        f"immutable snapshot collision: {snapshot.snapshot_id}"
                    )
                return current
            conn.execute(
                """
                INSERT INTO runtime_execution_snapshot(
                    snapshot_id, fingerprint, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.fingerprint,
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                ),
            )
        return snapshot

    def get_execution_snapshot(
        self,
        snapshot_id: str,
    ) -> ExecutionDefinitionSnapshot:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM runtime_execution_snapshot WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"execution snapshot not found: {snapshot_id}")
        return ExecutionDefinitionSnapshot.model_validate_json(row["snapshot_json"])

    def commit_partial_result(
        self,
        *,
        task_id: str,
        run_id: str,
        step_run_id: str,
        partial: CommittedPartialResult,
    ) -> CommittedPartialResult:
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT partial_json
                FROM runtime_partial_commit
                WHERE execution_key=?
                """,
                (partial.execution_key,),
            ).fetchone()
            if existing:
                return CommittedPartialResult.model_validate_json(
                    existing["partial_json"]
                )
            try:
                conn.execute(
                    """
                    INSERT INTO runtime_partial_commit(
                        partial_id, execution_key, task_id, run_id,
                        step_run_id, partition_key, partial_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        partial.partial_id,
                        partial.execution_key,
                        task_id,
                        run_id,
                        step_run_id,
                        partial.partition_key,
                        partial.model_dump_json(),
                        partial.committed_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    """
                    SELECT partial_json
                    FROM runtime_partial_commit
                    WHERE execution_key=?
                    """,
                    (partial.execution_key,),
                ).fetchone()
                if existing:
                    return CommittedPartialResult.model_validate_json(
                        existing["partial_json"]
                    )
                raise
        return partial

    def list_partial_results(
        self,
        task_id: str,
        *,
        partition_key: str | None = None,
    ) -> list[CommittedPartialResult]:
        sql = (
            "SELECT partial_json FROM runtime_partial_commit "
            "WHERE task_id=?"
        )
        params: list[Any] = [task_id]
        if partition_key is not None:
            sql += " AND partition_key=?"
            params.append(partition_key)
        sql += " ORDER BY created_at, partial_id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            CommittedPartialResult.model_validate_json(row["partial_json"])
            for row in rows
        ]

    def get_partial_by_execution_key(
        self,
        execution_key: str,
    ) -> CommittedPartialResult | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT partial_json
                FROM runtime_partial_commit
                WHERE execution_key=?
                """,
                (execution_key,),
            ).fetchone()
        return (
            CommittedPartialResult.model_validate_json(row["partial_json"])
            if row
            else None
        )

    def get_merge_result(self, merge_key: str) -> MergeResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM runtime_merge_commit WHERE merge_key=?",
                (merge_key,),
            ).fetchone()
        return MergeResult.model_validate_json(row["result_json"]) if row else None

    def commit_merge_result(self, result: MergeResult) -> MergeResult:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT result_json FROM runtime_merge_commit WHERE merge_key=?",
                (result.merge_key,),
            ).fetchone()
            if existing:
                return MergeResult.model_validate_json(existing["result_json"])
            try:
                conn.execute(
                    """
                    INSERT INTO runtime_merge_commit(merge_key, result_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        result.merge_key,
                        result.model_dump_json(),
                        datetime.now().isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT result_json FROM runtime_merge_commit WHERE merge_key=?",
                    (result.merge_key,),
                ).fetchone()
                if existing:
                    return MergeResult.model_validate_json(existing["result_json"])
                raise
        return result

    def get_run(self, run_id: str) -> WorkflowRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM runtime_run WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return WorkflowRunRecord.model_validate_json(row["record_json"]) if row else None

    def get_step_run(self, step_run_id: str) -> StepRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM runtime_step_run WHERE step_run_id=?",
                (step_run_id,),
            ).fetchone()
        return StepRunRecord.model_validate_json(row["record_json"]) if row else None

    def list_projection_events(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        status: str = "PENDING",
    ) -> list[ProjectionOutboxEvent]:
        where = ["status=?"]
        params: list[Any] = [status]
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        if run_id is not None:
            where.append("run_id=?")
            params.append(run_id)
        sql = (
            "SELECT * FROM runtime_projection_outbox WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at, event_id"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            ProjectionOutboxEvent(
                event_id=row["event_id"],
                task_id=row["task_id"],
                run_id=row["run_id"],
                step_run_id=row["step_run_id"],
                commit_id=row["commit_id"],
                projection_type=row["projection_type"],
                payload=json.loads(row["payload_json"] or "{}"),
                status=row["status"],
                attempt_count=int(row["attempt_count"] or 0),
                last_error=row["last_error"],
                created_at=datetime.fromisoformat(row["created_at"]),
                applied_at=(
                    datetime.fromisoformat(row["applied_at"])
                    if row["applied_at"]
                    else None
                ),
            )
            for row in rows
        ]

    def mark_projection_event_applied(self, event_id: str) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runtime_projection_outbox
                SET status='APPLIED', attempt_count=attempt_count+1,
                    last_error=NULL, applied_at=?
                WHERE event_id=?
                """,
                (now, event_id),
            )

    def mark_projection_event_pending(
        self,
        event_id: str,
        error: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runtime_projection_outbox
                SET status='PENDING', attempt_count=attempt_count+1,
                    last_error=?
                WHERE event_id=?
                """,
                (error, event_id),
            )

    def save_legacy_projection_binding(
        self,
        binding: LegacyProjectionBinding,
    ) -> LegacyProjectionBinding:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_legacy_projection_binding(
                    binding_id, task_id, run_id, legacy_analysis_set_id,
                    step_run_id, legacy_stage, projection_version,
                    last_applied_commit_id, projection_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(binding_id) DO UPDATE SET
                    legacy_analysis_set_id=excluded.legacy_analysis_set_id,
                    legacy_stage=excluded.legacy_stage,
                    projection_version=excluded.projection_version,
                    last_applied_commit_id=excluded.last_applied_commit_id,
                    projection_status=excluded.projection_status,
                    updated_at=excluded.updated_at
                """,
                (
                    binding.binding_id,
                    binding.task_id,
                    binding.run_id,
                    binding.legacy_analysis_set_id,
                    binding.step_run_id,
                    binding.legacy_stage,
                    binding.projection_version,
                    binding.last_applied_commit_id,
                    binding.projection_status,
                    binding.created_at.isoformat(),
                    binding.updated_at.isoformat(),
                ),
            )
        return binding

    def get_legacy_binding_by_run(
        self,
        run_id: str,
    ) -> LegacyProjectionBinding | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM runtime_legacy_projection_binding
                WHERE run_id=? AND step_run_id IS NULL
                """,
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return LegacyProjectionBinding(
            binding_id=row["binding_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            legacy_analysis_set_id=row["legacy_analysis_set_id"],
            step_run_id=row["step_run_id"],
            legacy_stage=row["legacy_stage"],
            projection_version=row["projection_version"],
            last_applied_commit_id=row["last_applied_commit_id"],
            projection_status=row["projection_status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def ensure_legacy_analysis_set(
        self,
        *,
        legacy_analysis_set_id: str,
        task_id: str,
        run_id: str,
        knowledge_id: str | None,
        issue_version_id: str | None,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO legacy_quality_analysis_set_projection(
                    legacy_analysis_set_id, task_id, run_id, knowledge_id,
                    issue_version_id, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(legacy_analysis_set_id) DO UPDATE SET
                    status=excluded.status,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    legacy_analysis_set_id,
                    task_id,
                    run_id,
                    knowledge_id,
                    issue_version_id,
                    status,
                    self._json(metadata or {}),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM legacy_quality_analysis_set_projection
                WHERE legacy_analysis_set_id=?
                """,
                (legacy_analysis_set_id,),
            ).fetchone()
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result

    def get_legacy_analysis_set(
        self,
        legacy_analysis_set_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM legacy_quality_analysis_set_projection
                WHERE legacy_analysis_set_id=?
                """,
                (legacy_analysis_set_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result

    def upsert_legacy_stage_projection(
        self,
        *,
        legacy_analysis_set_id: str,
        legacy_stage: str,
        step_run_id: str,
        commit_id: str | None,
        status: str,
        result_data: Any,
        evidence: list[Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO legacy_quality_stage_projection(
                    legacy_analysis_set_id, legacy_stage, step_run_id,
                    commit_id, status, result_json, evidence_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(legacy_analysis_set_id, legacy_stage) DO UPDATE SET
                    step_run_id=excluded.step_run_id,
                    commit_id=excluded.commit_id,
                    status=excluded.status,
                    result_json=excluded.result_json,
                    evidence_json=excluded.evidence_json,
                    updated_at=excluded.updated_at
                """,
                (
                    legacy_analysis_set_id,
                    legacy_stage,
                    step_run_id,
                    commit_id,
                    status,
                    self._json(result_data),
                    self._json(evidence or []),
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM legacy_quality_stage_projection
                WHERE legacy_analysis_set_id=? AND legacy_stage=?
                """,
                (legacy_analysis_set_id, legacy_stage),
            ).fetchone()
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "null")
        result["evidence"] = json.loads(result.pop("evidence_json") or "[]")
        return result

    def list_legacy_stage_projections(
        self,
        legacy_analysis_set_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM legacy_quality_stage_projection
                WHERE legacy_analysis_set_id=?
                ORDER BY legacy_stage
                """,
                (legacy_analysis_set_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item.pop("result_json") or "null")
            item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
            results.append(item)
        return results

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
            pending_steps=sum(
                s.status in {RuntimeStatus.QUEUED, RuntimeStatus.WAITING, RuntimeStatus.PARTIAL}
                for s in steps
            ),
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
            cancel_requested=task.cancel_requested,
            cancel_requested_at=task.cancel_requested_at,
        )
