"""Fail-closed validation for the separately owned Legacy Quality Issue DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote


class LegacyDatabaseBindingError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_AUXILIARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS qc_batch_analysis_job(
 job_id TEXT PRIMARY KEY,status TEXT NOT NULL,total INTEGER NOT NULL,completed INTEGER NOT NULL DEFAULT 0,
 failed INTEGER NOT NULL DEFAULT 0,processed INTEGER NOT NULL DEFAULT 0,concurrency INTEGER NOT NULL,
 created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT,error TEXT,
 items_json TEXT NOT NULL DEFAULT '[]',result_json TEXT);
CREATE TABLE IF NOT EXISTS product_config(
 product_code TEXT PRIMARY KEY,product_name TEXT NOT NULL,product_kind TEXT NOT NULL DEFAULT 'PRODUCT',
 default_issue_domain TEXT NOT NULL DEFAULT 'AUTO',enabled INTEGER NOT NULL DEFAULT 1,
 sort_order INTEGER NOT NULL DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS human_analysis_field_definition(
 field_id TEXT PRIMARY KEY,field_key TEXT UNIQUE NOT NULL,field_name TEXT NOT NULL,description TEXT,
 field_type TEXT NOT NULL,required INTEGER DEFAULT 0,enabled INTEGER DEFAULT 1,display_order INTEGER DEFAULT 0,
 default_value TEXT,validation_rule TEXT,config_version INTEGER DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS human_analysis_field_option(
 option_id TEXT PRIMARY KEY,field_id TEXT NOT NULL,option_value TEXT NOT NULL,option_label TEXT NOT NULL,
 display_order INTEGER DEFAULT 0,enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS human_analysis(
 analysis_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL,issue_version_id TEXT NOT NULL,created_by TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(knowledge_id,issue_version_id));
CREATE TABLE IF NOT EXISTS human_analysis_value(
 analysis_id TEXT NOT NULL,field_id TEXT NOT NULL,value_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(analysis_id,field_id));
CREATE TABLE IF NOT EXISTS human_analysis_audit(
 audit_id TEXT PRIMARY KEY,analysis_id TEXT NOT NULL,field_id TEXT,old_value_json TEXT,new_value_json TEXT,
 changed_by TEXT,changed_at TEXT NOT NULL,action TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_quality_report(
 report_id TEXT PRIMARY KEY,product_code TEXT NOT NULL,start_month TEXT NOT NULL,end_month TEXT NOT NULL,
 status TEXT NOT NULL,current_version_no INTEGER NOT NULL DEFAULT 1,created_by TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_quality_report_version(
 report_version_id TEXT PRIMARY KEY,report_id TEXT NOT NULL,version_no INTEGER NOT NULL,status TEXT NOT NULL,
 scope_hash TEXT NOT NULL,report_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(report_id,version_no));
CREATE TABLE IF NOT EXISTS product_quality_report_batch_cache(
 batch_hash TEXT PRIMARY KEY,agent_id TEXT NOT NULL,model_name TEXT,summary_json TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def _expected_schema_columns() -> dict[str, set[str]]:
    """Build expected Legacy tables from repository DDL in a memory database."""
    from quality_knowledge.capability_extension import SCHEMA as capability_schema
    from quality_knowledge.mapping.repository import MAPPING_SCHEMA
    from quality_knowledge.repositories.v1_repository import SCHEMA as issue_schema

    with sqlite3.connect(":memory:") as connection:
        connection.executescript(issue_schema)
        connection.executescript(capability_schema)
        connection.executescript(MAPPING_SCHEMA)
        connection.executescript(_AUXILIARY_SCHEMA)
        migrations = {
            "quality_issue_version": {
                "mapping_config_id": "TEXT", "mapping_config_version": "INTEGER",
                "issue_domain": "TEXT DEFAULT 'AUTO'", "issue_domain_source": "TEXT DEFAULT 'AI'",
                "year": "TEXT DEFAULT ''", "year_source": "TEXT DEFAULT 'ISSUE_ID'",
                "month_source": "TEXT DEFAULT 'SOURCE_DATA'",
            },
            "issue_capability_gap": {
                "recommended_action": "TEXT", "action_type": "TEXT", "action_target": "TEXT",
                "expected_prevention_effect": "TEXT", "priority": "TEXT", "first_action": "TEXT",
                "verification_metric": "TEXT",
            },
            "analysis_run": {"analysis_profile_json": "TEXT"},
        }
        for table, additions in migrations.items():
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {sql_type}')
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        return {
            table: {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for table in tables
        }


def _normalized(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def validate_legacy_database(
    p0_db_path: str | Path,
    legacy_db_path: str | Path | None,
) -> tuple[Path | None, str | None]:
    """Return the canonical Legacy DB path and an unavailable diagnostic, if any.

    This function opens SQLite in read-only mode and never creates or migrates a
    database. A path collision is a configuration error and raises immediately.
    """

    if legacy_db_path is None or not str(legacy_db_path).strip():
        return None, "LEGACY_DB_PATH_NOT_CONFIGURED"
    p0_path = _normalized(p0_db_path)
    legacy_path = _normalized(legacy_db_path)
    if legacy_path == p0_path:
        raise LegacyDatabaseBindingError("LEGACY_P0_DATABASE_PATH_COLLISION")
    if not legacy_path.is_file():
        return legacy_path, "LEGACY_DB_MISSING"

    uri = f"file:{quote(str(legacy_path))}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if not integrity or integrity[0] != "ok":
                return legacy_path, "LEGACY_DB_SCHEMA_INCOMPATIBLE"
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            for table, required in _expected_schema_columns().items():
                if table not in tables:
                    return legacy_path, "LEGACY_DB_SCHEMA_INCOMPATIBLE"
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if not required.issubset(columns):
                    return legacy_path, "LEGACY_DB_SCHEMA_INCOMPATIBLE"
            version = connection.execute(
                "SELECT version FROM knowledge_schema_version WHERE component='mapping'"
            ).fetchone()
            if not version or int(version[0]) < 1:
                return legacy_path, "LEGACY_DB_SCHEMA_INCOMPATIBLE"
    except sqlite3.Error:
        return legacy_path, "LEGACY_DB_SCHEMA_INCOMPATIBLE"
    return legacy_path, None
