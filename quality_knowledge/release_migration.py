from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from quality_knowledge.human_analysis import HumanAnalysisRepository
from quality_knowledge.mapping import MappingConfigurationRepository
from quality_knowledge.repositories import IssueKnowledgeRepository


TOOL_VERSION = "P3-RC1"
DATA_GROUPS = {
    "knowledge": ("quality_issue", "quality_issue_version", "issue_source_raw_v1"),
    "mapping": ("mapping_config", "mapping_item", "mapping_alias", "mapping_migration_run"),
    "human_analysis": (
        "human_analysis_field_definition", "human_analysis_field_option",
        "human_analysis", "human_analysis_value", "human_analysis_audit",
    ),
    "analysis": ("analysis_run", "issue_ai_analysis", "issue_capability_gap"),
    "configuration": ("knowledge_schema_version",),
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _counts(db: Path) -> dict[str, int]:
    present = _tables(db)
    with sqlite3.connect(db) as conn:
        return {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in sorted(present) if not table.startswith("sqlite_")}


def _group_counts(counts: dict[str, int]) -> dict[str, int]:
    return {name: sum(counts.get(table, 0) for table in tables)
            for name, tables in DATA_GROUPS.items()}


class ReleaseMigration:
    """Release-gated SQLite migration: dry run report must precede apply."""

    def dry_run(self, source: str | Path, target: str | Path, report: str | Path) -> dict:
        source, target, report = Path(source).resolve(), Path(target).resolve(), Path(report).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if source == target:
            raise ValueError("source and target must be different; migration never mutates the historical database")
        with sqlite3.connect(source) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [dict(zip(("table", "rowid", "parent", "fkid"), r))
                            for r in conn.execute("PRAGMA foreign_key_check")]
        source_counts = _counts(source)
        conflicts = []
        if target.exists() and target.stat().st_size:
            conflicts.append("TARGET_ALREADY_EXISTS")
        report_data = {
            "report_version": 1,
            "migration_id": "MIG-" + uuid.uuid4().hex,
            "tool_version": TOOL_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": "DRY_RUN",
            "source": str(source),
            "target": str(target),
            "source_sha256": _sha256(source),
            "source_size": source.stat().st_size,
            "sqlite_integrity": integrity,
            "foreign_key_violations": foreign_keys,
            "table_counts": source_counts,
            "data_groups": _group_counts(source_counts),
            "conflicts": conflicts,
            "can_apply": integrity == "ok" and not foreign_keys and not conflicts,
        }
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return report_data

    def apply(self, report: str | Path) -> dict:
        report_path = Path(report).resolve()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        if data.get("mode") != "DRY_RUN" or not data.get("can_apply"):
            raise ValueError("a successful Dry Run migration report is required")
        source, target = Path(data["source"]), Path(data["target"])
        if _sha256(source) != data.get("source_sha256"):
            raise ValueError("SOURCE_CHANGED_AFTER_DRY_RUN")
        if target.exists() and target.stat().st_size:
            raise FileExistsError("target appeared after Dry Run; refusing to overwrite historical data")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".p3-staging-" + uuid.uuid4().hex)
        try:
            shutil.copy2(source, staging)
            # Current repositories perform additive, idempotent schema upgrades.
            IssueKnowledgeRepository(staging)
            MappingConfigurationRepository(staging)
            HumanAnalysisRepository(staging)
            with sqlite3.connect(staging) as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS release_migration_audit(
                    migration_id TEXT PRIMARY KEY,tool_version TEXT NOT NULL,source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,report_path TEXT NOT NULL,applied_at TEXT NOT NULL,
                    result_json TEXT NOT NULL)""")
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                fk = list(conn.execute("PRAGMA foreign_key_check"))
                if integrity != "ok" or fk:
                    raise ValueError(f"post-migration validation failed: integrity={integrity}, fk={len(fk)}")
                result = {
                    "migration_id": data["migration_id"], "migration_result": "APPLIED",
                    "tool_version": TOOL_VERSION, "source_sha256": data["source_sha256"],
                    "target": str(target), "table_counts": _counts(staging),
                    "data_groups": _group_counts(_counts(staging)),
                    "sqlite_integrity": integrity, "foreign_key_violations": 0,
                }
                conn.execute("INSERT INTO release_migration_audit VALUES(?,?,?,?,?,?,?)", (
                    data["migration_id"], TOOL_VERSION, str(source), data["source_sha256"],
                    str(report_path), datetime.now(timezone.utc).isoformat(),
                    json.dumps(result, ensure_ascii=False),
                ))
            os.replace(staging, target)
            applied_report = report_path.with_name(report_path.stem + ".applied.json")
            applied_report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["applied_report"] = str(applied_report)
            return result
        finally:
            if staging.exists():
                staging.unlink()
