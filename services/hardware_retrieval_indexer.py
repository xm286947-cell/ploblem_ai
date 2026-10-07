"""Rebuildable metadata projection and generation indexer for Hardware Retrieval W2A."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from services.hardware_retrieval_index import (
    HARDWARE_RETRIEVAL_INDEX_MAPPING,
    build_index_document,
)
from services.hardware_retrieval_metadata import RETRIEVAL_METADATA_CONTRACT_VERSION

METADATA_DB_FILENAME = "hardware_retrieval_metadata.db"
METADATA_DB_SCHEMA_VERSION = 1
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class HardwareRetrievalIndexerError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _metadata_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS retrieval_generation (
    generation_id TEXT PRIMARY KEY,
    index_name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    expected_count INTEGER NOT NULL,
    indexed_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_metadata (
    generation_id TEXT NOT NULL
        REFERENCES retrieval_generation(generation_id) ON DELETE CASCADE,
    knowledge_id TEXT NOT NULL,
    business_case_id TEXT NOT NULL,
    formal_object_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    metadata_hash TEXT NOT NULL,
    PRIMARY KEY(generation_id, knowledge_id)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_metadata_case
ON retrieval_metadata(generation_id, business_case_id);

CREATE TABLE IF NOT EXISTS retrieval_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    active_generation_id TEXT
);

INSERT OR IGNORE INTO retrieval_state(singleton, active_generation_id)
VALUES(1, NULL);
"""


class HardwareRetrievalMetadataStore:
    """Durable rebuild journal; Formal Knowledge is never written here."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(_SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def begin_generation(
        self,
        *,
        generation_id: str,
        index_name: str,
        records: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        generation = str(generation_id or "").strip()
        index = str(index_name or "").strip()
        if not _GENERATION_RE.fullmatch(generation):
            raise HardwareRetrievalIndexerError("GENERATION_ID_INVALID")
        if not index:
            raise HardwareRetrievalIndexerError("INDEX_NAME_REQUIRED")

        materialized = [dict(item) for item in records]
        ids = [str(item.get("knowledge_id") or "").strip() for item in materialized]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise HardwareRetrievalIndexerError("GENERATION_KNOWLEDGE_ID_INVALID")

        now = _utc_now()
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM retrieval_generation WHERE generation_id=? OR index_name=?",
                (generation, index),
            ).fetchone()
            if exists:
                raise HardwareRetrievalIndexerError("GENERATION_ALREADY_EXISTS")
            connection.execute(
                """
                INSERT INTO retrieval_generation(
                    generation_id,index_name,status,expected_count,indexed_count,
                    error_code,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (generation, index, "BUILDING", len(materialized), 0, None, now, now),
            )
            for item in materialized:
                metadata = item.get("metadata")
                if not isinstance(metadata, Mapping):
                    raise HardwareRetrievalIndexerError("RETRIEVAL_METADATA_INVALID")
                if metadata.get("contract_version") != RETRIEVAL_METADATA_CONTRACT_VERSION:
                    raise HardwareRetrievalIndexerError(
                        "RETRIEVAL_METADATA_CONTRACT_INVALID"
                    )
                formal_hash = str(item.get("formal_object_hash") or "").strip()
                business_case_id = str(item.get("business_case_id") or "").strip()
                if (
                    len(formal_hash) != 64
                    or not business_case_id
                    or str(metadata.get("knowledge_id") or "").strip()
                    != str(item["knowledge_id"])
                ):
                    raise HardwareRetrievalIndexerError("RETRIEVAL_METADATA_IDENTITY_INVALID")
                payload = _canonical_json(metadata)
                connection.execute(
                    """
                    INSERT INTO retrieval_metadata(
                        generation_id,knowledge_id,business_case_id,
                        formal_object_hash,metadata_json,metadata_hash
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        generation,
                        str(item["knowledge_id"]),
                        business_case_id,
                        formal_hash,
                        payload,
                        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    ),
                )
        return self.get_generation(generation)

    def _transition(
        self,
        generation_id: str,
        *,
        expected_status: str,
        new_status: str,
        indexed_count: int | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM retrieval_generation WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            if row is None:
                raise HardwareRetrievalIndexerError("GENERATION_NOT_FOUND")
            if row["status"] != expected_status:
                raise HardwareRetrievalIndexerError("GENERATION_STATE_INVALID")
            count = row["indexed_count"] if indexed_count is None else int(indexed_count)
            connection.execute(
                """
                UPDATE retrieval_generation
                SET status=?,indexed_count=?,error_code=?,updated_at=?
                WHERE generation_id=?
                """,
                (new_status, count, error_code, _utc_now(), generation_id),
            )
        return self.get_generation(generation_id)

    def mark_indexed(self, generation_id: str, indexed_count: int) -> dict[str, Any]:
        generation = self.get_generation(generation_id)
        if int(indexed_count) != int(generation["expected_count"]):
            raise HardwareRetrievalIndexerError("GENERATION_COUNT_MISMATCH")
        return self._transition(
            generation_id,
            expected_status="BUILDING",
            new_status="INDEXED",
            indexed_count=int(indexed_count),
        )

    def mark_switching(self, generation_id: str) -> dict[str, Any]:
        return self._transition(
            generation_id,
            expected_status="INDEXED",
            new_status="SWITCHING",
        )

    def mark_active(self, generation_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM retrieval_generation WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            if row is None:
                raise HardwareRetrievalIndexerError("GENERATION_NOT_FOUND")
            if row["status"] != "SWITCHING":
                raise HardwareRetrievalIndexerError("GENERATION_STATE_INVALID")
            previous = connection.execute(
                "SELECT active_generation_id FROM retrieval_state WHERE singleton=1"
            ).fetchone()
            previous_id = previous["active_generation_id"] if previous else None
            if previous_id and previous_id != generation_id:
                connection.execute(
                    """
                    UPDATE retrieval_generation
                    SET status='SUPERSEDED',updated_at=?
                    WHERE generation_id=? AND status='ACTIVE'
                    """,
                    (_utc_now(), previous_id),
                )
            connection.execute(
                """
                UPDATE retrieval_generation
                SET status='ACTIVE',error_code=NULL,updated_at=?
                WHERE generation_id=?
                """,
                (_utc_now(), generation_id),
            )
            connection.execute(
                "UPDATE retrieval_state SET active_generation_id=? WHERE singleton=1",
                (generation_id,),
            )
        return self.get_generation(generation_id)

    def mark_failed(self, generation_id: str, error_code: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM retrieval_generation WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            if row is None:
                raise HardwareRetrievalIndexerError("GENERATION_NOT_FOUND")
            if row["status"] == "ACTIVE":
                raise HardwareRetrievalIndexerError("ACTIVE_GENERATION_CANNOT_FAIL")
            connection.execute(
                """
                UPDATE retrieval_generation
                SET status='FAILED',error_code=?,updated_at=?
                WHERE generation_id=?
                """,
                (str(error_code), _utc_now(), generation_id),
            )
        return self.get_generation(generation_id)

    def get_generation(self, generation_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM retrieval_generation WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
        if row is None:
            raise HardwareRetrievalIndexerError("GENERATION_NOT_FOUND")
        return dict(row)

    def active_generation(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            state = connection.execute(
                "SELECT active_generation_id FROM retrieval_state WHERE singleton=1"
            ).fetchone()
            generation_id = state["active_generation_id"] if state else None
        if not generation_id:
            return None
        return self.get_generation(str(generation_id))

    def list_metadata(self, generation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM retrieval_metadata
                WHERE generation_id=?
                ORDER BY knowledge_id
                """,
                (generation_id,),
            ).fetchall()
        return [
            {
                "generation_id": row["generation_id"],
                "knowledge_id": row["knowledge_id"],
                "business_case_id": row["business_case_id"],
                "formal_object_hash": row["formal_object_hash"],
                "metadata": json.loads(row["metadata_json"]),
                "metadata_hash": row["metadata_hash"],
            }
            for row in rows
        ]


class SearchGenerationAdapter(Protocol):
    def ensure_index(self, index_name: str, mapping: Mapping[str, Any]) -> dict[str, Any]:
        ...

    def upsert(
        self,
        index_name: str,
        document_id: str,
        document: Mapping[str, Any],
        *,
        refresh: bool = True,
    ) -> dict[str, Any]:
        ...

    def switch_alias(self, alias: str, new_index: str) -> dict[str, Any]:
        ...


class HardwareRetrievalGenerationIndexer:
    def __init__(
        self,
        adapter: SearchGenerationAdapter,
        store: HardwareRetrievalMetadataStore,
        *,
        index_prefix: str = "hardware-knowledge-search-v1",
        alias: str = "hardware-knowledge-search-active",
    ):
        self.adapter = adapter
        self.store = store
        self.index_prefix = str(index_prefix or "").strip()
        self.alias = str(alias or "").strip()
        if not self.index_prefix or not self.alias:
            raise HardwareRetrievalIndexerError("INDEXER_CONFIG_INVALID")

    def rebuild_all(
        self,
        generation_id: str,
        items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        generation = str(generation_id or "").strip()
        if not _GENERATION_RE.fullmatch(generation):
            raise HardwareRetrievalIndexerError("GENERATION_ID_INVALID")
        index_name = f"{self.index_prefix}-{generation}"

        materialized = [dict(item) for item in items]
        prepared: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in materialized:
            projection = item.get("projection")
            metadata = item.get("metadata")
            if not isinstance(projection, Mapping) or not isinstance(metadata, Mapping):
                raise HardwareRetrievalIndexerError("GENERATION_ITEM_INVALID")
            try:
                document = build_index_document(projection, metadata)
            except Exception as error:
                code = str(getattr(error, "code", None) or "INDEX_DOCUMENT_INVALID")
                raise HardwareRetrievalIndexerError(code) from error
            knowledge_id = str(document["knowledge_id"])
            if knowledge_id in seen:
                raise HardwareRetrievalIndexerError("GENERATION_KNOWLEDGE_ID_INVALID")
            seen.add(knowledge_id)
            prepared.append((document, metadata))
            records.append(
                {
                    "knowledge_id": knowledge_id,
                    "business_case_id": document["business_case_id"],
                    "formal_object_hash": document["formal_object_hash"],
                    "metadata": metadata,
                }
            )

        self.store.begin_generation(
            generation_id=generation,
            index_name=index_name,
            records=records,
        )

        try:
            ensured = self.adapter.ensure_index(
                index_name,
                HARDWARE_RETRIEVAL_INDEX_MAPPING,
            )
            if ensured.get("created") is not True:
                raise HardwareRetrievalIndexerError("INDEX_GENERATION_ALREADY_EXISTS")

            indexed_count = 0
            for document, _metadata in prepared:
                self.adapter.upsert(
                    index_name,
                    str(document["knowledge_id"]),
                    document,
                    refresh=True,
                )
                indexed_count += 1
            self.store.mark_indexed(generation, indexed_count)
            self.store.mark_switching(generation)

            switched = self.adapter.switch_alias(self.alias, index_name)
            if switched.get("acknowledged") is not True:
                raise HardwareRetrievalIndexerError("INDEX_ALIAS_SWITCH_FAILED")
        except Exception as error:
            code = str(getattr(error, "code", None) or "INDEX_GENERATION_BUILD_FAILED")
            try:
                current = self.store.get_generation(generation)
                if current["status"] != "SWITCHING":
                    self.store.mark_failed(generation, code)
                else:
                    self.store.mark_failed(generation, code)
            except Exception:
                pass
            if isinstance(error, HardwareRetrievalIndexerError):
                raise
            raise HardwareRetrievalIndexerError(code) from error

        try:
            active = self.store.mark_active(generation)
        except Exception as error:
            raise HardwareRetrievalIndexerError(
                "INDEX_ACTIVATION_RECONCILIATION_REQUIRED"
            ) from error

        return {
            "generation_id": generation,
            "index_name": index_name,
            "alias": self.alias,
            "indexed_count": len(prepared),
            "generation": active,
        }


__all__ = [
    "METADATA_DB_FILENAME",
    "METADATA_DB_SCHEMA_VERSION",
    "HardwareRetrievalGenerationIndexer",
    "HardwareRetrievalIndexerError",
    "HardwareRetrievalMetadataStore",
]
