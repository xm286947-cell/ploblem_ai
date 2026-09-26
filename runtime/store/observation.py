"""SQLite persistence for the unified Runtime Observation contract."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from runtime.contracts import RuntimeObservation


class RuntimeObservationRepositoryError(RuntimeError):
    pass


class RuntimeObservationAlreadyExists(RuntimeObservationRepositoryError):
    pass


class RuntimeObservationRepository:
    """Persist observations in the existing application SQLite database.

    This is an additive table in the existing store.  It does not create a
    parallel runtime, provider, or evidence store.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _init_schema(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_observation (
                    observation_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    availability_status TEXT NOT NULL,
                    capture_time TEXT,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_runtime_observation_device_metric
                   ON runtime_observation(device_id, metric_name, capture_time)"""
            )

    def create(self, observation: RuntimeObservation) -> RuntimeObservation:
        payload = observation.model_dump(mode="json")
        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO runtime_observation(
                        observation_id, device_id, metric_name,
                        availability_status, capture_time, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation.observation_id,
                        observation.device_id,
                        observation.metric_name,
                        observation.availability_status.value,
                        payload.get("capture_time"),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise RuntimeObservationAlreadyExists(
                "RUNTIME_OBSERVATION_ALREADY_EXISTS"
            ) from error
        return observation

    def get(self, observation_id: str) -> RuntimeObservation | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM runtime_observation WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        return RuntimeObservation.model_validate_json(row["record_json"]) if row else None

    def list(
        self,
        *,
        device_id: str,
        metric_name: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeObservation]:
        query = "SELECT record_json FROM runtime_observation WHERE device_id = ?"
        values: list[Any] = [device_id]
        if metric_name:
            query += " AND metric_name = ?"
            values.append(metric_name)
        query += " ORDER BY capture_time DESC, observation_id DESC LIMIT ?"
        values.append(min(max(limit, 1), 500))
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [RuntimeObservation.model_validate_json(row["record_json"]) for row in rows]
