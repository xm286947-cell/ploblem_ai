from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from services.hardware_asset_operation_journal import HardwareAssetOperationJournal
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_case_source_store import (
    HardwareCaseSourceError,
    HardwareCaseSourceStore,
)
from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_startup_coordinator import HardwareStartupCoordinator


class SimulatedPowerLoss(BaseException):
    pass


def _ready_store(tmp_path: Path):
    app_root = tmp_path / "application"
    app_root.mkdir(parents=True)
    data_root = tmp_path / "persistent-data"
    resolver = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=tmp_path / "user-config" / "bootstrap.json",
        legacy_roots=(),
    )
    startup = HardwareStartupCoordinator(app_root, resolver=resolver).run()
    assert startup["ready"] is True, startup
    hardware_db = data_root / "db" / "hardware_case_mvp.db"
    asset_db = data_root / "db" / "hardware_asset.db"
    repository = CandidateAssetRepository(asset_db)
    store = HardwareCaseSourceStore(
        hardware_db,
        data_root / "sources",
        initialize_schema=False,
        operation_journal=HardwareAssetOperationJournal(asset_db),
        candidate_repository=repository,
    )
    return data_root, store, repository, asset_db


def _candidate(repository: CandidateAssetRepository, source: dict) -> dict:
    case_id = source["business_case_id"]
    source_id = source["source_id"]
    return repository.create_or_commit_candidate(
        business_case_id=case_id,
        source_id=source_id,
        source_ref=source["source_ref"],
        generation_run_id="run-source-crash-safety",
        pipeline_version="hardware-r1-agent-pipeline/v1.5",
        agent_config_version="hardware-case-reuse/v3",
        knowledge_schema_version="hardware-case-knowledge-object/v1",
        validator_version="hardware-r1-validator/v1",
        knowledge_object={
            "contract_version": "hardware-case-knowledge-object/v1",
            "identity": {"business_case_id": case_id, "raw_title": "Crash safety"},
            "source_fact": {"business_case_id": case_id, "source_id": source_id},
            "engineering_context": {"primary_subject": {"value": "supported fact"}},
            "evidence": [
                {
                    "block_id": "B0001",
                    "source_locator": {"paragraph": 1},
                    "text": "supported fact",
                }
            ],
            "conflicts": [],
            "review": {"object_status": "CANDIDATE", "field_decisions": []},
        },
    )


def _assert_error(code: str, operation):
    with pytest.raises(HardwareCaseSourceError) as error:
        operation()
    assert error.value.code == code


def test_upload_recovery_after_atomic_file_move_commits_registry_and_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _root, store, _repository, _asset_db = _ready_store(tmp_path)
    original_connect = store._connect
    calls = 0

    def crash_after_move():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise SimulatedPowerLoss()
        return original_connect()

    monkeypatch.setattr(store, "_connect", crash_after_move)
    with pytest.raises(SimulatedPowerLoss):
        store.register_active_bytes("A0162", "A0162.docx", b"durable upload")

    operation = store.operation_journal.list_nonterminal(operation_type="SOURCE_UPLOAD")[0]
    fingerprint = operation["request_fingerprint"]
    final_path = store._journal_path(fingerprint["relative_path"])
    assert final_path.read_bytes() == b"durable upload"
    _assert_error(
        "SOURCE_NOT_REGISTERED",
        lambda: store.get_active_source("A0162"),
    )

    monkeypatch.setattr(store, "_connect", original_connect)
    recovered = store.recover_source_operations()

    assert recovered == [{"operation_id": operation["operation_id"], "operation_state": "COMPLETED"}]
    active = store.get_active_source("A0162")
    assert active["source_id"] == hashlib.sha256(b"durable upload").hexdigest()
    assert store.resolve_path(active["source_ref"]).read_bytes() == b"durable upload"
    assert store.operation_journal.get(operation["operation_id"])["operation_state"] == "COMPLETED"
    assert not (store.source_root / ".incoming" / operation["operation_id"]).exists()


def test_upload_recovery_is_idempotent_after_hardware_db_commit(tmp_path: Path, monkeypatch):
    _root, store, _repository, _asset_db = _ready_store(tmp_path)
    original_transition = store.operation_journal.transition

    def crash_after_source_commit(operation_id, state, **kwargs):
        if state == "LOCAL_COMMITTED":
            raise SimulatedPowerLoss()
        return original_transition(operation_id, state, **kwargs)

    monkeypatch.setattr(store.operation_journal, "transition", crash_after_source_commit)
    with pytest.raises(SimulatedPowerLoss):
        store.register_active_bytes("A0152", "A0152.docx", b"committed upload")

    operation = store.operation_journal.list_nonterminal(operation_type="SOURCE_UPLOAD")[0]
    assert store.get_active_source("A0152")["source_id"] == hashlib.sha256(
        b"committed upload"
    ).hexdigest()

    monkeypatch.setattr(store.operation_journal, "transition", original_transition)
    store.recover_source_operations()
    assert store.operation_journal.get(operation["operation_id"])["operation_state"] == "COMPLETED"
    with sqlite3.connect(_asset_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM hardware_asset_operation_journal WHERE operation_id=?",
            (operation["operation_id"],),
        ).fetchone()[0] == 1


def test_delete_crash_after_quarantine_restores_source_before_logical_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _root, store, repository, _asset_db = _ready_store(tmp_path)
    source = store.register_active_bytes("A0207", "A0207.docx", b"source to retain")
    candidate = _candidate(repository, source)
    original_connect = store._connect
    calls = 0

    def crash_before_delete_transaction():
        nonlocal calls
        calls += 1
        if calls == 6:
            raise SimulatedPowerLoss()
        return original_connect()

    monkeypatch.setattr(store, "_connect", crash_before_delete_transaction)
    with pytest.raises(SimulatedPowerLoss):
        store.delete_active_source("A0207", deleted_by="test")

    operation = store.operation_journal.list_nonterminal(operation_type="SOURCE_DELETE")[0]
    fingerprint = operation["request_fingerprint"]
    quarantine = store._journal_path(fingerprint["quarantine_relative_path"])
    original = store._journal_path(fingerprint["relative_path"])
    assert quarantine.read_bytes() == b"source to retain"
    assert not original.exists()

    monkeypatch.setattr(store, "_connect", original_connect)
    result = store.recover_source_operations()
    assert result[0]["operation_state"] == "CANCELLED"
    assert original.read_bytes() == b"source to retain"
    assert not quarantine.exists()
    assert repository.get_candidate(candidate["candidate_id"])["asset_status"] == "ACTIVE"
    assert store.get_active_source("A0207")["source_id"] == source["source_id"]


def test_delete_crash_after_logical_commit_invalidates_candidate_and_finishes_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _root, store, repository, _asset_db = _ready_store(tmp_path)
    source = store.register_active_bytes("A0156", "A0156.docx", b"source to delete")
    candidate = _candidate(repository, source)
    original_invalidate = repository.invalidate_for_source

    def crash_after_source_db_commit(*args, **kwargs):
        raise SimulatedPowerLoss()

    monkeypatch.setattr(repository, "invalidate_for_source", crash_after_source_db_commit)
    with pytest.raises(SimulatedPowerLoss):
        store.delete_active_source("A0156", deleted_by="test")

    operation = store.operation_journal.list_nonterminal(operation_type="SOURCE_DELETE")[0]
    fingerprint = operation["request_fingerprint"]
    quarantine = store._journal_path(fingerprint["quarantine_relative_path"])
    assert quarantine.read_bytes() == b"source to delete"
    _assert_error(
        "SOURCE_NOT_REGISTERED",
        lambda: store.get_active_source("A0156"),
    )

    monkeypatch.setattr(repository, "invalidate_for_source", original_invalidate)
    result = store.recover_source_operations()
    assert result[0]["operation_state"] == "COMPLETED"
    assert not quarantine.exists()
    invalidated = repository.get_candidate(candidate["candidate_id"])
    assert invalidated["asset_status"] == "INVALIDATED"
    with sqlite3.connect(_asset_db) as connection:
        assert connection.execute(
            "SELECT event_type FROM hardware_candidate_event WHERE candidate_id=? ORDER BY created_at DESC LIMIT 1",
            (candidate["candidate_id"],),
        ).fetchone()[0] == "INVALIDATED"


def test_source_delete_is_locked_once_promotion_has_started(tmp_path: Path):
    _root, store, repository, _asset_db = _ready_store(tmp_path)
    source = store.register_active_bytes("A9001", "A9001.docx", b"promoted source")
    candidate = _candidate(repository, source)
    repository.update_promotion_status(
        candidate["candidate_id"],
        expected_status="NOT_STARTED",
        new_status="PRECHECK_PASS",
        expected_row_version=candidate["row_version"],
        actor="test",
        reason="Lock source after promotion starts",
    )
    path = store.resolve_path(source["source_ref"])

    _assert_error(
        "SOURCE_DELETE_BLOCKED_BY_PROMOTION",
        lambda: store.delete_active_source("A9001"),
    )

    assert path.read_bytes() == b"promoted source"
    assert store.get_active_source("A9001")["source_id"] == source["source_id"]
    assert store.operation_journal.list_nonterminal(operation_type="SOURCE_DELETE") == []


def test_source_delete_is_locked_by_pending_publication_intent(tmp_path: Path):
    _root, store, _repository, _asset_db = _ready_store(tmp_path)
    source = store.register_active_bytes("A9008", "A9008.docx", b"publication locked source")
    store.operation_journal.prepare(
        operation_id="HOP-pending-publication",
        operation_type="PUBLISH",
        business_case_id="A9008",
        candidate_id="HCAND-pending",
        source_id=source["source_id"],
        desired_action="PUBLISH_FORMAL_KNOWLEDGE",
        request_fingerprint={"source_id": source["source_id"]},
    )
    path = store.resolve_path(source["source_ref"])

    _assert_error(
        "SOURCE_RECOVERY_LOCKED",
        lambda: store.delete_active_source("A9008"),
    )

    assert path.read_bytes() == b"publication locked source"
    assert store.get_active_source("A9008")["source_id"] == source["source_id"]
    assert store.operation_journal.list_nonterminal(operation_type="SOURCE_DELETE") == []


def test_source_upload_fails_closed_without_versioned_operation_journal(tmp_path: Path):
    hardware_db = tmp_path / "hardware_case.db"
    store = HardwareCaseSourceStore(hardware_db, tmp_path / "sources")

    _assert_error(
        "SOURCE_OPERATION_JOURNAL_UNAVAILABLE",
        lambda: store.register_active_bytes("A0152", "A0152.docx", b"no journal"),
    )
    assert not any(path.is_file() for path in (tmp_path / "sources").rglob("*"))
