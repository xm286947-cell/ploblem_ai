"""Migration V002: Hardware R1 active Source binding lifecycle."""
from __future__ import annotations

import sqlite3

MIGRATION_ID = "HARDWARE-DB-0002-R1-SOURCE-BINDING"
SOURCE_VERSION = 1
TARGET_VERSION = 2

R1_SOURCE_BINDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS hardware_r1_source_binding (
    business_case_id TEXT PRIMARY KEY,
    source_ref TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    source_status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hardware_r1_source_formal_reference (
    business_case_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(business_case_id, knowledge_id)
);

CREATE TABLE IF NOT EXISTS hardware_r1_source_delete_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_case_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    deleted_by TEXT,
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hardware_r1_source_binding_source
ON hardware_r1_source_binding(source_id);

CREATE INDEX IF NOT EXISTS idx_hardware_r1_source_formal_ref_source
ON hardware_r1_source_formal_reference(source_id);
"""

REQUIRED_COLUMNS = {
    "hardware_r1_source_binding": {
        "business_case_id", "source_ref", "source_id", "source_status",
        "created_at", "updated_at",
    },
    "hardware_r1_source_formal_reference": {
        "business_case_id", "source_id", "knowledge_id", "created_at",
    },
    "hardware_r1_source_delete_audit": {
        "audit_id", "business_case_id", "source_ref", "source_id",
        "display_name", "sha256", "deleted_by", "deleted_at",
    },
}


def apply(connection: sqlite3.Connection) -> None:
    connection.executescript(R1_SOURCE_BINDING_SCHEMA)


__all__ = [
    "MIGRATION_ID",
    "REQUIRED_COLUMNS",
    "R1_SOURCE_BINDING_SCHEMA",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply",
]
