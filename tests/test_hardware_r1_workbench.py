from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_case_r1_workbench import (
    HardwareR1WorkbenchService,
    HardwareR1WorkbenchStore,
    aggregate_batch_status,
    bind_case_status,
)


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


def _result(
    *,
    status: str = "PASS",
    failed_stage: str | None = None,
    error_code: str | None = None,
    gate: str = "PASS",
    a_cache: bool = False,
    b_cache: bool = False,
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
        "evidence_validation": {
            "status": gate,
            "fabricated_fact_count": 0,
            "fabricated_block_id_count": 0,
        },
        "knowledge_object": (
            {
                "contract_version": "hardware-case-knowledge-object/v1",
                "review": {"object_status": "CANDIDATE"},
            }
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
    batch_id = store.create_batch()
    ready_id = store.add_item(
        batch_id,
        source_file="A1.docx",
        business_case_id="A1",
        snapshot=_snapshot("A1"),
        result=_result(status="PASS"),
        orchestration_status="CANDIDATE_READY",
    )
    review_id = store.add_item(
        batch_id,
        source_file="A2.docx",
        business_case_id="A2",
        snapshot=_snapshot("A2", "b" * 64),
        result=_result(status="NEEDS_REVIEW"),
        orchestration_status="REVIEW",
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
    client = TestClient(
        create_p0_app(
            tmp_path / "quality.db",
            hardware_case_db_path=tmp_path / "hardware.db",
            hardware_tree_upload_dir=tmp_path / "trees",
            hardware_case_source_root=tmp_path / "sources",
            enabled_domains={"HARDWARE_CASE"},
        )
    )
    page = client.get("/p0/hardware-cases/knowledge-production")
    assert page.status_code == 200
    assert "知识生产工作台" in page.text
    assert "Retry Failed Only" in page.text
    assert "Advanced Debug" in page.text
    assert "Force Full Run" in page.text
    assert "Error Code" in page.text
    assert "data-detail-error-code" in page.text

    asset = client.get(
        "/p0/static/hardware_case_knowledge_production.js"
    )
    assert asset.status_code == 200
    assert "retry-failed-only" in asset.text
    assert "advanced-debug" in asset.text
    assert "startBatchPolling" in asset.text
    assert "syncItemIntoBatch" in asset.text
    assert "PROVIDER_TIMEOUT" in asset.text

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
