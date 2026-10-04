"""Asset schema v3: durable local-operation recovery journal."""
from __future__ import annotations

import sqlite3

MIGRATION_ID = "HARDWARE-ASSET-0003-SOURCE-OPERATION-JOURNAL"
SOURCE_VERSION = 2
TARGET_VERSION = 3
SCHEMA_NAME = "HARDWARE_ASSET_SCHEMA_V3"

CREATE_STATEMENTS = (
    """
    CREATE TABLE hardware_asset_operation_journal (
        operation_id TEXT PRIMARY KEY,
        operation_type TEXT NOT NULL,
        business_case_id TEXT,
        candidate_id TEXT,
        source_id TEXT,
        desired_action TEXT NOT NULL,
        operation_state TEXT NOT NULL CHECK(operation_state IN (
            'PREPARED', 'LOCAL_COMMITTED', 'REMOTE_SENT', 'OUTCOME_UNKNOWN',
            'RECONCILING', 'COMPLETED', 'FAILED', 'CANCELLED'
        )),
        request_fingerprint TEXT NOT NULL,
        remote_idempotency_key TEXT,
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        error_code TEXT,
        recovery_action TEXT
    )
    """,
    """
    CREATE INDEX idx_hardware_asset_operation_state
    ON hardware_asset_operation_journal(operation_state, operation_type, updated_at)
    """,
    """
    CREATE INDEX idx_hardware_asset_operation_asset
    ON hardware_asset_operation_journal(business_case_id, source_id, operation_state)
    """,
)

REQUIRED_COLUMNS = {
    "hardware_asset_operation_journal": {
        "operation_id", "operation_type", "business_case_id", "candidate_id",
        "source_id", "desired_action", "operation_state", "request_fingerprint",
        "remote_idempotency_key", "started_at", "updated_at", "completed_at",
        "error_code", "recovery_action",
    },
}


def apply(connection: sqlite3.Connection) -> None:
    for statement in CREATE_STATEMENTS:
        connection.execute(statement)


__all__ = [
    "CREATE_STATEMENTS",
    "MIGRATION_ID",
    "REQUIRED_COLUMNS",
    "SCHEMA_NAME",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply",
]
