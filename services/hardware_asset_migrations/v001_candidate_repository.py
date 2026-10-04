"""Initial frozen schema for Hardware Candidate Assets."""
from __future__ import annotations

import sqlite3

MIGRATION_ID = "HARDWARE-ASSET-0001-CANDIDATE-REPOSITORY"
SOURCE_VERSION = 0
TARGET_VERSION = 1
SCHEMA_NAME = "HARDWARE_ASSET_SCHEMA_V1"

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS hardware_asset_schema_version (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        schema_version INTEGER NOT NULL,
        schema_name TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hardware_asset_schema_migration (
        migration_id TEXT PRIMARY KEY,
        source_version INTEGER NOT NULL,
        target_version INTEGER NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hardware_candidate_asset (
        candidate_id TEXT PRIMARY KEY,
        business_case_id TEXT NOT NULL,
        source_id TEXT NOT NULL CHECK(length(source_id) = 64),
        source_ref TEXT NOT NULL,
        candidate_hash TEXT NOT NULL CHECK(length(candidate_hash) = 64),
        knowledge_object_json TEXT NOT NULL,
        generation_run_id TEXT,
        pipeline_version TEXT NOT NULL,
        agent_config_version TEXT NOT NULL,
        knowledge_schema_version TEXT NOT NULL,
        validator_version TEXT NOT NULL,
        asset_status TEXT NOT NULL
            CHECK(asset_status IN ('ACTIVE', 'INVALIDATED')),
        production_review_status TEXT NOT NULL
            CHECK(production_review_status IN ('NOT_REQUIRED', 'REQUIRED', 'RESOLVED')),
        promotion_status TEXT NOT NULL CHECK(promotion_status IN (
            'NOT_STARTED', 'PRECHECK_PASS', 'CANDIDATE_INTAKED', 'INTAKE_FAILED',
            'REVIEW_CONFIRMED', 'REVIEW_FAILED', 'PUBLISHED_PENDING_QUERY_BACK',
            'PUBLISH_FAILED', 'VERIFIED', 'VERIFY_FAILED'
        )),
        row_version INTEGER NOT NULL CHECK(row_version >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(business_case_id, source_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hardware_candidate_asset_case_status
    ON hardware_candidate_asset(business_case_id, asset_status, updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hardware_candidate_asset_source
    ON hardware_candidate_asset(source_id, updated_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS hardware_candidate_evidence_ref (
        candidate_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        source_id TEXT NOT NULL CHECK(length(source_id) = 64),
        source_ref TEXT NOT NULL,
        block_id TEXT NOT NULL,
        locator_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(candidate_id, evidence_id),
        UNIQUE(candidate_id, block_id),
        FOREIGN KEY(candidate_id) REFERENCES hardware_candidate_asset(candidate_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hardware_candidate_review (
        review_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        before_candidate_hash TEXT NOT NULL CHECK(length(before_candidate_hash) = 64),
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
    CREATE INDEX IF NOT EXISTS idx_hardware_candidate_review_candidate
    ON hardware_candidate_review(candidate_id, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS hardware_candidate_event (
        event_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN (
            'CREATED', 'REGENERATED', 'PRODUCTION_REVIEWED',
            'PROMOTION_STARTED', 'INVALIDATED', 'REACTIVATED'
        )),
        old_candidate_hash TEXT,
        new_candidate_hash TEXT,
        run_id TEXT,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(candidate_id) REFERENCES hardware_candidate_asset(candidate_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hardware_candidate_event_candidate
    ON hardware_candidate_event(candidate_id, created_at, event_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS hardware_asset_promotion (
        asset_candidate_id TEXT PRIMARY KEY,
        knowledge_candidate_id TEXT,
        knowledge_id TEXT,
        public_ref TEXT,
        promotion_status TEXT NOT NULL CHECK(promotion_status IN (
            'PRECHECK_PASS', 'CANDIDATE_INTAKED', 'INTAKE_FAILED',
            'REVIEW_CONFIRMED', 'REVIEW_FAILED', 'PUBLISHED_PENDING_QUERY_BACK',
            'PUBLISH_FAILED', 'VERIFIED', 'VERIFY_FAILED'
        )),
        formal_review_status TEXT,
        source_id TEXT NOT NULL CHECK(length(source_id) = 64),
        business_case_id TEXT NOT NULL,
        last_action TEXT NOT NULL,
        error_code TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
        origin_batch_id TEXT,
        origin_item_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(asset_candidate_id) REFERENCES hardware_candidate_asset(candidate_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hardware_candidate_event_no_update
    BEFORE UPDATE ON hardware_candidate_event
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_EVENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hardware_candidate_event_no_delete
    BEFORE DELETE ON hardware_candidate_event
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_EVENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hardware_candidate_review_no_update
    BEFORE UPDATE ON hardware_candidate_review
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_REVIEW_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hardware_candidate_review_no_delete
    BEFORE DELETE ON hardware_candidate_review
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_REVIEW_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS hardware_candidate_asset_no_delete
    BEFORE DELETE ON hardware_candidate_asset
    BEGIN
        SELECT RAISE(ABORT, 'HARDWARE_CANDIDATE_ASSET_DELETE_FORBIDDEN');
    END
    """,
)

REQUIRED_COLUMNS = {
    "hardware_asset_schema_version": {
        "singleton", "schema_version", "schema_name", "updated_at",
    },
    "hardware_asset_schema_migration": {
        "migration_id", "source_version", "target_version", "applied_at",
    },
    "hardware_candidate_asset": {
        "candidate_id", "business_case_id", "source_id", "source_ref",
        "candidate_hash", "knowledge_object_json", "generation_run_id",
        "pipeline_version", "agent_config_version", "knowledge_schema_version",
        "validator_version", "asset_status", "production_review_status",
        "promotion_status", "row_version", "created_at", "updated_at",
    },
    "hardware_candidate_evidence_ref": {
        "candidate_id", "evidence_id", "source_id", "source_ref", "block_id",
        "locator_json", "created_at",
    },
    "hardware_candidate_review": {
        "review_id", "candidate_id", "before_candidate_hash",
        "after_candidate_hash", "reviewer", "reason", "review_record_json",
        "created_at",
    },
    "hardware_candidate_event": {
        "event_id", "candidate_id", "event_type", "old_candidate_hash",
        "new_candidate_hash", "run_id", "actor", "reason", "created_at",
    },
    "hardware_asset_promotion": {
        "asset_candidate_id", "knowledge_candidate_id", "knowledge_id",
        "public_ref", "promotion_status", "formal_review_status", "source_id",
        "business_case_id", "last_action", "error_code", "retry_count",
        "origin_batch_id", "origin_item_id", "created_at", "updated_at",
    },
}


def apply(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)


__all__ = [
    "MIGRATION_ID",
    "REQUIRED_COLUMNS",
    "SCHEMA_NAME",
    "SCHEMA_STATEMENTS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply",
]
