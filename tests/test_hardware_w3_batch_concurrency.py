"""W3-01 Mock-only concurrency proofs; no real Provider configuration needed."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

import pytest

from services.hardware_r1_batch_concurrency import execute_case_batch
from services.hardware_case_r1_workbench import (
    HardwareR1WorkbenchService,
    HardwareR1WorkbenchStore,
)


def test_parallel_overlaps_and_respects_per_batch_limit():
    lock = Lock()
    active = 0
    peak = 0
    seen = []

    def run(item):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        sleep(0.04)
        with lock:
            seen.append(item)
            active -= 1

    execute_case_batch(range(6), worker=run, execution_mode="PARALLEL", concurrency=2)
    assert sorted(seen) == list(range(6))
    assert peak == 2


def test_legacy_sequential_mode_does_not_overlap():
    lock = Lock()
    active = 0
    peak = 0

    def run(_item):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        sleep(0.003)
        with lock:
            active -= 1

    execute_case_batch(range(4), worker=run)
    assert peak == 1


def test_global_pool_limits_cross_batch_case_load():
    lock = Lock()
    active = 0
    peak = 0

    def run(_item):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        sleep(0.02)
        with lock:
            active -= 1

    with ThreadPoolExecutor(max_workers=3) as request_pool:
        calls = [
            request_pool.submit(
                execute_case_batch,
                range(8),
                worker=run,
                execution_mode="PARALLEL",
                concurrency=4,
            )
            for _ in range(3)
        ]
        for call in calls:
            call.result()
    assert 2 <= peak <= 4


def test_case_worker_error_is_isolated():
    processed = []
    failures = []

    def run(item):
        if item == 2:
            raise RuntimeError("synthetic stage failure")
        processed.append(item)

    execute_case_batch(
        range(5),
        worker=run,
        execution_mode="PARALLEL",
        concurrency=3,
        on_error=lambda item, exc: failures.append((item, type(exc).__name__)),
    )
    assert sorted(processed) == [0, 1, 3, 4]
    assert failures == [(2, "RuntimeError")]


@pytest.mark.parametrize("mode,workers", [
    ("INVALID", 2), ("PARALLEL", 0), ("PARALLEL", 5),
    ("PARALLEL", True), ("PARALLEL", 2.5),
])
def test_unsafe_parallel_configuration_rejected(mode, workers):
    with pytest.raises(ValueError):
        execute_case_batch([], worker=lambda _x: None, execution_mode=mode, concurrency=workers)


def test_atomic_case_claim_blocks_simultaneous_stale_requests(tmp_path):
    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = store.create_batch()
    item_id = store.add_item(batch_id, source_file="case.docx")
    same_snapshot = store.get_item(item_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(
            lambda _: store.claim_item_for_run(same_snapshot), range(2)
        ))
    assert sorted(result) == [False, True]
    assert store.get_item(item_id)["orchestration_status"] == "RUNNING"


def test_service_runs_multiple_cases_with_failed_item_isolation(tmp_path, monkeypatch):
    import services.hardware_case_r1_workbench as module

    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = store.create_batch()
    items = []
    for case_id in ("CASE-A", "CASE-B", "CASE-C"):
        item_id = store.add_item(
            batch_id, source_file=f"{case_id}.docx",
            business_case_id=case_id,
            source_id=case_id,
            snapshot={"identity": {"business_case_id": case_id},
                      "source": {"source_id": case_id}},
        )
        items.append(item_id)

    lock = Lock()
    attempted = []

    def fake_pipeline(snapshot, _structurer, **_kwargs):
        case_id = snapshot["identity"]["business_case_id"]
        with lock:
            attempted.append(case_id)
        sleep(0.02)
        if case_id == "CASE-B":
            raise RuntimeError("synthetic failure")
        return {
            "pipeline_status": "CASE_EXTRACTION_FAILED",
            "failed_stage": "STAGE_A",
            "error_code": "SYNTHETIC_STAGE_A_FAILED",
            "latency_trace": {},
        }

    monkeypatch.setattr(module, "run_r1_agent_extraction", fake_pipeline)
    service = HardwareR1WorkbenchService(
        store, source_store=object(), structurer_factory=lambda: object()
    )
    result = service.run_batch(batch_id, execution_mode="PARALLEL", concurrency=2)
    assert sorted(attempted) == ["CASE-A", "CASE-B", "CASE-C"]
    states = {item["business_case_id"]: item["orchestration_status"] for item in result["items"]}
    assert states["CASE-B"] == "RUNTIME_BLOCKED"
    assert states["CASE-A"] == states["CASE-C"] == "FAILED"


def test_concurrent_run_batch_requests_cannot_invoke_same_item_twice(tmp_path, monkeypatch):
    import services.hardware_case_r1_workbench as module

    store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = store.create_batch()
    store.add_item(
        batch_id, source_file="CASE-A.docx",
        business_case_id="CASE-A", source_id="source-a",
        snapshot={"identity": {"business_case_id": "CASE-A"},
                  "source": {"source_id": "source-a"}},
    )
    calls = []
    lock = Lock()

    def fake_pipeline(_snapshot, _structurer, **_kwargs):
        with lock:
            calls.append(1)
        sleep(0.05)
        return {
            "pipeline_status": "CASE_EXTRACTION_FAILED",
            "failed_stage": "STAGE_A",
            "error_code": "TEST_STAGE_A_FAILED",
            "latency_trace": {},
        }

    monkeypatch.setattr(module, "run_r1_agent_extraction", fake_pipeline)
    service = HardwareR1WorkbenchService(
        store, source_store=object(), structurer_factory=lambda: object()
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls_f = [pool.submit(service.run_batch, batch_id) for _ in range(2)]
        for f in calls_f:
            f.result()
    assert calls == [1]
