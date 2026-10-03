from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from services.hardware_asset_legacy_migration import (
    LegacyAssetMigrationError,
    LegacyAssetMigrationRunner,
    LegacyCandidatePreflightScanner,
    LegacyMigrationPaths,
    select_legacy_canonical_groups,
)
from services.hardware_asset_repository import candidate_content_hash
from services.hardware_case_r1_workbench import HardwareR1WorkbenchStore
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_r1_knowledge_promotion import HardwareR1KnowledgePromotionStore


CASE_ID = "A0162"
SOURCE_BYTES = b"legacy source bytes used for source-binding verification"


class SimulatedCrash(BaseException):
    pass


def _knowledge_object(source_id: str, *, subject: str, reviewed: bool) -> dict:
    conflicts = []
    decisions = []
    reviewer = None
    reviewed_at = None
    if reviewed:
        conflicts = [
            {
                "conflict_id": "C-LEGACY-1",
                "status": "RESOLVED",
                "resolution_status": "CONFIRMED",
            }
        ]
        decisions = [
            {
                "conflict_id": "C-LEGACY-1",
                "decision_source": "SOURCE_EVIDENCE",
                "selected_value": subject,
                "reviewer": "product-reviewer",
                "reviewed_at": "2026-10-03T10:00:00Z",
            }
        ]
        reviewer = "product-reviewer"
        reviewed_at = "2026-10-03T10:00:00Z"
    return {
        "contract_version": "hardware-case-knowledge-object/v1",
        "identity": {"business_case_id": CASE_ID, "raw_title": "Legacy case"},
        "source_fact": {"business_case_id": CASE_ID, "source_id": source_id},
        "engineering_context": {"primary_subject": {"value": subject}},
        "evidence": [
            {
                "block_id": "B0001",
                "block_type": "PARAGRAPH",
                "source_locator": {"paragraph": 1, "section_path": ["2"]},
                "text": subject,
            }
        ],
        "conflicts": conflicts,
        "review": {
            "object_status": "CANDIDATE",
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "field_decisions": decisions,
        },
    }


def _result(source_id: str, *, subject: str, reviewed: bool = False) -> dict:
    return {
        "status": "PASS",
        "pipeline_version": "hardware-r1-agent-pipeline/v1.5",
        "run_id": "legacy-run-" + subject.replace(" ", "-"),
        "runtime": {"agent_config_version": "hardware-case-reuse/v3"},
        "knowledge_object": _knowledge_object(
            source_id, subject=subject, reviewed=reviewed
        ),
        "evidence_validation": {
            "status": "PASS",
            "fabricated_fact_count": 0,
            "fabricated_block_id_count": 0,
            "validator_version": "hardware-r1-validator/v1",
        },
    }


def _setup(tmp_path: Path):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True)
    hardware_db = legacy / "hardware_case_mvp.db"
    workbench_db = legacy / "hardware_case_mvp_r1_workbench.db"
    source_root = legacy / "hardware_case_mvp_sources"
    source_store = HardwareCaseSourceStore(hardware_db, source_root)
    binding = source_store.register_active_bytes(
        CASE_ID, "source.docx", SOURCE_BYTES
    )
    workbench = HardwareR1WorkbenchStore(workbench_db)
    promotion_store = HardwareR1KnowledgePromotionStore(workbench_db)

    data_root = tmp_path / "durable-data"
    for relative in ("db", "manifest", "backups"):
        (data_root / relative).mkdir(parents=True, exist_ok=True)
    preview_db = legacy / "hardware_case_mvp_r1_preview.db"
    preview_db.write_bytes(b"preview database must remain unopened")
    paths = LegacyMigrationPaths(
        hardware_db=hardware_db,
        workbench_db=workbench_db,
        source_root=source_root,
        target_data_root=data_root,
        preview_db=preview_db,
    )
    return paths, binding, workbench, promotion_store, preview_db


def _add_item(
    workbench: HardwareR1WorkbenchStore,
    source_id: str,
    *,
    subject: str,
    reviewed: bool = False,
    updated_at: str,
) -> tuple[str, str]:
    batch_id = workbench.create_batch()
    item_id = workbench.add_item(
        batch_id,
        source_file="source.docx",
        business_case_id=CASE_ID,
        source_id=source_id,
        orchestration_status="CANDIDATE_READY",
        result=_result(source_id, subject=subject, reviewed=reviewed),
    )
    with sqlite3.connect(workbench.db_path) as connection:
        connection.execute(
            "UPDATE hardware_r1_batch_item SET updated_at=? WHERE item_id=?",
            (updated_at, item_id),
        )
    return batch_id, item_id


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preflight_is_read_only_ignores_preview_and_selects_reviewed_candidate(tmp_path):
    paths, binding, workbench, _, preview_db = _setup(tmp_path)
    _add_item(
        workbench,
        binding["source_id"],
        subject="machine latest",
        updated_at="2026-10-04T10:00:00Z",
    )
    _, reviewed_item = _add_item(
        workbench,
        binding["source_id"],
        subject="human reviewed",
        reviewed=True,
        updated_at="2026-10-03T10:00:00Z",
    )
    hardware_before = _sha(paths.hardware_db)
    workbench_before = _sha(paths.workbench_db)
    preview_before = preview_db.read_bytes()

    scan = LegacyCandidatePreflightScanner(paths).scan()
    group = select_legacy_canonical_groups(scan)[0]

    assert len(scan.candidates) == 2
    assert group.canonical.item_id == reviewed_item
    assert group.canonical.review_status == "RESOLVED"
    assert _sha(paths.hardware_db) == hardware_before
    assert _sha(paths.workbench_db) == workbench_before
    assert preview_db.read_bytes() == preview_before
    assert not paths.asset_db.exists()


def test_preflight_skips_queued_null_and_failed_evidence_rows(tmp_path):
    paths, binding, workbench, _, _ = _setup(tmp_path)
    batch_id = workbench.create_batch()
    queued_item = workbench.add_item(
        batch_id,
        source_file="not-imported.docx",
        business_case_id=CASE_ID,
        source_id=binding["source_id"],
        orchestration_status="QUEUED",
        result=_result(binding["source_id"], subject="queued"),
    )
    null_item = workbench.add_item(
        batch_id,
        source_file="not-imported.docx",
        business_case_id=CASE_ID,
        source_id=binding["source_id"],
        orchestration_status="RUNTIME_BLOCKED",
        result=None,
    )
    failed_result = _result(binding["source_id"], subject="gate failed")
    failed_result["evidence_validation"]["status"] = "FAILED"
    failed_item = workbench.add_item(
        batch_id,
        source_file="not-imported.docx",
        business_case_id=CASE_ID,
        source_id=binding["source_id"],
        orchestration_status="RUNTIME_BLOCKED",
        result=failed_result,
    )
    with sqlite3.connect(paths.workbench_db) as connection:
        connection.execute(
            "UPDATE hardware_r1_batch_item SET result_json='null' WHERE item_id=?",
            (null_item,),
        )

    scan = LegacyCandidatePreflightScanner(paths).scan()

    assert scan.candidates == ()
    assert scan.skipped_item_count == 3
    assert queued_item and null_item and failed_item


def test_migration_preserves_review_history_provenance_and_is_idempotent(tmp_path):
    paths, binding, workbench, _, preview_db = _setup(tmp_path)
    _add_item(
        workbench,
        binding["source_id"],
        subject="machine latest",
        updated_at="2026-10-04T10:00:00Z",
    )
    _, reviewed_item = _add_item(
        workbench,
        binding["source_id"],
        subject="human reviewed",
        reviewed=True,
        updated_at="2026-10-03T10:00:00Z",
    )
    hardware_before = _sha(paths.hardware_db)
    workbench_before = _sha(paths.workbench_db)
    preview_before = preview_db.read_bytes()

    first = LegacyAssetMigrationRunner(paths).run()
    second = LegacyAssetMigrationRunner(paths).run()

    assert first["status"] == second["status"] == "PASS"
    assert first["migration_id"] == second["migration_id"]
    assert paths.asset_db.stat().st_size > 0
    with sqlite3.connect(paths.asset_db) as connection:
        candidate = connection.execute(
            "SELECT candidate_hash,knowledge_object_json,production_review_status "
            "FROM hardware_candidate_asset"
        ).fetchone()
        assert candidate[2] == "RESOLVED"
        imported = json.loads(candidate[1])
        assert imported["engineering_context"]["primary_subject"]["value"] == "human reviewed"
        assert candidate[0] == candidate_content_hash(imported)
        review = connection.execute(
            "SELECT before_candidate_hash,after_candidate_hash,review_record_json "
            "FROM hardware_candidate_review"
        ).fetchone()
        assert review[0] is None
        review_record = json.loads(review[2])
        assert review_record["legacy_before_hash_unavailable"] is True
        assert (
            review_record["legacy_before_hash_status"]
            == "LEGACY_BEFORE_HASH_UNAVAILABLE"
        )
        assert review_record["legacy_item_id"] == reviewed_item
        assert connection.execute(
            "SELECT COUNT(*) FROM hardware_candidate_legacy_origin"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT selected_as_canonical FROM hardware_candidate_legacy_origin "
            "WHERE legacy_item_id=?",
            (reviewed_item,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM hardware_asset_migration WHERE migration_id=?",
            (first["migration_id"],),
        ).fetchone()[0] == "COMPLETED"

    assert _sha(paths.hardware_db) == hardware_before
    assert _sha(paths.workbench_db) == workbench_before
    assert preview_db.read_bytes() == preview_before
    assert len(list((paths.backup_root / "legacy-hardware").glob("*.sqlite3"))) == 1
    assert len(list((paths.backup_root / "legacy-workbench").glob("*.sqlite3"))) == 1


def test_promotion_hash_and_formal_reference_are_migrated_without_republishing(tmp_path):
    paths, binding, workbench, promotion_store, _ = _setup(tmp_path)
    batch_id, item_id = _add_item(
        workbench,
        binding["source_id"],
        subject="published candidate",
        updated_at="2026-10-04T10:00:00Z",
    )
    result = _result(binding["source_id"], subject="published candidate")
    golden_hash = candidate_content_hash(result["knowledge_object"])
    promotion_store.ensure(
        item_id=item_id,
        batch_id=batch_id,
        business_case_id=CASE_ID,
        source_id=binding["source_id"],
        golden_hash=golden_hash,
    )
    promotion_store.update(
        item_id,
        status="VERIFIED",
        last_action="QUERY_BACK_VERIFIED",
        candidate_id="UK-CANDIDATE-LEGACY-7",
        review_status="CONFIRMED",
        knowledge_id="UK-KNOWLEDGE-7",
        public_ref="hardware://A0162/7",
    )
    source_store = HardwareCaseSourceStore(paths.hardware_db, paths.source_root)
    source_store.add_formal_knowledge_reference(
        CASE_ID, "UK-KNOWLEDGE-7", source_id=binding["source_id"]
    )

    result = LegacyAssetMigrationRunner(paths).run()

    assert result["status"] == "PASS"
    with sqlite3.connect(paths.asset_db) as connection:
        asset = connection.execute(
            "SELECT promotion_status,row_version FROM hardware_candidate_asset"
        ).fetchone()
        promotion = connection.execute(
            "SELECT knowledge_candidate_id,knowledge_id,public_ref,promotion_status,"
            "origin_batch_id,origin_item_id FROM hardware_asset_promotion"
        ).fetchone()
        assert asset == ("VERIFIED", 2)
        assert promotion == (
            "UK-CANDIDATE-LEGACY-7",
            "UK-KNOWLEDGE-7",
            "hardware://A0162/7",
            "VERIFIED",
            batch_id,
            item_id,
        )


@pytest.mark.parametrize(
    ("promotion_status", "promotion_hash", "knowledge_id", "expected_code"),
    [
        ("PRECHECK_PASS", "f" * 64, None, "LEGACY_PROMOTION_HASH_MISMATCH"),
        (
            "VERIFIED",
            None,
            "UK-KNOWLEDGE-MISSING-REF",
            "LEGACY_FORMAL_REFERENCE_INCONSISTENT",
        ),
    ],
)
def test_inconsistent_promotion_blocks_migration(
    tmp_path,
    promotion_status,
    promotion_hash,
    knowledge_id,
    expected_code,
):
    paths, binding, workbench, promotion_store, _ = _setup(tmp_path)
    batch_id, item_id = _add_item(
        workbench,
        binding["source_id"],
        subject="candidate with inconsistent promotion",
        updated_at="2026-10-04T10:00:00Z",
    )
    result = _result(
        binding["source_id"], subject="candidate with inconsistent promotion"
    )
    promotion_store.ensure(
        item_id=item_id,
        batch_id=batch_id,
        business_case_id=CASE_ID,
        source_id=binding["source_id"],
        golden_hash=(
            promotion_hash
            or candidate_content_hash(result["knowledge_object"])
        ),
    )
    promotion_store.update(
        item_id,
        status=promotion_status,
        last_action="LEGACY_TEST",
        candidate_id="UK-CANDIDATE-LEGACY-9",
        knowledge_id=knowledge_id,
    )

    with pytest.raises(LegacyAssetMigrationError) as error:
        LegacyAssetMigrationRunner(paths).run()

    assert error.value.code == expected_code
    assert not paths.asset_db.exists()


def test_different_legacy_promotion_hashes_block_canonical_selection(tmp_path):
    paths, binding, workbench, promotion_store, _ = _setup(tmp_path)
    for index, subject in enumerate(("promotion candidate one", "promotion candidate two")):
        batch_id, item_id = _add_item(
            workbench,
            binding["source_id"],
            subject=subject,
            updated_at=f"2026-10-0{3 + index}T10:00:00Z",
        )
        candidate = _result(binding["source_id"], subject=subject)["knowledge_object"]
        promotion_store.ensure(
            item_id=item_id,
            batch_id=batch_id,
            business_case_id=CASE_ID,
            source_id=binding["source_id"],
            golden_hash=candidate_content_hash(candidate),
        )
        promotion_store.update(
            item_id,
            status="PRECHECK_PASS",
            last_action="LEGACY_TEST",
        )
    with pytest.raises(LegacyAssetMigrationError) as error:
        LegacyAssetMigrationRunner(paths).run()

    assert error.value.code == "LEGACY_PROMOTION_CONFLICT"
    assert not paths.asset_db.exists()


def test_reviewed_candidate_hash_conflict_fails_without_touching_legacy_or_target(tmp_path):
    paths, binding, workbench, _, _ = _setup(tmp_path)
    _add_item(
        workbench,
        binding["source_id"],
        subject="reviewed one",
        reviewed=True,
        updated_at="2026-10-03T10:00:00Z",
    )
    _add_item(
        workbench,
        binding["source_id"],
        subject="reviewed two",
        reviewed=True,
        updated_at="2026-10-04T10:00:00Z",
    )
    hardware_before = _sha(paths.hardware_db)
    workbench_before = _sha(paths.workbench_db)

    with pytest.raises(LegacyAssetMigrationError) as error:
        LegacyAssetMigrationRunner(paths).run()

    assert error.value.code == "LEGACY_REVIEWED_CANDIDATE_CONFLICT"
    assert not paths.asset_db.exists()
    assert _sha(paths.hardware_db) == hardware_before
    assert _sha(paths.workbench_db) == workbench_before


def test_unavailable_or_changed_source_fails_closed_without_mutating_registry(tmp_path):
    paths, binding, workbench, _, _ = _setup(tmp_path)
    _add_item(
        workbench,
        binding["source_id"],
        subject="candidate",
        updated_at="2026-10-04T10:00:00Z",
    )
    source_path = next(paths.source_root.rglob("source.docx"))
    source_path.write_bytes(b"source changed after registry binding")
    hardware_before = _sha(paths.hardware_db)
    workbench_before = _sha(paths.workbench_db)

    with pytest.raises(LegacyAssetMigrationError) as error:
        LegacyAssetMigrationRunner(paths).run()

    assert error.value.code == "LEGACY_CANDIDATE_SOURCE_UNAVAILABLE"
    assert not paths.asset_db.exists()
    assert _sha(paths.hardware_db) == hardware_before
    assert _sha(paths.workbench_db) == workbench_before


def test_activation_crash_recovers_without_rebuilding_candidate_assets(tmp_path):
    paths, binding, workbench, _, _ = _setup(tmp_path)
    _add_item(
        workbench,
        binding["source_id"],
        subject="recover me",
        updated_at="2026-10-04T10:00:00Z",
    )

    def crash(checkpoint: str) -> None:
        if checkpoint == "AFTER_ACTIVATION_BEFORE_COMPLETION":
            raise SimulatedCrash(checkpoint)

    with pytest.raises(SimulatedCrash):
        LegacyAssetMigrationRunner(paths, fault_injector=crash).run()

    assert paths.asset_db.is_file()
    assert paths.asset_db.stat().st_size > 0
    with sqlite3.connect(paths.asset_db) as connection:
        assert connection.execute(
            "SELECT state FROM hardware_asset_migration"
        ).fetchone()[0] == "ACTIVATING"

    recovered = LegacyAssetMigrationRunner(paths).run()

    assert recovered["status"] == "PASS"
    with sqlite3.connect(paths.asset_db) as connection:
        assert connection.execute(
            "SELECT state FROM hardware_asset_migration"
        ).fetchone()[0] == "COMPLETED"
        assert connection.execute(
            "SELECT COUNT(*) FROM hardware_candidate_asset"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM hardware_candidate_evidence_ref"
        ).fetchone()[0] == 1


def test_verified_stage_resumes_activation_and_data_change_fails_closed(tmp_path):
    paths, binding, workbench, _, _ = _setup(tmp_path)
    _add_item(
        workbench,
        binding["source_id"],
        subject="verified stage",
        updated_at="2026-10-04T10:00:00Z",
    )

    def crash_after_verify(checkpoint: str) -> None:
        if checkpoint == "VERIFIED":
            raise SimulatedCrash(checkpoint)

    with pytest.raises(SimulatedCrash):
        LegacyAssetMigrationRunner(paths, fault_injector=crash_after_verify).run()

    assert not paths.asset_db.exists()
    resumed = LegacyAssetMigrationRunner(paths).run()
    assert resumed["status"] == "PASS"
    with sqlite3.connect(paths.asset_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM hardware_candidate_asset"
        ).fetchone()[0] == 1

    other_paths, other_binding, other_workbench, _, _ = _setup(
        tmp_path / "changed"
    )
    _add_item(
        other_workbench,
        other_binding["source_id"],
        subject="source will change",
        updated_at="2026-10-04T10:00:00Z",
    )
    with pytest.raises(SimulatedCrash):
        LegacyAssetMigrationRunner(
            other_paths, fault_injector=crash_after_verify
        ).run()
    source_path = next(other_paths.source_root.rglob("source.docx"))
    source_path.write_bytes(b"changed while a verified stage was pending")

    with pytest.raises(LegacyAssetMigrationError) as error:
        LegacyAssetMigrationRunner(other_paths).run()

    assert error.value.code == "LEGACY_DATA_CHANGED_DURING_MIGRATION"
    assert not other_paths.asset_db.exists()
