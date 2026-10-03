from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from services.hardware_asset_repository import (
    ASSET_SCHEMA_VERSION,
    CandidateAssetRepository,
    CandidateAssetRepositoryError,
    candidate_content_hash,
    candidate_identity,
    deterministic_evidence_id,
)
from services.hardware_asset_migrations import v001_candidate_repository


CASE_ID = "A0152"
SOURCE_ID = "a" * 64
SOURCE_REF = "HCSRC-0152"
VERSIONS = {
    "pipeline_version": "hardware-r1-agent-pipeline/v1.5",
    "agent_config_version": "hardware-case-reuse/v3",
    "knowledge_schema_version": "hardware-case-knowledge-object/v1",
    "validator_version": "hardware-r1-validator/v1",
}


def _knowledge_object(
    *,
    subject: str = "UART output configuration",
    conflict: bool = False,
) -> dict:
    return {
        "contract_version": "hardware-case-knowledge-object/v1",
        "identity": {"business_case_id": CASE_ID, "raw_title": "UART issue"},
        "source_fact": {"source_id": SOURCE_ID, "business_case_id": CASE_ID},
        "engineering_context": {"primary_subject": {"value": subject}},
        "evidence": [
            {
                "block_id": "B0001",
                "block_type": "PARAGRAPH",
                "source_locator": {"paragraph": 1, "section_path": ["3", "4"]},
                "text": "UART output configuration",
            }
        ],
        "conflicts": (
            [
                {
                    "conflict_id": "C-1",
                    "status": "OPEN",
                    "resolution_status": "NEEDS_REVIEW",
                }
            ]
            if conflict
            else []
        ),
        "review": {
            "object_status": "CANDIDATE",
            "reviewer": None,
            "reviewed_at": None,
            "field_decisions": [],
        },
    }


def _create(
    repo: CandidateAssetRepository,
    *,
    knowledge_object: dict | None = None,
    run_id: str = "run-1",
    source_ref: str = SOURCE_REF,
) -> dict:
    return repo.create_or_commit_candidate(
        business_case_id=CASE_ID,
        source_id=SOURCE_ID,
        source_ref=source_ref,
        knowledge_object=knowledge_object or _knowledge_object(),
        generation_run_id=run_id,
        **VERSIONS,
    )


def _ready_repo(tmp_path: Path) -> tuple[CandidateAssetRepository, Path]:
    db_path = tmp_path / "db" / "hardware_asset.db"
    repository = CandidateAssetRepository(db_path)
    result = repository.initialize()
    assert result == {"schema_version": ASSET_SCHEMA_VERSION, "status": "READY"}
    return repository, db_path


def _query(db_path: Path, sql: str, params: tuple = ()):
    with sqlite3.connect(db_path) as connection:
        return connection.execute(sql, params).fetchall()


def test_asset_schema_v2_initializes_idempotently_and_records_migration(tmp_path):
    repository, db_path = _ready_repo(tmp_path)

    assert repository.schema_version() == ASSET_SCHEMA_VERSION == 2
    assert repository.initialize()["schema_version"] == ASSET_SCHEMA_VERSION
    assert db_path.is_file() and db_path.stat().st_size > 0
    assert _query(
        db_path,
        "SELECT schema_version FROM hardware_asset_schema_version WHERE singleton=1",
    ) == [(ASSET_SCHEMA_VERSION,)]
    assert _query(
        db_path,
        "SELECT source_version,target_version FROM hardware_asset_schema_migration "
        "ORDER BY target_version",
    ) == [(0, 1), (1, 2)]
    assert set(row[0] for row in _query(
        db_path,
        "SELECT name FROM sqlite_master WHERE type='table'",
    )) >= {
        "hardware_candidate_asset",
        "hardware_candidate_evidence_ref",
        "hardware_candidate_review",
        "hardware_candidate_event",
        "hardware_asset_promotion",
        "hardware_candidate_legacy_origin",
        "hardware_asset_migration",
    }
    review_columns = {
        row[1]: row
        for row in _query(db_path, "PRAGMA table_info(hardware_candidate_review)")
    }
    assert review_columns["before_candidate_hash"][3] == 0


def test_v0_asset_schema_migrates_deterministically(tmp_path):
    db_path = tmp_path / "hardware_asset.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE hardware_asset_schema_version (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                schema_version INTEGER NOT NULL,
                schema_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO hardware_asset_schema_version VALUES(1,0,'HARDWARE_ASSET_SCHEMA_V0','old')"
        )

    repository = CandidateAssetRepository(db_path)
    assert repository.initialize()["schema_version"] == ASSET_SCHEMA_VERSION == 2
    assert repository.schema_version() == ASSET_SCHEMA_VERSION
    assert _query(
        db_path,
        "SELECT source_version,target_version FROM hardware_asset_schema_migration "
        "ORDER BY target_version",
    ) == [(0, 1), (1, 2)]


def test_populated_v1_database_migrates_review_history_without_data_loss(tmp_path):
    db_path = tmp_path / "populated-v1.db"
    knowledge_object = _knowledge_object()
    candidate_id = candidate_identity(CASE_ID, SOURCE_ID)
    content_hash = candidate_content_hash(knowledge_object)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        v001_candidate_repository.apply(connection)
        connection.execute(
            "INSERT INTO hardware_asset_schema_version "
            "(singleton,schema_version,schema_name,updated_at) VALUES(1,1,?,?)",
            (v001_candidate_repository.SCHEMA_NAME, "legacy-v1"),
        )
        connection.execute(
            "INSERT INTO hardware_asset_schema_migration "
            "(migration_id,source_version,target_version,applied_at) VALUES(?,?,?,?)",
            (
                v001_candidate_repository.MIGRATION_ID,
                v001_candidate_repository.SOURCE_VERSION,
                v001_candidate_repository.TARGET_VERSION,
                "legacy-v1",
            ),
        )
        connection.execute(
            """
            INSERT INTO hardware_candidate_asset(
                candidate_id,business_case_id,source_id,source_ref,candidate_hash,
                knowledge_object_json,generation_run_id,pipeline_version,
                agent_config_version,knowledge_schema_version,validator_version,
                asset_status,production_review_status,promotion_status,row_version,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE','RESOLVED','NOT_STARTED',2,?,?)
            """,
            (
                candidate_id,
                CASE_ID,
                SOURCE_ID,
                SOURCE_REF,
                content_hash,
                json.dumps(knowledge_object, ensure_ascii=False, sort_keys=True),
                "old-run",
                VERSIONS["pipeline_version"],
                VERSIONS["agent_config_version"],
                VERSIONS["knowledge_schema_version"],
                VERSIONS["validator_version"],
                "old-created",
                "old-updated",
            ),
        )
        connection.execute(
            """
            INSERT INTO hardware_candidate_review(
                review_id,candidate_id,before_candidate_hash,after_candidate_hash,
                reviewer,reason,review_record_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                "old-review",
                candidate_id,
                "b" * 64,
                content_hash,
                "reviewer-1",
                "legacy review",
                '{"legacy":true}',
                "old-reviewed-at",
            ),
        )

    repository = CandidateAssetRepository(db_path)
    assert repository.initialize()["schema_version"] == ASSET_SCHEMA_VERSION == 2
    assert _query(
        db_path,
        "SELECT before_candidate_hash,after_candidate_hash,review_record_json "
        "FROM hardware_candidate_review WHERE review_id='old-review'",
    ) == [("b" * 64, content_hash, '{"legacy":true}')]
    assert _query(
        db_path,
        "SELECT candidate_hash,production_review_status,row_version "
        "FROM hardware_candidate_asset WHERE candidate_id=?",
        (candidate_id,),
    ) == [(content_hash, "RESOLVED", 2)]


def test_candidate_operations_do_not_implicitly_migrate_v0_schema(tmp_path):
    db_path = tmp_path / "hardware_asset.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE hardware_asset_schema_version (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                schema_version INTEGER NOT NULL,
                schema_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO hardware_asset_schema_version VALUES(1,0,'HARDWARE_ASSET_SCHEMA_V0','old')"
        )

    repository = CandidateAssetRepository(db_path)
    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.get_candidate("HCAND-not-initialized")
    assert error.value.code == "ASSET_SCHEMA_MIGRATION_REQUIRED"
    assert _query(
        db_path,
        "SELECT schema_version FROM hardware_asset_schema_version WHERE singleton=1",
    ) == [(0,)]


def test_missing_database_is_not_silently_created_by_reads(tmp_path):
    db_path = tmp_path / "missing" / "hardware_asset.db"
    repository = CandidateAssetRepository(db_path)

    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.get_candidate("missing")
    assert error.value.code == "CANDIDATE_ASSET_STORE_UNAVAILABLE"
    assert not db_path.exists()


def test_newer_asset_schema_fails_closed(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE hardware_asset_schema_version SET schema_version=? WHERE singleton=1",
            (ASSET_SCHEMA_VERSION + 1,),
        )

    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.initialize()
    assert error.value.code == "ASSET_SCHEMA_TOO_NEW"

    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.get_candidate("HCAND-not-read")
    assert error.value.code == "ASSET_SCHEMA_TOO_NEW"

    with pytest.raises(CandidateAssetRepositoryError) as error:
        _create(repository)
    assert error.value.code == "ASSET_SCHEMA_TOO_NEW"


@pytest.mark.parametrize(
    ("table", "column", "value", "error_code"),
    [
        (
            "hardware_asset_schema_version",
            "schema_name",
            "UNEXPECTED_SCHEMA",
            "ASSET_SCHEMA_VERSION_MISMATCH",
        ),
        (
            "hardware_asset_schema_migration",
            "source_version",
            9,
            "ASSET_SCHEMA_MIGRATION_RECORD_MISSING",
        ),
    ],
)
def test_asset_schema_metadata_mismatch_fails_closed(
    tmp_path, table, column, value, error_code
):
    repository, db_path = _ready_repo(tmp_path)
    with sqlite3.connect(db_path) as connection:
        if table == "hardware_asset_schema_version":
            connection.execute(
                f"UPDATE {table} SET {column}=? WHERE singleton=1", (value,)
            )
        else:
            connection.execute(
                f"UPDATE {table} SET {column}=?", (value,)
            )

    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.initialize()
    assert error.value.code == error_code


def test_candidate_identity_and_canonical_hash_are_stable(tmp_path):
    repository, _ = _ready_repo(tmp_path)
    first_object = _knowledge_object()
    reordered_object = {
        "review": first_object["review"],
        "evidence": first_object["evidence"],
        "source_fact": first_object["source_fact"],
        "identity": first_object["identity"],
        "engineering_context": first_object["engineering_context"],
        "conflicts": first_object["conflicts"],
        "contract_version": first_object["contract_version"],
    }

    assert candidate_identity(CASE_ID, SOURCE_ID) == (
        "HCAND-" + hashlib.sha256(f"{CASE_ID}\n{SOURCE_ID}".encode()).hexdigest()
    )
    assert candidate_content_hash(first_object) == candidate_content_hash(reordered_object)
    assert _create(repository, knowledge_object=first_object)["candidate_id"] == candidate_identity(
        CASE_ID, SOURCE_ID
    )


def test_candidate_commit_is_idempotent_and_stores_only_evidence_references(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    first = _create(repository)
    second = _create(repository, run_id="run-retry")

    assert first["commit_result"] == "CREATED"
    assert second["commit_result"] == "IDEMPOTENT_REUSE"
    assert second["candidate_id"] == first["candidate_id"]
    assert second["candidate_hash"] == first["candidate_hash"]
    assert second["row_version"] == first["row_version"] == 1
    assert len(second["evidence_refs"]) == 1
    assert second["evidence_refs"][0]["evidence_id"] == deterministic_evidence_id(
        CASE_ID, SOURCE_ID, "B0001"
    )
    assert second["evidence_refs"][0]["locator"] == {
        "paragraph": 1,
        "section_path": ["3", "4"],
    }
    assert _query(
        db_path,
        "SELECT event_type FROM hardware_candidate_event ORDER BY created_at,event_id",
    ) == [("CREATED",)]
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_asset") == [(1,)]
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_evidence_ref") == [(1,)]


def test_unreviewed_regeneration_keeps_asset_identity_and_advances_row_version(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    first = _create(repository)
    second = _create(repository, knowledge_object=_knowledge_object(subject="UART reset"))

    assert second["commit_result"] == "REGENERATED"
    assert second["candidate_id"] == first["candidate_id"]
    assert second["candidate_hash"] != first["candidate_hash"]
    assert second["row_version"] == 2
    assert _query(
        db_path,
        "SELECT event_type,old_candidate_hash,new_candidate_hash "
        "FROM hardware_candidate_event ORDER BY rowid",
    ) == [
        ("CREATED", None, first["candidate_hash"]),
        ("REGENERATED", first["candidate_hash"], second["candidate_hash"]),
    ]


def test_invalidated_candidate_only_reactivates_without_content_change(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    candidate = _create(repository)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE hardware_candidate_asset SET asset_status='INVALIDATED' "
            "WHERE candidate_id=?",
            (candidate["candidate_id"],),
        )

    with pytest.raises(CandidateAssetRepositoryError) as error:
        _create(repository, knowledge_object=_knowledge_object(subject="new content"))
    assert error.value.code == "CANDIDATE_ASSET_INVALIDATED"
    assert repository.get_candidate(candidate["candidate_id"])["candidate_hash"] == candidate[
        "candidate_hash"
    ]

    reactivated = _create(repository)
    assert reactivated["commit_result"] == "REACTIVATED"
    assert reactivated["asset_status"] == "ACTIVE"
    assert reactivated["candidate_hash"] == candidate["candidate_hash"]
    assert reactivated["row_version"] == 2


def test_review_is_atomic_and_locks_candidate_content(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    first = _create(repository, knowledge_object=_knowledge_object(conflict=True))
    reviewed = copy.deepcopy(first["knowledge_object"])
    reviewed["engineering_context"]["primary_subject"]["value"] = "UART reset"
    reviewed["conflicts"][0].update(
        {"status": "RESOLVED", "resolution_status": "CONFIRMED"}
    )
    reviewed["review"].update(
        {
            "reviewer": "reviewer-1",
            "reviewed_at": "2026-10-04T01:00:00Z",
            "field_decisions": [
                {"conflict_id": "C-1", "decision_source": "SOURCE_RAW_TITLE"}
            ],
        }
    )

    result = repository.apply_production_review(
        first["candidate_id"],
        reviewed_knowledge_object=reviewed,
        expected_row_version=1,
        reviewer="reviewer-1",
        reason="Confirmed against the source evidence",
    )

    assert result["production_review_status"] == "RESOLVED"
    assert result["row_version"] == 2
    assert result["candidate_hash"] == candidate_content_hash(reviewed)
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_review") == [(1,)]
    assert _query(
        db_path,
        "SELECT event_type,old_candidate_hash,new_candidate_hash "
        "FROM hardware_candidate_event WHERE event_type='PRODUCTION_REVIEWED'",
    ) == [("PRODUCTION_REVIEWED", first["candidate_hash"], result["candidate_hash"])]

    with pytest.raises(CandidateAssetRepositoryError) as error:
        _create(repository, knowledge_object=_knowledge_object(subject="changed after review"))
    assert error.value.code == "CANDIDATE_LOCKED_BY_REVIEW"


def test_reactivation_preserves_resolved_review_status(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    candidate = _create(repository, knowledge_object=_knowledge_object(conflict=True))
    reviewed = copy.deepcopy(candidate["knowledge_object"])
    reviewed["conflicts"][0].update(
        {"status": "RESOLVED", "resolution_status": "CONFIRMED"}
    )
    reviewed["review"].update(
        {"reviewer": "reviewer-1", "reviewed_at": "2026-10-04T01:00:00Z"}
    )
    reviewed_candidate = repository.apply_production_review(
        candidate["candidate_id"],
        reviewed_knowledge_object=reviewed,
        expected_row_version=1,
        reviewer="reviewer-1",
        reason="Confirmed against source",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE hardware_candidate_asset SET asset_status='INVALIDATED' "
            "WHERE candidate_id=?",
            (candidate["candidate_id"],),
        )

    reactivated = _create(repository, knowledge_object=reviewed)
    assert reactivated["commit_result"] == "REACTIVATED"
    assert reactivated["production_review_status"] == "RESOLVED"
    assert reactivated["candidate_hash"] == reviewed_candidate["candidate_hash"]


def test_review_update_is_compare_and_set_and_only_allows_required_transition(tmp_path):
    repository, _ = _ready_repo(tmp_path)
    candidate = _create(repository)
    updated = repository.update_review_status(
        candidate["candidate_id"],
        expected_status="NOT_REQUIRED",
        new_status="REQUIRED",
        expected_row_version=1,
    )
    assert updated["production_review_status"] == "REQUIRED"
    assert updated["row_version"] == 2

    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.update_review_status(
            candidate["candidate_id"],
            expected_status="NOT_REQUIRED",
            new_status="REQUIRED",
            expected_row_version=1,
        )
    assert error.value.code == "CANDIDATE_CONCURRENT_UPDATE"

    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.update_review_status(
            candidate["candidate_id"],
            expected_status="REQUIRED",
            new_status="RESOLVED",
            expected_row_version=2,
        )
    assert error.value.code == "CANDIDATE_REVIEW_TRANSITION_INVALID"


def test_concurrent_review_cas_allows_exactly_one_writer(tmp_path):
    repository, _ = _ready_repo(tmp_path)
    candidate = _create(repository)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def update() -> None:
        barrier.wait()
        try:
            repository.update_review_status(
                candidate["candidate_id"],
                expected_status="NOT_REQUIRED",
                new_status="REQUIRED",
                expected_row_version=1,
            )
            value = "PASS"
        except CandidateAssetRepositoryError as error:
            value = error.code
        with outcome_lock:
            outcomes.append(value)

    threads = [threading.Thread(target=update) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["CANDIDATE_CONCURRENT_UPDATE", "PASS"]


def test_promotion_cas_creates_local_ledger_and_locks_content(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    candidate = _create(repository)
    started = repository.update_promotion_status(
        candidate["candidate_id"],
        expected_status="NOT_STARTED",
        new_status="PRECHECK_PASS",
        expected_row_version=1,
        actor="operator-1",
        reason="Promotion precheck passed",
    )
    assert started["promotion_status"] == "PRECHECK_PASS"
    assert started["row_version"] == 2
    assert started["promotion_record"]["asset_candidate_id"] == candidate["candidate_id"]
    assert _query(
        db_path,
        "SELECT event_type FROM hardware_candidate_event WHERE event_type='PROMOTION_STARTED'",
    ) == [("PROMOTION_STARTED",)]

    continued = repository.update_promotion_status(
        candidate["candidate_id"],
        expected_status="PRECHECK_PASS",
        new_status="CANDIDATE_INTAKED",
        expected_row_version=2,
        actor="operator-1",
        reason="Candidate intake recorded",
    )
    assert continued["promotion_status"] == "CANDIDATE_INTAKED"
    assert continued["row_version"] == 3
    assert continued["promotion_record"]["promotion_status"] == "CANDIDATE_INTAKED"

    with pytest.raises(CandidateAssetRepositoryError) as error:
        _create(repository, knowledge_object=_knowledge_object(subject="changed after promotion"))
    assert error.value.code == "CANDIDATE_LOCKED_BY_PROMOTION"


def test_candidate_evidence_and_event_commit_roll_back_as_one_transaction(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_created_event BEFORE INSERT ON hardware_candidate_event
            WHEN NEW.event_type='CREATED'
            BEGIN SELECT RAISE(ABORT,'injected event failure'); END
            """
        )

    with pytest.raises(CandidateAssetRepositoryError) as error:
        _create(repository)
    assert error.value.code == "CANDIDATE_DATA_INTEGRITY_ERROR"
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_asset") == [(0,)]
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_evidence_ref") == [(0,)]
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_event") == [(0,)]


def test_review_content_record_and_audit_event_roll_back_together(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    candidate = _create(repository, knowledge_object=_knowledge_object(conflict=True))
    reviewed = copy.deepcopy(candidate["knowledge_object"])
    reviewed["conflicts"][0].update(
        {"status": "RESOLVED", "resolution_status": "CONFIRMED"}
    )
    reviewed["review"].update(
        {"reviewer": "reviewer-1", "reviewed_at": "2026-10-04T01:00:00Z"}
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_review_record BEFORE INSERT ON hardware_candidate_review
            BEGIN SELECT RAISE(ABORT,'injected review failure'); END
            """
        )

    with pytest.raises(CandidateAssetRepositoryError) as error:
        repository.apply_production_review(
            candidate["candidate_id"],
            reviewed_knowledge_object=reviewed,
            expected_row_version=1,
            reviewer="reviewer-1",
            reason="Test atomic rollback",
        )
    assert error.value.code == "CANDIDATE_DATA_INTEGRITY_ERROR"
    current = repository.get_candidate(candidate["candidate_id"])
    assert current["candidate_hash"] == candidate["candidate_hash"]
    assert current["production_review_status"] == "REQUIRED"
    assert current["row_version"] == 1
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_review") == [(0,)]
    assert _query(
        db_path,
        "SELECT count(*) FROM hardware_candidate_event WHERE event_type='PRODUCTION_REVIEWED'",
    ) == [(0,)]


def test_audit_and_candidate_rows_are_immutable_or_not_hard_deleted(tmp_path):
    repository, db_path = _ready_repo(tmp_path)
    candidate = _create(repository)
    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            connection.execute(
                "UPDATE hardware_candidate_event SET reason='changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="DELETE_FORBIDDEN"):
            connection.execute(
                "DELETE FROM hardware_candidate_asset WHERE candidate_id=?",
                (candidate["candidate_id"],),
            )


@pytest.mark.parametrize(
    ("knowledge_object", "source_ref"),
    [
        ({"contract_version": "other/v1"}, SOURCE_REF),
        (_knowledge_object(), ""),
    ],
)
def test_invalid_candidate_input_is_rejected_without_partial_asset(
    tmp_path, knowledge_object, source_ref
):
    repository, db_path = _ready_repo(tmp_path)
    with pytest.raises(CandidateAssetRepositoryError) as error:
        _create(repository, knowledge_object=knowledge_object, source_ref=source_ref)
    assert error.value.code == "CANDIDATE_INPUT_INVALID"
    assert _query(db_path, "SELECT count(*) FROM hardware_candidate_asset") == [(0,)]


def test_case_and_source_lookups_return_only_matching_assets(tmp_path):
    repository, _ = _ready_repo(tmp_path)
    candidate = _create(repository)

    assert [item["candidate_id"] for item in repository.find_by_business_case_id(CASE_ID)] == [
        candidate["candidate_id"]
    ]
    assert [item["candidate_id"] for item in repository.find_by_source_id(SOURCE_ID)] == [
        candidate["candidate_id"]
    ]
    assert [
        item["candidate_id"]
        for item in repository.get_active_by_business_case_id(CASE_ID)
    ] == [candidate["candidate_id"]]
