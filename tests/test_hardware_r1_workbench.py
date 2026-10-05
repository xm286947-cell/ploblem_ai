from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_case_r1_preview_store import HardwareR1PreviewStore
from services.hardware_case_r1_runtime import (
    R1_STAGE_A_VALIDATOR_VERSION,
    R1_STAGE_B_VALIDATOR_VERSION,
    _R1StageCache,
    invalidate_hardware_r1_stage_cache,
)
from services.hardware_case_r1_workbench import (
    HardwareR1WorkbenchError,
    HardwareR1WorkbenchService,
    HardwareR1WorkbenchStore,
    aggregate_batch_status,
    bind_case_status,
)
import services.hardware_case_r1_workbench as workbench_module


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}


def _snapshot(case_id: str = "A0152", source_id: str = "a" * 64) -> dict:
    return {
        "snapshot_version": "hardware-document-snapshot/v1",
        "source": {"source_id": source_id, "file_name": f"{case_id}-demo.docx"},
        "identity": {
            "source_id": source_id,
            "business_case_id": case_id,
            "raw_title": "demo",
            "identity_status": "PARSED",
            "warnings": [],
        },
        "structure": {
            "blocks": [
                {
                    "block_id": "B0001",
                    "block_type": "PARAGRAPH",
                    "text": "MCU UART demo",
                    "source_locator": {"paragraph": 1},
                }
            ]
        },
        "markdown_view": {
            "view_version": "hardware-markdown-view/v1",
            "markdown": "MCU UART demo",
        },
    }


def _knowledge_object(case_id: str, source_id: str, *, needs_review: bool = False) -> dict:
    conflicts = (
        [
            {
                "conflict_id": "CONFLICT-title-subject",
                "type": "TITLE_CONTENT_SUBJECT_MISMATCH",
                "field": "primary_subject",
                "source_values": [
                    {"source": "SOURCE_RAW_TITLE", "value": "CPU"},
                    {"source": "AI_BODY_CANDIDATE", "value": "MCU串口输出配置"},
                ],
                "evidence_block_ids": ["B0001"],
                "status": "OPEN",
                "resolution_status": "NEEDS_REVIEW",
            }
        ]
        if needs_review
        else []
    )
    return {
        "contract_version": "hardware-case-knowledge-object/v1",
        "identity": {
            "business_case_id": case_id,
            "raw_title": "demo",
            "identity_status": "PARSED",
        },
        "source_fact": {
            "source_id": source_id,
            "business_case_id": case_id,
            "raw_title": "demo",
        },
        "engineering_context": {
            "primary_subject": {
                "value": "MCU串口输出配置",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": ["B0001"],
            }
        },
        "evidence": [
            {
                "block_id": "B0001",
                "source_locator": {"paragraph": 1},
                "text": "MCU UART demo",
            }
        ],
        "conflicts": conflicts,
        "review": {
            "object_status": "CANDIDATE",
            "reviewer": None,
            "reviewed_at": None,
            "field_decisions": [],
        },
        "provenance": {"agent_config_version": "hardware-case-r1-agent/v1"},
    }


def _result(
    *,
    status: str = "PASS",
    failed_stage: str | None = None,
    error_code: str | None = None,
    gate: str = "PASS",
    a_cache: bool = False,
    b_cache: bool = False,
    case_id: str = "A0152",
    source_id: str = "a" * 64,
) -> dict:
    return {
        "pipeline_version": "hardware-r1-agent-pipeline/v1.3.3",
        "execution_trace_version": "hardware-r1-execution-trace/v1.5",
        "pipeline_status": (
            "GOLDEN_PREVIEW_READY"
            if failed_stage is None
            else "PARTIAL_REUSABLE_KNOWLEDGE_FAILED"
            if failed_stage == "STAGE_B"
            else "CASE_EXTRACTION_FAILED"
        ),
        "status": status,
        "failed_stage": failed_stage,
        "error_code": error_code,
        "provider_call_count": 0,
        "validation_retry_count": 0,
        "run_id": "run-1",
        "knowledge_object_contract_version": "hardware-case-knowledge-object/v1",
        "runtime": {
            "stage_a": {"agent_config_version": "hardware-case-r1-stage-a/v1"},
            "stage_b": {"agent_config_version": "hardware-case-r1-stage-b/v1"},
        },
        "evidence_validation": {
            "status": gate,
            "fabricated_fact_count": 0,
            "fabricated_block_id_count": 0,
        },
        "knowledge_object": (
            _knowledge_object(case_id, source_id, needs_review=status == "NEEDS_REVIEW")
            if failed_stage is None and gate == "PASS"
            else None
        ),
        "latency_trace": {
            "TOTAL_MS": 123,
            "CACHE_KEY_VERSION": "v2",
            "STAGE_A_CACHE_HIT": a_cache,
            "STAGE_B_CACHE_HIT": b_cache,
            "STAGE_A_CACHE_RECOVERY_CALL_COUNT": 0,
            "STAGE_B_CACHE_RECOVERY_CALL_COUNT": 0,
            "STAGE_A_TRANSPORT_RETRY_COUNT": 0,
            "STAGE_B_TRANSPORT_RETRY_COUNT": 0,
            "STAGE_A_VALIDATION_RETRY_COUNT": 0,
            "STAGE_B_VALIDATION_RETRY_COUNT": 0,
            "STAGE_A_INPUT_CHARS": 10,
            "STAGE_A_INPUT_BYTES": 10,
            "STAGE_A_OUTPUT_CHARS": 10,
            "STAGE_A_OUTPUT_BYTES": 10,
            "STAGE_B_INPUT_CHARS": 10,
            "STAGE_B_INPUT_BYTES": 10,
            "STAGE_B_OUTPUT_CHARS": 10,
            "STAGE_B_OUTPUT_BYTES": 10,
            "STAGE_A_PROMPT_TOKENS": "UNKNOWN",
            "STAGE_A_COMPLETION_TOKENS": "UNKNOWN",
            "STAGE_B_PROMPT_TOKENS": "UNKNOWN",
            "STAGE_B_COMPLETION_TOKENS": "UNKNOWN",
            "STAGE_A_PROVIDER_ATTEMPTS": [],
            "STAGE_B_PROVIDER_ATTEMPTS": [],
        },
    }


def _commit_fixture_asset(
    repository: CandidateAssetRepository,
    result: dict,
    *,
    case_id: str,
    source_id: str,
) -> dict:
    return repository.create_or_commit_candidate(
        business_case_id=case_id,
        source_id=source_id,
        source_ref=f"r1:{case_id}:{source_id}",
        knowledge_object=result["knowledge_object"],
        generation_run_id=result.get("run_id"),
        pipeline_version=result["pipeline_version"],
        agent_config_version=(
            "stage_a=hardware-case-r1-stage-a/v1;"
            "stage_b=hardware-case-r1-stage-b/v1"
        ),
        knowledge_schema_version=result["knowledge_object_contract_version"],
        validator_version=(
            f"stage_a={R1_STAGE_A_VALIDATOR_VERSION};"
            f"stage_b={R1_STAGE_B_VALIDATOR_VERSION}"
        ),
    )


def _candidate_repository(path: Path) -> CandidateAssetRepository:
    repository = CandidateAssetRepository(path)
    repository.initialize()
    return repository


class _ActiveSourceStub:
    def __init__(self, case_id: str, source_id: str):
        self.case_id = case_id
        self.source_id = source_id
        self.source_ref = f"r1:{case_id}:{source_id}"

    def get_active_source(self, business_case_id: str) -> dict:
        assert business_case_id == self.case_id
        return {
            "business_case_id": self.case_id,
            "binding_status": "ACTIVE",
            "source_id": self.source_id,
            "source_ref": self.source_ref,
        }


def _docx(path: Path) -> bytes:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>MCU UART demo</w:t></w:r></w:p></w:body></w:document>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
    return path.read_bytes()


def test_gate_pass_review_required_maps_to_review_not_publish() -> None:
    review = bind_case_status(
        snapshot=_snapshot(),
        result=_result(status="NEEDS_REVIEW"),
    )
    assert review["gate"] == "PASS"
    assert review["result"] == "REVIEW"

    ready = bind_case_status(snapshot=_snapshot(), result=_result(status="PASS"))
    assert ready["result"] == "CANDIDATE_READY"

    gate_failed = bind_case_status(
        snapshot=_snapshot(),
        result=_result(status="PASS", gate="FAILED"),
    )
    assert gate_failed["gate"] == "FAILED"
    assert gate_failed["result"] == "FAILED"



def test_running_stage_b_retry_preserves_stage_a_last_good_status() -> None:
    running = bind_case_status(
        snapshot=_snapshot(),
        result=_result(
            status="PARTIAL",
            failed_stage="STAGE_B",
            error_code="PROVIDER_TIMEOUT",
            gate="NOT_RUN",
            a_cache=True,
        ),
        orchestration_status="RUNNING",
        error_code=None,
    )
    assert running == {
        "parse": "PASS",
        "stage_a": "CACHE_HIT",
        "stage_b": "RUNNING",
        "gate": "WAITING",
        "result": "RUNNING",
        "failed_stage": "STAGE_B",
        "error_code": None,
        "retryable": False,
    }


def test_existing_workbench_database_gains_candidate_id_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-workbench.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE hardware_r1_batch (
                batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE hardware_r1_batch_item (
                item_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                business_case_id TEXT, source_id TEXT, source_file TEXT NOT NULL,
                orchestration_status TEXT NOT NULL, failed_stage TEXT, error_code TEXT,
                snapshot_json TEXT, result_json TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    store = HardwareR1WorkbenchStore(db_path)
    batch_id = store.create_batch()
    item_id = store.add_item(
        batch_id, source_file="A0152.docx", candidate_id="HCAND-test"
    )
    assert store.get_item(item_id)["candidate_id"] == "HCAND-test"
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(hardware_r1_batch_item)"
            )
        }
    assert "candidate_id" in columns


@pytest.mark.parametrize("status", ["REVIEW", "CANDIDATE_READY"])
def test_final_workbench_state_without_candidate_id_fails_closed(
    tmp_path: Path, status: str
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / f"{status}.db")
    batch_id = store.create_batch()
    pipeline_status = "NEEDS_REVIEW" if status == "REVIEW" else "PASS"
    item_id = store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id="A0152",
        snapshot=_snapshot(),
        result=_result(status=pipeline_status),
        orchestration_status=status,
    )
    item = store.get_item(item_id)
    assert item["result"] == "FAILED"
    assert item["error_code"] == "CANDIDATE_ASSET_NOT_BOUND"
    assert item["candidate_id"] is None
    assert item["retryable"] is True


def test_candidate_commit_precedes_ready_and_survives_restart_and_cache_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id, source_id = "A0152", "a" * 64
    result = _result(case_id=case_id, source_id=source_id)
    store_path = tmp_path / "workbench.db"
    asset_path = tmp_path / "hardware_asset.db"
    runtime_path = tmp_path / "runtime.db"
    store = HardwareR1WorkbenchStore(store_path)
    repository = _candidate_repository(asset_path)
    preview_store = HardwareR1PreviewStore(tmp_path / "preview.db")
    batch_id = store.create_batch()
    item_id = store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id=case_id,
        source_id=source_id,
        snapshot=_snapshot(case_id, source_id),
    )
    monkeypatch.setattr(
        workbench_module,
        "run_r1_agent_extraction",
        lambda *_args, **_kwargs: deepcopy(result),
    )
    cache = _R1StageCache(runtime_path)
    cache.put(
        "STAGE_A",
        "cache-key",
        {"candidate": "stage result"},
        {"run_id": "runtime-run"},
        source_id=source_id,
        validator_version=R1_STAGE_A_VALIDATOR_VERSION,
        pipeline_version=result["pipeline_version"],
        schema_version="stage-a/v1",
        agent_config_hash="config-hash",
        prompt_version="prompt/v1",
    )

    commit_states: list[str] = []
    original_commit = repository.create_or_commit_candidate

    def observe_commit(**kwargs):
        commit_states.append(store.get_item(item_id)["orchestration_status"])
        return original_commit(**kwargs)

    monkeypatch.setattr(repository, "create_or_commit_candidate", observe_commit)
    service = HardwareR1WorkbenchService(
        store,
        source_store=_ActiveSourceStub(case_id, source_id),
        structurer_factory=lambda: object(),
        preview_store=preview_store,
        candidate_repository=repository,
    )

    batch = service.run_batch(batch_id)
    item = batch["items"][0]
    candidate_id = item["candidate_id"]
    assert commit_states == ["RUNNING"]
    assert item["result"] == "CANDIDATE_READY"
    assert candidate_id
    assert item["candidate"]["identity"]["business_case_id"] == case_id
    assert repository.get_candidate(candidate_id)["candidate_id"] == candidate_id

    resumed = service.run_resume_item(item_id)
    assert resumed["candidate_id"] == candidate_id
    assert resumed["result"] == "CANDIDATE_READY"
    with sqlite3.connect(asset_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM hardware_candidate_asset WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM hardware_candidate_event WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()[0] == 1

    assert preview_store.clear() == 2
    assert invalidate_hardware_r1_stage_cache(
        source_id,
        root=tmp_path,
        environ={"HARDWARE_CASE_RUNTIME_DB": str(runtime_path)},
    ) == 1
    assert repository.get_candidate(candidate_id) is not None

    restarted = HardwareR1WorkbenchService(
        HardwareR1WorkbenchStore(store_path),
        source_store=_ActiveSourceStub(case_id, source_id),
        structurer_factory=lambda: object(),
        candidate_repository=_candidate_repository(asset_path),
    ).get_item(item_id)
    assert restarted["candidate_id"] == candidate_id
    assert restarted["candidate"]["identity"]["business_case_id"] == case_id
    assert restarted["result"] == "CANDIDATE_READY"


def test_review_gate_commits_durable_candidate_before_review_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id, source_id = "A0152", "a" * 64
    result = _result(status="NEEDS_REVIEW", case_id=case_id, source_id=source_id)
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    repository = _candidate_repository(tmp_path / "hardware_asset.db")
    batch_id = store.create_batch()
    store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id=case_id,
        source_id=source_id,
        snapshot=_snapshot(case_id, source_id),
    )
    monkeypatch.setattr(
        workbench_module,
        "run_r1_agent_extraction",
        lambda *_args, **_kwargs: deepcopy(result),
    )
    service = HardwareR1WorkbenchService(
        store,
        source_store=_ActiveSourceStub(case_id, source_id),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    item = service.run_batch(batch_id)["items"][0]
    assert item["result"] == "REVIEW"
    assert item["candidate_id"]
    assert item["candidate_asset"]["production_review_status"] == "REQUIRED"


def test_candidate_commit_failure_never_enters_review_or_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id, source_id = "A0152", "a" * 64
    result = _result(status="NEEDS_REVIEW", case_id=case_id, source_id=source_id)

    class FailingCandidateRepository:
        def create_or_commit_candidate(self, **_kwargs):
            raise OSError("simulated durable storage failure")

    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = store.create_batch()
    store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id=case_id,
        source_id=source_id,
        snapshot=_snapshot(case_id, source_id),
    )
    monkeypatch.setattr(
        workbench_module,
        "run_r1_agent_extraction",
        lambda *_args, **_kwargs: deepcopy(result),
    )
    service = HardwareR1WorkbenchService(
        store,
        source_store=_ActiveSourceStub(case_id, source_id),
        structurer_factory=lambda: object(),
        candidate_repository=FailingCandidateRepository(),
    )
    item = service.run_batch(batch_id)["items"][0]
    assert item["result"] == "FAILED"
    assert item["orchestration_status"] == "FAILED"
    assert item["error_code"] == "CANDIDATE_ASSET_COMMIT_FAILED"
    assert item["candidate_id"] is None
    assert item["candidate"] is None


def test_workbench_candidate_display_reads_asset_not_result_json(tmp_path: Path) -> None:
    case_id, source_id = "A0152", "a" * 64
    asset_result = _result(case_id=case_id, source_id=source_id)
    asset_result["knowledge_object"]["engineering_context"]["primary_subject"][
        "value"
    ] = "durable value"
    repository = _candidate_repository(tmp_path / "hardware_asset.db")
    asset = _commit_fixture_asset(
        repository, asset_result, case_id=case_id, source_id=source_id
    )
    debug_result = _result(case_id=case_id, source_id=source_id)
    debug_result["knowledge_object"]["engineering_context"]["primary_subject"][
        "value"
    ] = "debug-only value"
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = store.create_batch()
    item_id = store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id=case_id,
        source_id=source_id,
        snapshot=_snapshot(case_id, source_id),
        result=debug_result,
        orchestration_status="CANDIDATE_READY",
        candidate_id=asset["candidate_id"],
    )
    item = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    ).get_item(item_id)
    assert item["pipeline_result"]["knowledge_object"]["engineering_context"][
        "primary_subject"
    ]["value"] == "debug-only value"
    assert item["candidate"]["engineering_context"]["primary_subject"][
        "value"
    ] == "durable value"


def test_review_candidate_keeps_machine_readable_reason_for_fast_ui(
    tmp_path: Path,
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    repository = _candidate_repository(tmp_path / "hardware_asset.db")
    batch_id = store.create_batch()
    result = _result(status="NEEDS_REVIEW")
    asset = _commit_fixture_asset(
        repository, result, case_id="A0152", source_id="a" * 64
    )
    item_id = store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id="A0152",
        source_id="a" * 64,
        snapshot=_snapshot("A0152"),
        result=result,
        orchestration_status="REVIEW",
        candidate_id=asset["candidate_id"],
    )
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    item = service.get_item(item_id)
    conflict = item["candidate"]["conflicts"][0]
    assert conflict["type"] == "TITLE_CONTENT_SUBJECT_MISMATCH"
    assert conflict["field"] == "primary_subject"
    assert conflict["source_values"] == [
        {"source": "SOURCE_RAW_TITLE", "value": "CPU"},
        {"source": "AI_BODY_CANDIDATE", "value": "MCU串口输出配置"},
    ]
    assert conflict["evidence_block_ids"] == ["B0001"]


def test_review_conflict_commits_to_durable_asset_and_preserves_debug_snapshot(
    tmp_path: Path,
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    asset_path = tmp_path / "hardware_asset.db"
    repository = _candidate_repository(asset_path)
    batch_id = store.create_batch()
    result = _result(status="NEEDS_REVIEW")
    asset = _commit_fixture_asset(
        repository, result, case_id="A0152", source_id="a" * 64
    )
    debug_result = deepcopy(result)
    debug_conflict = debug_result["knowledge_object"]["conflicts"][0]
    debug_conflict["source_values"] = [
        {"source": "DEBUG_ONLY", "value": "must not be reviewed"}
    ]
    debug_result["knowledge_object"]["engineering_context"]["primary_subject"][
        "value"
    ] = "debug snapshot only"
    item_id = store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id="A0152",
        source_id="a" * 64,
        snapshot=_snapshot("A0152"),
        result=debug_result,
        orchestration_status="REVIEW",
        candidate_id=asset["candidate_id"],
    )
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    reviewed = service.resolve_review_conflict(
        item_id,
        conflict_id="CONFLICT-title-subject",
        decision_source="SOURCE_RAW_TITLE",
        reviewer="reviewer-b2",
    )

    durable = repository.get_candidate(asset["candidate_id"])
    assert durable["production_review_status"] == "RESOLVED"
    assert durable["row_version"] == asset["row_version"] + 1
    assert durable["candidate_hash"] != asset["candidate_hash"]
    assert durable["knowledge_object"]["engineering_context"][
        "primary_subject"
    ]["value"] == "CPU"
    conflict = durable["knowledge_object"]["conflicts"][0]
    assert conflict["status"] == "RESOLVED"
    assert conflict["resolution_status"] == "CONFIRMED"
    assert conflict["resolution"]["reviewer"] == "reviewer-b2"
    assert conflict["resolution"]["decision_source"] == "SOURCE_RAW_TITLE"
    assert durable["knowledge_object"]["review"]["field_decisions"] == [
        {
            "conflict_id": "CONFLICT-title-subject",
            "field": "primary_subject",
            "decision_source": "SOURCE_RAW_TITLE",
            "selected_value": "CPU",
            "reviewer": "reviewer-b2",
            "reviewed_at": conflict["resolution"]["reviewed_at"],
        }
    ]
    assert reviewed["result"] == "CANDIDATE_READY"
    assert reviewed["candidate"]["engineering_context"]["primary_subject"][
        "value"
    ] == "CPU"
    assert reviewed["pipeline_result"]["knowledge_object"][
        "engineering_context"
    ]["primary_subject"]["value"] == "debug snapshot only"
    assert reviewed["pipeline_result"]["knowledge_object"]["conflicts"][0][
        "source_values"
    ] == [{"source": "DEBUG_ONLY", "value": "must not be reviewed"}]
    assert reviewed["provider_calls"] == debug_result["provider_call_count"]
    with sqlite3.connect(asset_path) as connection:
        review_rows = connection.execute(
            "SELECT reviewer,reason FROM hardware_candidate_review WHERE candidate_id=?",
            (asset["candidate_id"],),
        ).fetchall()
        audit_rows = connection.execute(
            "SELECT event_type,old_candidate_hash,new_candidate_hash "
            "FROM hardware_candidate_event WHERE candidate_id=? "
            "AND event_type='PRODUCTION_REVIEWED'",
            (asset["candidate_id"],),
        ).fetchall()
    assert review_rows[0][0] == "reviewer-b2"
    assert "CONFLICT-title-subject" in review_rows[0][1]
    assert audit_rows == [
        (
            "PRODUCTION_REVIEWED",
            asset["candidate_hash"],
            durable["candidate_hash"],
        )
    ]


def test_review_commit_restart_recovers_ready_after_workbench_write_failure(
    tmp_path: Path,
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    repository = _candidate_repository(tmp_path / "hardware_asset.db")
    batch_id = store.create_batch()
    result = _result(status="NEEDS_REVIEW")
    asset = _commit_fixture_asset(
        repository, result, case_id="A0152", source_id="a" * 64
    )
    item_id = store.add_item(
        batch_id,
        source_file="A0152.docx",
        business_case_id="A0152",
        source_id="a" * 64,
        snapshot=_snapshot("A0152"),
        result=result,
        orchestration_status="REVIEW",
        candidate_id=asset["candidate_id"],
    )
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    original_update = store.update_item

    def fail_workbench_state_update(*args, **kwargs):
        raise OSError("simulated process interruption after durable commit")

    store.update_item = fail_workbench_state_update  # type: ignore[method-assign]
    with pytest.raises(OSError, match="simulated process interruption"):
        service.resolve_review_conflict(
            item_id,
            conflict_id="CONFLICT-title-subject",
            decision_source="SOURCE_RAW_TITLE",
            reviewer="reviewer-b2",
        )
    store.update_item = original_update  # type: ignore[method-assign]

    restarted = HardwareR1WorkbenchService(
        HardwareR1WorkbenchStore(tmp_path / "workbench.db"),
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=_candidate_repository(tmp_path / "hardware_asset.db"),
    ).get_item(item_id)
    assert restarted["result"] == "CANDIDATE_READY"
    assert restarted["candidate_asset"]["production_review_status"] == "RESOLVED"
    assert restarted["candidate"]["engineering_context"]["primary_subject"][
        "value"
    ] == "CPU"
    assert restarted["pipeline_result"]["knowledge_object"]["conflicts"][0][
        "status"
    ] == "OPEN"


def test_review_conflict_api_uses_durable_review_and_maps_conflict_errors(
    tmp_path: Path,
) -> None:
    app = create_p0_app(
        tmp_path / "quality.db",
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "trees",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )
    service = app.state.hardware_r1_workbench_service
    repository = app.state.hardware_candidate_asset_repository
    repository.initialize()
    result = _result(status="NEEDS_REVIEW")
    asset = _commit_fixture_asset(
        repository, result, case_id="A0152", source_id="a" * 64
    )
    item_id = service.store.add_item(
        service.store.create_batch(),
        source_file="A0152.docx",
        business_case_id="A0152",
        source_id="a" * 64,
        snapshot=_snapshot("A0152"),
        result=result,
        orchestration_status="REVIEW",
        candidate_id=asset["candidate_id"],
    )
    client = TestClient(app)
    response = client.post(
        f"/api/v2/hardware-cases/r1/workbench/items/{item_id}/review-conflicts/"
        "CONFLICT-title-subject/resolve",
        headers=MAINTAINER,
        json={
            "decision_source": "SOURCE_RAW_TITLE",
            "reviewer": "reviewer-api",
        },
    )
    assert response.status_code == 200
    assert response.json()["result"] == "CANDIDATE_READY"
    assert repository.get_candidate(asset["candidate_id"])[
        "production_review_status"
    ] == "RESOLVED"

    second_result = _result(status="NEEDS_REVIEW", case_id="A0153", source_id="b" * 64)
    second_asset = _commit_fixture_asset(
        repository, second_result, case_id="A0153", source_id="b" * 64
    )
    second_item_id = service.store.add_item(
        service.store.create_batch(),
        source_file="A0153.docx",
        business_case_id="A0153",
        source_id="b" * 64,
        snapshot=_snapshot("A0153", "b" * 64),
        result=second_result,
        orchestration_status="REVIEW",
        candidate_id=second_asset["candidate_id"],
    )
    with sqlite3.connect(app.state.hardware_candidate_asset_repository.db_path) as connection:
        connection.execute(
            "UPDATE hardware_candidate_asset SET asset_status='INVALIDATED' "
            "WHERE candidate_id=?",
            (second_asset["candidate_id"],),
        )
    failed = client.post(
        f"/api/v2/hardware-cases/r1/workbench/items/{second_item_id}/review-conflicts/"
        "CONFLICT-title-subject/resolve",
        headers=MAINTAINER,
        json={"decision_source": "SOURCE_RAW_TITLE", "reviewer": "reviewer-api"},
    )
    assert failed.status_code == 409
    assert failed.json()["detail"] == "CANDIDATE_ASSET_INVALIDATED"


@pytest.mark.parametrize(
    ("asset_patch", "expected_code"),
    [
        ({"asset_status": "INVALIDATED"}, "CANDIDATE_ASSET_INVALIDATED"),
        ({"promotion_status": "PRECHECK_PASS"}, "CANDIDATE_LOCKED_BY_PROMOTION"),
    ],
)
def test_review_fails_closed_for_invalidated_or_promoting_candidate(
    tmp_path: Path, asset_patch: dict[str, str], expected_code: str
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    repository = _candidate_repository(tmp_path / "hardware_asset.db")
    result = _result(status="NEEDS_REVIEW")
    asset = _commit_fixture_asset(
        repository, result, case_id="A0152", source_id="a" * 64
    )
    item_id = store.add_item(
        store.create_batch(),
        source_file="A0152.docx",
        business_case_id="A0152",
        source_id="a" * 64,
        snapshot=_snapshot("A0152"),
        result=result,
        orchestration_status="REVIEW",
        candidate_id=asset["candidate_id"],
    )
    column, value = next(iter(asset_patch.items()))
    with sqlite3.connect(tmp_path / "hardware_asset.db") as connection:
        connection.execute(
            f"UPDATE hardware_candidate_asset SET {column}=? WHERE candidate_id=?",
            (value, asset["candidate_id"]),
        )
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    with pytest.raises(HardwareR1WorkbenchError) as error:
        service.resolve_review_conflict(
            item_id,
            conflict_id="CONFLICT-title-subject",
            decision_source="SOURCE_RAW_TITLE",
            reviewer="reviewer-b2",
        )
    assert error.value.code == expected_code
    assert store.get_item(item_id)["result"] == "REVIEW"


def test_concurrent_durable_review_fails_closed_without_ready_state(
    tmp_path: Path,
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    asset_path = tmp_path / "hardware_asset.db"
    repository = _candidate_repository(asset_path)
    result = _result(status="NEEDS_REVIEW")
    asset = _commit_fixture_asset(
        repository, result, case_id="A0152", source_id="a" * 64
    )
    item_id = store.add_item(
        store.create_batch(),
        source_file="A0152.docx",
        business_case_id="A0152",
        source_id="a" * 64,
        snapshot=_snapshot("A0152"),
        result=result,
        orchestration_status="REVIEW",
        candidate_id=asset["candidate_id"],
    )
    apply_review = repository.apply_production_review

    def advance_row_version_then_apply(*args, **kwargs):
        with sqlite3.connect(asset_path) as connection:
            connection.execute(
                "UPDATE hardware_candidate_asset SET row_version=row_version+1 "
                "WHERE candidate_id=?",
                (asset["candidate_id"],),
            )
        return apply_review(*args, **kwargs)

    repository.apply_production_review = advance_row_version_then_apply  # type: ignore[method-assign]
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    with pytest.raises(HardwareR1WorkbenchError) as error:
        service.resolve_review_conflict(
            item_id,
            conflict_id="CONFLICT-title-subject",
            decision_source="SOURCE_RAW_TITLE",
            reviewer="reviewer-b2",
        )
    assert error.value.code == "CANDIDATE_CONCURRENT_UPDATE"
    assert store.get_item(item_id)["result"] == "REVIEW"
    current = repository.get_candidate(asset["candidate_id"])
    assert current["production_review_status"] == "REQUIRED"
    assert current["knowledge_object"]["conflicts"][0]["status"] == "OPEN"


@pytest.mark.parametrize(
    (
        "conflict_id",
        "decision_source",
        "conflict_patch",
        "expected_code",
    ),
    [
        (
            "missing-conflict",
            "SOURCE_RAW_TITLE",
            {},
            "REVIEW_CONFLICT_NOT_FOUND",
        ),
        (
            "CONFLICT-title-subject",
            "UNLISTED_SOURCE",
            {},
            "REVIEW_DECISION_SOURCE_INVALID",
        ),
        (
            "CONFLICT-title-subject",
            "SOURCE_RAW_TITLE",
            {"field": "unfrozen_field"},
            "REVIEW_FIELD_NOT_SUPPORTED",
        ),
        (
            "CONFLICT-title-subject",
            "SOURCE_RAW_TITLE",
            {"resolution_status": "CONFIRMED"},
            "REVIEW_CONFLICT_ALREADY_RESOLVED",
        ),
    ],
)
def test_review_rejects_invalid_conflict_decisions_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conflict_id: str,
    decision_source: str,
    conflict_patch: dict[str, str],
    expected_code: str,
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    repository = _candidate_repository(tmp_path / "hardware_asset.db")
    result = _result(status="NEEDS_REVIEW")
    asset = _commit_fixture_asset(
        repository, result, case_id="A0152", source_id="a" * 64
    )
    item_id = store.add_item(
        store.create_batch(),
        source_file="A0152.docx",
        business_case_id="A0152",
        source_id="a" * 64,
        snapshot=_snapshot("A0152"),
        result=result,
        orchestration_status="REVIEW",
        candidate_id=asset["candidate_id"],
    )
    original_get_candidate = repository.get_candidate

    def get_modified_candidate(candidate_id: str) -> dict | None:
        current = original_get_candidate(candidate_id)
        if current and conflict_patch:
            current["knowledge_object"]["conflicts"][0].update(conflict_patch)
        return current

    monkeypatch.setattr(repository, "get_candidate", get_modified_candidate)
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    with pytest.raises(HardwareR1WorkbenchError) as error:
        service.resolve_review_conflict(
            item_id,
            conflict_id=conflict_id,
            decision_source=decision_source,
            reviewer="reviewer-b2",
        )
    assert error.value.code == expected_code
    assert store.get_item(item_id)["result"] == "REVIEW"
    current = original_get_candidate(asset["candidate_id"])
    assert current["production_review_status"] == "REQUIRED"
    assert current["row_version"] == asset["row_version"]


def test_runtime_and_dependency_blocked_are_not_business_failed() -> None:
    runtime = bind_case_status(
        snapshot=_snapshot(),
        result=None,
        orchestration_status="RUNTIME_BLOCKED",
        error_code="MODEL_LOCAL_CONFIG_REQUIRED",
    )
    dependency = bind_case_status(
        snapshot=_snapshot(),
        result=None,
        orchestration_status="DEPENDENCY_BLOCKED",
        error_code="SOURCE_BINDING_CLOSURE_REQUIRED",
    )
    assert runtime["result"] == "RUNTIME_BLOCKED"
    assert dependency["result"] == "DEPENDENCY_BLOCKED"
    assert runtime["retryable"] is False
    assert dependency["retryable"] is False


def test_batch_aggregate_contract() -> None:
    assert aggregate_batch_status([]) == "EMPTY"
    assert aggregate_batch_status([{"result": "QUEUED"}]) == "QUEUED"
    assert aggregate_batch_status([{"result": "RUNNING"}]) == "RUNNING"
    assert aggregate_batch_status(
        [{"result": "CANDIDATE_READY"}, {"result": "REVIEW"}]
    ) == "READY_FOR_REVIEW"
    assert aggregate_batch_status(
        [{"result": "CANDIDATE_READY"}, {"result": "FAILED"}]
    ) == "PARTIAL_FAILURE"
    assert aggregate_batch_status(
        [{"result": "FAILED"}, {"result": "FAILED"}]
    ) == "FAILED"


def test_retry_failed_only_selects_failed_cases_and_preserves_ready(tmp_path: Path) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    repository = _candidate_repository(tmp_path / "hardware_asset.db")
    batch_id = store.create_batch()
    ready_result = _result(status="PASS", case_id="A1", source_id="a" * 64)
    ready_asset = _commit_fixture_asset(
        repository, ready_result, case_id="A1", source_id="a" * 64
    )
    ready_id = store.add_item(
        batch_id,
        source_file="A1.docx",
        business_case_id="A1",
        source_id="a" * 64,
        snapshot=_snapshot("A1"),
        result=ready_result,
        orchestration_status="CANDIDATE_READY",
        candidate_id=ready_asset["candidate_id"],
    )
    review_result = _result(
        status="NEEDS_REVIEW", case_id="A2", source_id="b" * 64
    )
    review_asset = _commit_fixture_asset(
        repository, review_result, case_id="A2", source_id="b" * 64
    )
    review_id = store.add_item(
        batch_id,
        source_file="A2.docx",
        business_case_id="A2",
        source_id="b" * 64,
        snapshot=_snapshot("A2", "b" * 64),
        result=review_result,
        orchestration_status="REVIEW",
        candidate_id=review_asset["candidate_id"],
    )
    stage_a_id = store.add_item(
        batch_id,
        source_file="A3.docx",
        business_case_id="A3",
        snapshot=_snapshot("A3", "c" * 64),
        result=_result(
            status="FAILED",
            failed_stage="STAGE_A",
            error_code="PROVIDER_TIMEOUT",
            gate="NOT_RUN",
        ),
        orchestration_status="FAILED",
    )
    stage_b_id = store.add_item(
        batch_id,
        source_file="A4.docx",
        business_case_id="A4",
        snapshot=_snapshot("A4", "d" * 64),
        result=_result(
            status="PARTIAL",
            failed_stage="STAGE_B",
            error_code="PROVIDER_TIMEOUT",
            gate="PASS",
            a_cache=True,
        ),
        orchestration_status="FAILED",
    )
    store.add_item(
        batch_id,
        source_file="A5.docx",
        business_case_id="A5",
        snapshot=_snapshot("A5", "e" * 64),
        orchestration_status="RUNNING",
    )

    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
        candidate_repository=repository,
    )
    calls: list[tuple[str, str | None, bool]] = []

    def fake_run(item, *, retry_stage, force_full_run):
        calls.append((item["item_id"], retry_stage, force_full_run))

    service._run_item = fake_run  # type: ignore[method-assign]
    payload = service.retry_failed_only(batch_id)

    assert payload["retry_selected_count"] == 2
    assert set(calls) == {
        (stage_a_id, "STAGE_A", False),
        (stage_b_id, "STAGE_B", False),
    }
    assert ready_id not in {item[0] for item in calls}
    assert review_id not in {item[0] for item in calls}


def test_retry_failed_stage_item_preserves_stage_a_last_good_on_stage_b_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = store.create_batch()
    item_id = store.add_item(
        batch_id,
        source_file="A4.docx",
        business_case_id="A4",
        source_id="d" * 64,
        snapshot=_snapshot("A4", "d" * 64),
        result=_result(
            status="PARTIAL",
            failed_stage="STAGE_B",
            error_code="PROVIDER_TIMEOUT",
            gate="NOT_RUN",
            case_id="A4",
            source_id="d" * 64,
            a_cache=True,
        ),
        orchestration_status="FAILED",
    )
    calls: list[dict] = []

    def fake_pipeline(snapshot, structurer, **kwargs):
        calls.append(dict(kwargs))
        return _result(
            status="PARTIAL",
            failed_stage="STAGE_B",
            error_code="PROVIDER_TIMEOUT",
            gate="NOT_RUN",
            case_id="A4",
            source_id="d" * 64,
            a_cache=True,
        )

    monkeypatch.setattr(workbench_module, "run_r1_agent_extraction", fake_pipeline)
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
    )

    item = service.retry_failed_stage_item(item_id)

    assert calls == [
        {
            "force_retry": False,
            "retry_failed_stage": "STAGE_B",
        }
    ]
    assert item["result"] == "FAILED"
    assert item["failed_stage"] == "STAGE_B"
    assert item["stage_a"] == "CACHE_HIT"
    assert item["stage_b"] == "FAILED"
    assert item["error_code"] == "PROVIDER_TIMEOUT"


def test_advanced_debug_reuses_frozen_trace_and_unknown_tokens(tmp_path: Path) -> None:
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = store.create_batch()
    item_id = store.add_item(
        batch_id,
        source_file="A1.docx",
        business_case_id="A1",
        snapshot=_snapshot("A1"),
        result=_result(status="PASS", a_cache=True, b_cache=True),
        orchestration_status="CANDIDATE_READY",
    )
    service = HardwareR1WorkbenchService(
        store,
        source_store=object(),
        structurer_factory=lambda: object(),
    )
    debug = service.advanced_debug(item_id)
    assert debug["read_only"] is True
    assert debug["cache_key_version"] == "v2"
    assert debug["pipeline_version"] == "hardware-r1-agent-pipeline/v1.3.3"
    assert debug["stage_a"]["cache_hit"] is True
    assert debug["stage_b"]["cache_hit"] is True
    assert debug["stage_a"]["prompt_tokens"] == "UNKNOWN"


def test_batch_upload_is_file_isolated_and_source_binding_dependency_visible(
    tmp_path: Path,
) -> None:
    class SourceBindingStub:
        def register_active_bytes(self, case_id, filename, content, *, mime_type=None):
            return {
                "business_case_id": case_id,
                "source_id": (
                    "a" * 64 if case_id == "A0152" else "b" * 64
                ),
                "binding_status": "ACTIVE",
            }

    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    service = HardwareR1WorkbenchService(
        store,
        source_store=SourceBindingStub(),
        structurer_factory=lambda: object(),
    )
    raw = _docx(tmp_path / "A0152-demo.docx")
    batch = service.upload_batch(
        [
            ("A0152-demo.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("bad.pdf", b"pdf", "application/pdf"),
        ]
    )
    assert batch["summary"]["TOTAL"] == 2
    results = {item["source_file"]: item for item in batch["items"]}
    assert results["A0152-demo.docx"]["result"] == "QUEUED"
    assert results["bad.pdf"]["result"] == "FAILED"
    assert results["bad.pdf"]["failed_stage"] == "PARSE"


def test_workbench_page_and_api_are_bound_in_existing_hardware_host(
    tmp_path: Path,
) -> None:
    app = create_p0_app(
        tmp_path / "quality.db",
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "trees",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )
    assert (
        app.state.hardware_r1_workbench_service.candidate_repository
        is app.state.hardware_candidate_asset_repository
    )
    client = TestClient(app)
    page = client.get("/p0/hardware-cases/knowledge-production")
    assert page.status_code == 200
    assert "知识生产工作台" in page.text
    assert "Retry Failed Only" in page.text
    assert "Advanced Debug" in page.text
    assert "Force Full Run" in page.text
    assert "Error Code" in page.text
    assert "data-detail-error-code" in page.text
    assert "data-review-required" in page.text
    assert "需要人工确认" in page.text

    asset = client.get(
        "/p0/static/hardware_case_knowledge_production.js"
    )
    assert asset.status_code == 200
    assert "retry-failed-only" in asset.text
    assert "advanced-debug" in asset.text
    assert "startBatchPolling" in asset.text
    assert "syncItemIntoBatch" in asset.text
    assert "PROVIDER_TIMEOUT" in asset.text
    assert "openReviewConflicts" in asset.text
    assert "reviewConflictSummary" in asset.text
    assert "renderReviewRequired" in asset.text
    assert "只需要确认下面" in asset.text
    assert "确认采用" in asset.text
    assert "confirmReviewConflict" in asset.text
    assert "review-conflicts/" in asset.text

    blocked = client.get(
        "/api/v2/hardware-cases/r1/workbench/batches"
    )
    assert blocked.status_code == 403

    batches = client.get(
        "/api/v2/hardware-cases/r1/workbench/batches",
        headers=MAINTAINER,
    )
    assert batches.status_code == 200
    assert batches.json()["items"] == []
