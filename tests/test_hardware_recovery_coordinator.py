from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from services.hardware_asset_operation_journal import HardwareAssetOperationJournal
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_startup_coordinator import HardwareStartupCoordinator


class SimulatedPowerLoss(BaseException):
    pass


def _installed(tmp_path: Path):
    app_root = tmp_path / "application"
    app_root.mkdir(parents=True)
    data_root = tmp_path / "persistent-data"
    asset_db = data_root / "db" / "hardware_asset.db"
    resolver = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=tmp_path / "user-config" / "bootstrap.json",
        legacy_roots=(),
    )
    startup = HardwareStartupCoordinator(app_root, resolver=resolver).run()
    assert startup["ready"] is True, startup
    source_store = HardwareCaseSourceStore(
        data_root / "db" / "hardware_case_mvp.db",
        data_root / "sources",
        initialize_schema=False,
        operation_journal=HardwareAssetOperationJournal(asset_db),
        candidate_repository=CandidateAssetRepository(asset_db),
    )
    return app_root, data_root, resolver, source_store


def _candidate(repository: CandidateAssetRepository, source: dict) -> dict:
    return repository.create_or_commit_candidate(
        business_case_id=source["business_case_id"],
        source_id=source["source_id"],
        source_ref=source["source_ref"],
        generation_run_id="recovery-startup-test",
        pipeline_version="hardware-r1-agent-pipeline/v1.5",
        agent_config_version="hardware-case-reuse/v3",
        knowledge_schema_version="hardware-case-knowledge-object/v1",
        validator_version="hardware-r1-validator/v1",
        knowledge_object={
            "contract_version": "hardware-case-knowledge-object/v1",
            "identity": {"business_case_id": source["business_case_id"], "raw_title": "Recovery"},
            "source_fact": {
                "business_case_id": source["business_case_id"],
                "source_id": source["source_id"],
            },
            "engineering_context": {"primary_subject": {"value": "evidence-grounded"}},
            "evidence": [
                {"block_id": "B0001", "source_locator": {"paragraph": 1}, "text": "fact"}
            ],
            "conflicts": [],
            "review": {"object_status": "CANDIDATE", "field_decisions": []},
        },
    )


def test_startup_replays_upload_after_file_move_before_database_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_root, _data_root, resolver, source_store = _installed(tmp_path)
    original_connect = source_store._connect
    calls = 0

    def crash_before_source_registry_commit():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise SimulatedPowerLoss()
        return original_connect()

    monkeypatch.setattr(source_store, "_connect", crash_before_source_registry_commit)
    with pytest.raises(SimulatedPowerLoss):
        source_store.register_active_bytes("A0162", "A0162.docx", b"upload bytes")

    monkeypatch.setattr(source_store, "_connect", original_connect)
    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is True, result
    assert result["recovery_status"] == "COMPLETED"
    assert result["pending_local_recovery_count"] == 0
    active = source_store.get_active_source("A0162")
    assert active["source_id"] == hashlib.sha256(b"upload bytes").hexdigest()
    assert source_store.operation_journal.list_nonterminal() == []


def test_startup_replays_delete_after_local_commit_before_candidate_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_root, _data_root, resolver, source_store = _installed(tmp_path)
    repository = source_store.candidate_repository
    assert repository is not None
    source = source_store.register_active_bytes("A0156", "A0156.docx", b"delete bytes")
    candidate = _candidate(repository, source)
    original_invalidate = repository.invalidate_for_source

    def crash_after_hardware_database_commit(*_args, **_kwargs):
        raise SimulatedPowerLoss()

    monkeypatch.setattr(repository, "invalidate_for_source", crash_after_hardware_database_commit)
    with pytest.raises(SimulatedPowerLoss):
        source_store.delete_active_source("A0156", deleted_by="recovery-test")

    monkeypatch.setattr(repository, "invalidate_for_source", original_invalidate)
    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is True, result
    assert result["recovery_status"] == "COMPLETED"
    assert result["pending_local_recovery_count"] == 0
    assert repository.get_candidate(candidate["candidate_id"])["asset_status"] == "INVALIDATED"
    assert source_store.operation_journal.list_nonterminal() == []


def test_remote_publish_ambiguity_is_reported_without_blind_retry_or_domain_block(
    tmp_path: Path,
):
    app_root, data_root, resolver, source_store = _installed(tmp_path)
    source = source_store.register_active_bytes("A9008", "A9008.docx", b"remote pending")
    source_store.operation_journal.prepare(
        operation_id="publish-outcome-unknown",
        operation_type="PUBLISH",
        business_case_id="A9008",
        candidate_id="HCAND-pending",
        source_id=source["source_id"],
        desired_action="PUBLISH_FORMAL_KNOWLEDGE",
        request_fingerprint={"source_id": source["source_id"]},
    )

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is True, result
    assert result["degraded"] is True
    assert result["recovery_status"] == "DEGRADED"
    assert result["pending_remote_reconciliation_count"] == 1
    assert result["blocked_asset_count"] == 1
    assert source_store.operation_journal.list_nonterminal(operation_type="PUBLISH")
    assert (data_root / "manifest" / "hardware_data_manifest.json").is_file()


def test_unknown_nonterminal_operation_blocks_startup_without_mutating_journal(tmp_path: Path):
    app_root, _data_root, resolver, source_store = _installed(tmp_path)
    source_store.operation_journal.prepare(
        operation_id="unsupported-operation",
        operation_type="UNRECOGNIZED_OPERATION",
        business_case_id="A0152",
        candidate_id=None,
        source_id=None,
        desired_action="UNKNOWN",
        request_fingerprint={"input": "preserve"},
    )

    result = HardwareStartupCoordinator(app_root, resolver=resolver).run()

    assert result["ready"] is False
    assert result["error_code"] == "RECOVERY_OPERATION_UNKNOWN"
    assert source_store.operation_journal.list_nonterminal()[0]["operation_state"] == "PREPARED"
