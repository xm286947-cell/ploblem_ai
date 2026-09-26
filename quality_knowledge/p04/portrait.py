"""Customer quality portrait and immutable archive domain.

This module owns only the P04 portrait projections.  It accepts provider data
through an injected port and stores an immutable snapshot in its own repository
after archive.  It never reads another domain's repository or database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .contracts import P04State


PORTRAIT_CONTRACT = "customer-quality-portrait/v1"
ARCHIVE_CONTRACT = "customer-quality-portrait-archive/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class PortraitProvider(Protocol):
    def query(self, filters: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
        """Return live portrait inputs and a provider revision."""


class UnavailablePortraitProvider:
    def query(self, filters: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
        raise RuntimeError("PENDING_PROVIDER_CONTRACT")


class FixturePortraitProvider:
    def __init__(self, rows: Iterable[dict[str, Any]] | None = None) -> None:
        self.rows = [dict(row) for row in (rows or [
            {
                "customer_ref": "CUSTOMER-A",
                "product_ref": "PRODUCT-A",
                "industry_ref": "INDUSTRY-A",
                "quality_focus": "CORRECTNESS",
                "scenario_id": "QS-FIX-001",
                "source_problem_ids": ["PROBLEM-001"],
            },
            {
                "customer_ref": "CUSTOMER-A",
                "product_ref": "PRODUCT-B",
                "industry_ref": "INDUSTRY-A",
                "quality_focus": "RELIABILITY",
                "scenario_id": "QS-FIX-002",
                "source_problem_ids": ["PROBLEM-002"],
            },
        ])]
        self.revision = "portrait-fixture-v1"

    def query(self, filters: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
        rows = [row for row in self.rows if all(str(row.get(k) or "") == v for k, v in filters.items())]
        return [dict(row) for row in rows], self.revision

    def replace(self, rows: Iterable[dict[str, Any]], *, revision: str) -> None:
        self.rows = [dict(row) for row in rows]
        self.revision = revision


class PortraitQueryProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = PORTRAIT_CONTRACT
    state: P04State
    filters: dict[str, str] = Field(default_factory=dict)
    input_count: int = 0
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_composition: list[dict[str, Any]] = Field(default_factory=list)
    provider_revision: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ArchiveListProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = ARCHIVE_CONTRACT
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class ArchiveDetailProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = ARCHIVE_CONTRACT
    archive_id: str
    job_id: str
    filters: dict[str, Any]
    input_count: int
    input_snapshot: list[dict[str, Any]]
    result_snapshot: dict[str, Any]
    evidence_composition: list[dict[str, Any]]
    model: str
    input_hash: str
    generated_at: str
    archived_at: str
    archived: bool = True


class PortraitArchiveRepository:
    """Owns the P04 portrait job/archive persistence boundary."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS p04_portrait_job (
                    job_id TEXT PRIMARY KEY,
                    archive_id TEXT UNIQUE,
                    filters_json TEXT NOT NULL,
                    input_count INTEGER NOT NULL,
                    input_snapshot_json TEXT NOT NULL,
                    result_snapshot_json TEXT NOT NULL,
                    evidence_composition_json TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    archived_at TEXT,
                    archived INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def create_job(
        self,
        *,
        filters: dict[str, Any],
        input_snapshot: list[dict[str, Any]],
        result_snapshot: dict[str, Any],
        evidence_composition: list[dict[str, Any]],
        model: str,
        contract_version: str = PORTRAIT_CONTRACT,
        generated_at: str | None = None,
    ) -> str:
        job_id = "portrait-job-" + uuid.uuid4().hex
        generated_at = generated_at or _now()
        input_hash = hashlib.sha256(_dump({"filters": filters, "input": input_snapshot}).encode()).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO p04_portrait_job(
                    job_id, filters_json, input_count, input_snapshot_json,
                    result_snapshot_json, evidence_composition_json, model,
                    input_hash, contract_version, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, _dump(filters), len(input_snapshot), _dump(input_snapshot),
                    _dump(result_snapshot), _dump(evidence_composition), model,
                    input_hash, contract_version, generated_at,
                ),
            )
        return job_id

    def archive_job(self, job_id: str) -> str:
        archive_id = "portrait-archive-" + uuid.uuid4().hex
        archived_at = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE p04_portrait_job
                   SET archive_id=?, archived_at=?, archived=1
                 WHERE job_id=? AND archived=0
                """,
                (archive_id, archived_at, job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("ALREADY_ARCHIVED")
        return archive_id

    def list_archives(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT archive_id, job_id, filters_json, input_count, model,
                       input_hash, contract_version, generated_at, archived_at
                  FROM p04_portrait_job
                 WHERE archived=1
                 ORDER BY archived_at DESC, archive_id DESC
                """
            ).fetchall()
        return [
            {
                "archive_id": row["archive_id"],
                "job_id": row["job_id"],
                "filters": _load(row["filters_json"], {}),
                "input_count": row["input_count"],
                "model": row["model"],
                "input_hash": row["input_hash"],
                "contract_version": row["contract_version"],
                "generated_at": row["generated_at"],
                "archived_at": row["archived_at"],
            }
            for row in rows
        ]

    def get_archive(self, archive_id: str) -> ArchiveDetailProjection:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM p04_portrait_job WHERE archive_id=? AND archived=1",
                (archive_id,),
            ).fetchone()
        if row is None:
            raise ValueError("ARCHIVE_NOT_FOUND")
        return ArchiveDetailProjection(
            archive_id=row["archive_id"],
            job_id=row["job_id"],
            filters=_load(row["filters_json"], {}),
            input_count=row["input_count"],
            input_snapshot=_load(row["input_snapshot_json"], []),
            result_snapshot=_load(row["result_snapshot_json"], {}),
            evidence_composition=_load(row["evidence_composition_json"], []),
            model=row["model"],
            input_hash=row["input_hash"],
            generated_at=row["generated_at"],
            archived_at=row["archived_at"],
        )


class PortraitService:
    def __init__(self, provider: PortraitProvider, repository: PortraitArchiveRepository) -> None:
        self.provider = provider
        self.repository = repository

    def query(self, filters: dict[str, Any]) -> PortraitQueryProjection:
        normalized = {str(k): str(v) for k, v in filters.items() if v not in (None, "")}
        try:
            rows, revision = self.provider.query(normalized)
        except RuntimeError as error:
            return PortraitQueryProjection(
                state=P04State.DATA_UNAVAILABLE,
                filters=normalized,
                warnings=[str(error)],
            )
        evidence = [
            {
                "scenario_id": row.get("scenario_id"),
                "source_problem_ids": list(row.get("source_problem_ids") or []),
                "source": "PUBLISHED_QUALITYSCENARIO",
            }
            for row in rows
        ]
        result = {
            "customer_ref": normalized.get("customer_ref"),
            "product_count": len({row.get("product_ref") for row in rows if row.get("product_ref")}),
            "industry_count": len({row.get("industry_ref") for row in rows if row.get("industry_ref")}),
            "quality_focus_distribution": self._distribution(rows, "quality_focus"),
            "scenario_ids": [row.get("scenario_id") for row in rows],
        }
        return PortraitQueryProjection(
            state=P04State.NORMAL if rows else P04State.EMPTY,
            filters=normalized,
            input_count=len(rows),
            result=result,
            evidence_composition=evidence,
            provider_revision=revision,
        )

    def create_job(self, filters: dict[str, Any], *, model: str = "P04_PORTRAIT_AGGREGATOR") -> str:
        projection = self.query(filters)
        if projection.state == P04State.DATA_UNAVAILABLE:
            raise ValueError("DATA_UNAVAILABLE")
        input_rows, _ = self.provider.query({str(k): str(v) for k, v in filters.items() if v not in (None, "")})
        return self.repository.create_job(
            filters=projection.filters,
            input_snapshot=input_rows,
            result_snapshot=projection.result,
            evidence_composition=projection.evidence_composition,
            model=model,
            contract_version=PORTRAIT_CONTRACT,
        )

    def archive_job(self, job_id: str) -> dict[str, str]:
        archive_id = self.repository.archive_job(job_id)
        return {"contract_version": ARCHIVE_CONTRACT, "archive_id": archive_id, "job_id": job_id}

    @staticmethod
    def _distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key) or "")
            if value:
                result[value] = result.get(value, 0) + 1
        return result
