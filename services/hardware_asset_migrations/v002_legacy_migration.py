"""Asset schema v2: recovery journal and legacy-import provenance."""
from __future__ import annotations

import sqlite3

MIGRATION_ID = "HARDWARE-ASSET-0002-LEGACY-MIGRATION"
SOURCE_VERSION = 1
TARGET_VERSION = 2
SCHEMA_NAME = "HARDWARE_ASSET_SCHEMA_V2"

CREATE_STATEMENTS = (
    """
    CREATE TABLE hardware_candidate_review_v2 (
        review_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        before_candidate_hash TEXT CHECK(
            before_candidate_hash IS NULL OR length(before_candidate_hash) = 64
        ),
        after_candidate_hash TEXT NOT NULL CHECK(length(after_candidate_hash) = 64),
        reviewer TEXT NOT NULL,
        reason TEXT NOT NULL,
        review_record_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(candidate_id) REFERENCES hardware_candidate_asset(candidate_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_hardware_candidate_review_candidate
    ON hardware_candidate_review_v2(candidate_id, created_at)
    """,
    """
    CREATE TABLE hardware_asset_migration (
        migration_id TEXT PRIMARY KEY,
        state TEXT NOT NULL CHECK(state IN (
            'PREFLIGHT', 'BACKUP_READY', 'STAGING', 'VERIFYING', 'VERIFIED',
            'ACTIVATING', 'COMPLETED', 'FAILED'
        )),
        fingerprint_json TEXT NOT NULL,
        legacy_hardware_db_sha256 TEXT NOT NULL CHECK(length(legacy_hardware_db_sha256) = 64),
        legacy_workbench_db_sha256 TEXT NOT NULL CHECK(length(legacy_workbench_db_sha256) = 64),
        source_count INTEGER NOT NULL CHECK(source_count >= 0),
        source_hash_set_json TEXT NOT NULL,
        eligible_item_count INTEGER NOT NULL CHECK(eligible_item_count >= 0),
        promotion_record_count INTEGER NOT NULL CHECK(promotion_record_count >= 0),
        formal_reference_count INTEGER NOT NULL CHECK(formal_reference_count >= 0),
        hardware_backup_id TEXT NOT NULL,
        workbench_backup_id TEXT NOT NULL,
        skipped_item_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_item_count >= 0),
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE hardware_candidate_legacy_origin (
        migration_id TEXT NOT NULL,
        legacy_batch_id TEXT NOT NULL,
        legacy_item_id TEXT NOT NULL,
        business_case_id TEXT NOT NULL,
        source_id TEXT NOT NULL CHECK(length(source_id) = 64),
        candidate_id TEXT NOT NULL,
        legacy_candidate_hash TEXT NOT NULL CHECK(length(legacy_candidate_hash) = 64),
        selected_as_canonical INTEGER NOT NULL CHECK(selected_as_canonical IN (0,1)),
        migration_outcome TEXT NOT NULL,
        legacy_updated_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(migration_id, legacy_item_id),
        FOREIGN KEY(migration_id) REFERENCES hardware_asset_migration(migration_id)
            ON DELETE RESTRICT,
        FOREIGN KEY(candidate_id) REFERENCES hardware_candidate_asset(candidate_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_hardware_candidate_legacy_origin_candidate
    ON hardware_candidate_legacy_origin(candidate_id, migration_id)
    """,
    """
    CREATE TRIGGER hardware_candidate_review_no_update
    BEFORE UPDATE ON hardware_candidate_review
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_REVIEW_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER hardware_candidate_review_no_delete
    BEFORE DELETE ON hardware_candidate_review
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_REVIEW_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER hardware_candidate_legacy_origin_no_update
    BEFORE UPDATE ON hardware_candidate_legacy_origin
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_LEGACY_ORIGIN_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER hardware_candidate_legacy_origin_no_delete
    BEFORE DELETE ON hardware_candidate_legacy_origin
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_LEGACY_ORIGIN_IMMUTABLE');
    END
    """,
)

REQUIRED_COLUMNS = {
    "hardware_candidate_review": {
        "review_id", "candidate_id", "before_candidate_hash",
        "after_candidate_hash", "reviewer", "reason", "review_record_json",
        "created_at",
    },
    "hardware_candidate_legacy_origin": {
        "migration_id", "legacy_batch_id", "legacy_item_id",
        "business_case_id", "source_id", "candidate_id",
        "legacy_candidate_hash", "selected_as_canonical", "migration_outcome",
        "legacy_updated_at", "created_at",
    },
    "hardware_asset_migration": {
        "migration_id", "state", "fingerprint_json",
        "legacy_hardware_db_sha256", "legacy_workbench_db_sha256",
        "source_count", "source_hash_set_json", "eligible_item_count",
        "promotion_record_count", "formal_reference_count",
        "hardware_backup_id", "workbench_backup_id", "skipped_item_count",
        "error_code", "created_at", "updated_at", "completed_at",
    },
}


def apply(connection: sqlite3.Connection) -> None:
    # Rebuild the review table so imported history can truthfully retain a NULL
    # before hash when the legacy system never recorded one.
    connection.execute("DROP TRIGGER IF EXISTS hardware_candidate_review_no_update")
    connection.execute("DROP TRIGGER IF EXISTS hardware_candidate_review_no_delete")
    connection.execute("DROP INDEX IF EXISTS idx_hardware_candidate_review_candidate")
    connection.execute(
        "ALTER TABLE hardware_candidate_review RENAME TO hardware_candidate_review_v1"
    )
    connection.execute(CREATE_STATEMENTS[0])
    connection.execute(
        """
        INSERT INTO hardware_candidate_review_v2(
            review_id,candidate_id,before_candidate_hash,after_candidate_hash,
            reviewer,reason,review_record_json,created_at
        )
        SELECT review_id,candidate_id,before_candidate_hash,after_candidate_hash,
               reviewer,reason,review_record_json,created_at
        FROM hardware_candidate_review_v1
        """
    )
    connection.execute("DROP TABLE hardware_candidate_review_v1")
    connection.execute(
        "ALTER TABLE hardware_candidate_review_v2 RENAME TO hardware_candidate_review"
    )
    connection.execute(
        "CREATE INDEX idx_hardware_candidate_review_candidate "
        "ON hardware_candidate_review(candidate_id, created_at)"
    )
    for statement in CREATE_STATEMENTS[2:]:
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
