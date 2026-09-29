"""Migration V001: canonical Hardware Case schema at W3.2 baseline."""
from __future__ import annotations

import sqlite3

from repositories.hardware_case_repository import SCHEMA as CASE_SCHEMA
from repositories.hardware_tree_import_repository import IMPORT_SCHEMA
from services.hardware_case_intake import SCHEMA as INTAKE_SCHEMA
from services.hardware_case_source_store import SCHEMA as SOURCE_SCHEMA

MIGRATION_ID = "HARDWARE-DB-0001"
SOURCE_VERSION = 0
TARGET_VERSION = 1

REQUIRED_COLUMNS = {
    "hardware_case": {
        "case_id", "title", "case_status", "processing_status",
        "source_refs_json", "product_context_json", "created_at",
        "updated_at", "published_at",
    },
    "hardware_case_fact": {
        "case_id", "field_name", "candidate_json", "confirmed_json",
        "review_disposition", "evidence_refs_json",
    },
    "hardware_tree_node": {
        "node_id", "tree_type", "business_key", "name", "parent_id",
        "path_json", "description", "source_ref", "active",
        "source_metadata_json",
    },
    "hardware_case_mapping": {
        "mapping_id", "case_id", "tree_type", "node_id", "node_path",
        "tree_version", "path_snapshot", "relation_role", "mapping_status",
        "confidence", "basis_refs_json",
    },
    "hardware_case_evidence": {
        "evidence_id", "case_id", "source_ref", "evidence_type",
        "locator_json", "excerpt_or_caption", "evidence_status",
    },
    "hardware_case_source_registry": {
        "source_ref", "source_id", "display_name", "mime_type",
        "relative_path", "size_bytes", "sha256", "source_status",
        "created_at", "updated_at",
    },
    "hardware_case_intake": {
        "intake_id", "source_ref", "source_id", "filename", "status",
        "case_id", "error_code", "created_at", "updated_at",
    },
    "hardware_tree_import_job": {
        "job_id", "contract_version", "tree_type", "import_type", "status",
        "source_filename", "source_sha256", "operator", "current_version_id",
        "applied_version_id", "mapping_profile_json", "counts_json",
        "error_code", "created_at", "updated_at",
    },
    "hardware_tree_version": {
        "version_id", "tree_type", "version_seq", "status",
        "source_job_id", "created_at", "activated_at",
    },
    "hardware_tree_version_node": {
        "version_id", "node_id", "tree_type", "business_key", "name",
        "parent_id", "path_json", "description", "source_ref", "active",
        "metadata_json",
    },
    "hardware_tree_import_change": {
        "change_id", "job_id", "change_type", "node_id", "business_key",
        "before_json", "after_json", "decision", "issue_code",
        "created_at", "updated_at",
    },
    "hardware_tree_import_issue": {
        "issue_id", "job_id", "sheet_name", "row_number", "column_name",
        "original_value", "issue_type", "suggested_action", "resolved",
        "created_at",
    },
}


def _statements(script: str) -> list[str]:
    statements: list[str] = []
    current = ""
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current += line + "\n"
        if sqlite3.complete_statement(current):
            statement = current.strip()
            current = ""
            if statement.upper().startswith("PRAGMA "):
                continue
            statements.append(statement)
    if current.strip():
        raise RuntimeError("MIGRATION_SQL_INCOMPLETE")
    return statements


def apply(connection: sqlite3.Connection) -> None:
    for script in (CASE_SCHEMA, SOURCE_SCHEMA, INTAKE_SCHEMA, IMPORT_SCHEMA):
        for statement in _statements(script):
            connection.execute(statement)

    # Legacy unversioned databases may predate these columns. The additions are
    # centralized here so repositories never perform implicit migration.
    additions = (
        ("hardware_tree_node", "business_key", "TEXT"),
        ("hardware_case_mapping", "tree_version", "TEXT"),
        ("hardware_case_mapping", "path_snapshot", "TEXT"),
    )
    for table, column, declaration in additions:
        existing = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )


__all__ = [
    "MIGRATION_ID",
    "REQUIRED_COLUMNS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply",
]
