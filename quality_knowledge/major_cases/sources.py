"""Read-only access boundary for existing business sources."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable
import json
import sqlite3

from quality_knowledge.materials import normalize_itr


class BusinessSourceGateway(ABC):
    """Batch-only gateway. Implementations must never mutate the source system."""

    @abstractmethod
    def fetch_summaries(self, group_code: str, standard_itrs: Iterable[str]) -> dict[str, list[dict]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_evidence(self, group_code: str, source_type: str, record_id: str) -> dict | None:
        raise NotImplementedError


class NullBusinessSourceGateway(BusinessSourceGateway):
    def fetch_summaries(self, group_code: str, standard_itrs: Iterable[str]) -> dict[str, list[dict]]:
        return {normalize_itr(value): [] for value in standard_itrs if normalize_itr(value)}

    def fetch_evidence(self, group_code: str, source_type: str, record_id: str) -> dict | None:
        return None


class SqliteBusinessSourceGateway(BusinessSourceGateway):
    """Small, schema-tolerant adapter around the existing SQLite business DB.

    It opens SQLite with ``mode=ro`` and performs one set-based query per source
    table. Full-table text scans and per-row lookups are intentionally absent.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"BUSINESS_DATABASE_NOT_FOUND:{self.db_path}")
        connection = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _tables(connection: sqlite3.Connection) -> set[str]:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def fetch_summaries(self, group_code: str, standard_itrs: Iterable[str]) -> dict[str, list[dict]]:
        itrs = sorted({normalize_itr(value) for value in standard_itrs if normalize_itr(value)})
        result = {value: [] for value in itrs}
        if not itrs:
            return result
        placeholders = ",".join("?" for _ in itrs)
        with self._connect() as connection:
            tables = self._tables(connection)
            if "quality_issue" in tables:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(quality_issue)")}
                group_column = "group_code" if "group_code" in columns else ("business_type" if "business_type" in columns else "")
                # An unscoped legacy table is not safe to expose through a grouped
                # knowledge case. It can still be integrated later through a gateway
                # that knows the deployment-specific authorization boundary.
                if group_column:
                    select = ["knowledge_id", "business_issue_id", group_column]
                    select += [name for name in ("issue_version_id", "title", "product", "domain", "updated_at") if name in columns]
                    rows = connection.execute(
                        f"SELECT {','.join(dict.fromkeys(select))} FROM quality_issue WHERE {group_column}=? AND UPPER(business_issue_id) IN ({placeholders})",
                        [group_code, *itrs],
                    ).fetchall()
                    for row in rows:
                        item = dict(row)
                        itr = normalize_itr(item.get("business_issue_id"))
                        item.update({"source_system": "BUSINESS_DB", "source_type": "QUALITY_ISSUE", "record_id": item["knowledge_id"], "group_code": group_code})
                        result.setdefault(itr, []).append(item)
            if "source_material" in tables and "data_group" in tables:
                rows = connection.execute(
                    f"""SELECT m.material_id,m.material_type,m.canonical_itr,m.version_no,m.source_hash,
                               m.source_file,m.created_at,g.group_code
                        FROM source_material m JOIN data_group g ON g.group_id=m.group_id
                        WHERE UPPER(m.canonical_itr) IN ({placeholders}) AND g.group_code=?""",
                    [*itrs, group_code],
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    itr = normalize_itr(item.get("canonical_itr"))
                    item.update({"source_system": "BUSINESS_DB", "source_type": item.get("material_type") or "MATERIAL", "record_id": item["material_id"]})
                    result.setdefault(itr, []).append(item)
            if "business_event" in tables:  # compact contract used by isolated acceptance tests
                rows = connection.execute(
                    f"SELECT * FROM business_event WHERE group_code=? AND UPPER(standard_itr) IN ({placeholders})",
                    [group_code, *itrs],
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    itr = normalize_itr(item.get("standard_itr"))
                    item.update({"source_system": "BUSINESS_DB", "source_type": item.get("source_type") or "ITR", "record_id": item.get("record_id") or item.get("event_id")})
                    result.setdefault(itr, []).append(item)
        return result

    def fetch_evidence(self, group_code: str, source_type: str, record_id: str) -> dict | None:
        with self._connect() as connection:
            tables = self._tables(connection)
            if source_type == "QUALITY_ISSUE" and "quality_issue" in tables:
                columns = {item[1] for item in connection.execute("PRAGMA table_info(quality_issue)")}
                group_column = "group_code" if "group_code" in columns else ("business_type" if "business_type" in columns else "")
                if not group_column:
                    return None
                row = connection.execute(
                    f"SELECT * FROM quality_issue WHERE knowledge_id=? AND {group_column}=?", (record_id, group_code)
                ).fetchone()
                return dict(row) if row else None
            if "source_material" in tables:
                row = connection.execute(
                    """SELECT m.*,g.group_code FROM source_material m JOIN data_group g ON g.group_id=m.group_id
                       WHERE m.material_id=? AND g.group_code=?""",
                    (record_id, group_code),
                ).fetchone()
                if not row:
                    return None
                item = dict(row)
                if item.get("raw_json"):
                    try:
                        item["raw"] = json.loads(item.pop("raw_json"))
                    except (TypeError, json.JSONDecodeError):
                        item["raw"] = {}
                return item
            if "business_event" in tables:
                row = connection.execute(
                    "SELECT * FROM business_event WHERE record_id=? AND group_code=?", (record_id, group_code)
                ).fetchone()
                return dict(row) if row else None
        return None
