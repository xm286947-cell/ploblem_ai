"""W3-02 cross-process capacity, cooperative cancellation and recovery tests.

Mock-only: no real Provider, no Formal Knowledge mutation/publish.
"""
from __future__ import annotations

import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Event, Lock
from time import sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.hardware_case_r1_workbench import (
    HardwareR1WorkbenchService,
    HardwareR1WorkbenchStore,
)
from services.hardware_w3_capacity_gate import (
    HardwareW3CapacityGate,
    W3CapacityTimeout,
)
from quality_knowledge.web.hardware_r1_workbench_api import (
    create_hardware_r1_workbench_router,
)


def _item(number: int, *, case: str | None = None, source: str | None = None):
    return {
        "item_id": f"W3-{number}",
        "batch_id": "BATCH-A",
        "business_case_id": case or f"CASE-{number}",
        "source_id": source or f"SOURCE-{number}",
    }


def _process_acquire(db_path: str, output) -> None:
    gate = HardwareW3CapacityGate(db_path)
    try:
        with gate.lease(_item(99), max_wait_seconds=0.4):
            output.put("ACQUIRED")
    except W3CapacityTimeout:
        output.put("TIMEOUT")


def test_cross_process_capacity_is_fail_closed(tmp_path):
    db = tmp_path / "shared.db"
    # Run the Workbench migration first, exactly as production startup does.
    HardwareR1WorkbenchStore(db)
    gate = HardwareW3CapacityGate(db)
    with ExitStack() as stack:
        for number in range(4):
            stack.enter_context(gate.lease(_item(number)))
        assert len(gate.leases()) == 4
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        worker = ctx.Process(target=_process_acquire, args=(str(db), q))
        worker.start()
        worker.join(timeout=12)
        assert worker.exitcode == 0
        assert q.get(timeout=2) == "TIMEOUT"
        assert len(gate.leases()) == 4
    assert gate.leases() == []
    q2 = ctx.Queue()
    next_worker = ctx.Process(target=_process_acquire, args=(str(db), q2))
    next_worker.start()
    next_worker.join(timeout=12)
    assert next_worker.exitcode == 0
    assert q2.get(timeout=2) == "ACQUIRED"
    assert gate.leases() == []


def test_same_source_is_serialized_even_with_spare_slots(tmp_path):
    db = tmp_path / "shared.db"
    HardwareR1WorkbenchStore(db)
    first = HardwareW3CapacityGate(db)
    second = HardwareW3CapacityGate(db)
    with first.lease(_item(1, case="A", source="HASH"), max_wait_seconds=1):
        with pytest.raises(W3CapacityTimeout):
            with second.lease(
                _item(2, case="A", source="HASH"),
                max_wait_seconds=0.15, poll_seconds=0.01,
            ):
                pass
        assert len(first.leases()) == 1
    with second.lease(_item(2, case="A", source="HASH")):
        assert len(first.leases()) == 1


def test_explicit_reconcile_requires_stopped_worker_and_stale_heartbeat(tmp_path):
    store = HardwareR1WorkbenchStore(tmp_path / "shared.db")
    batch = store.create_batch()
    item_id = store.add_item(
        batch, source_file="case.docx", source_id="a",
        business_case_id="A", snapshot={"identity": {"business_case_id": "A"}},
    )
    gate = HardwareW3CapacityGate(store.db_path)
    item = store.get_item(item_id)
    assert store.claim_item_for_run(item)
    token = gate._try_acquire(item_id, gate.source_key(item))
    assert token
    with pytest.raises(ValueError, match="CONFIRMATION"):
        gate.reconcile_confirmed_stopped(batch, confirmed_stopped=False)
    assert gate.reconcile_confirmed_stopped(
        batch, confirmed_stopped=True, stale_seconds=1
    ) == []
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE hardware_w3_case_lease SET heartbeat_at=0 WHERE token=?",
            (token,),
        )
    assert gate.reconcile_confirmed_stopped(
        batch, confirmed_stopped=True, stale_seconds=1
    ) == [item_id]
    after = store.get_item(item_id)
    assert after["orchestration_status"] == "RUNTIME_BLOCKED"
    assert after["error_code"] == "W3_INTERRUPTED_REQUIRES_RECONCILIATION"
    assert gate.leases() == []


def test_cancel_and_explicit_resume_preserves_other_states(tmp_path):
    store = HardwareR1WorkbenchStore(tmp_path / "shared.db")
    batch = store.create_batch()
    first = store.add_item(batch, source_file="first.docx")
    other = store.add_item(batch, source_file="other.docx")
    store.update_item(
        other, orchestration_status="PARSE_FAILED",
        failed_stage=None, error_code=None, result=None,
    )
    service = HardwareR1WorkbenchService(
        store, source_store=object(), structurer_factory=lambda: object()
    )
    cancelled = service.cancel_batch(batch)
    assert cancelled["cancel_requested"] is True
    assert store.get_item(first)["orchestration_status"] == "CANCELLED"
    assert store.get_item(other)["orchestration_status"] == "PARSE_FAILED"
    assert service.run_batch(batch)["cancel_requested"] is True
    resumed = service.resume_cancelled_batch(batch)
    assert resumed["cancel_requested"] is False
    assert store.get_item(first)["orchestration_status"] == "QUEUED"


def test_cancellation_during_batch_only_stops_not_started_cases(tmp_path, monkeypatch):
    import services.hardware_case_r1_workbench as module

    store = HardwareR1WorkbenchStore(tmp_path / "shared.db")
    batch = store.create_batch()
    for n in range(3):
        store.add_item(
            batch,
            source_file=f"C{n}.docx",
            source_id=f"S{n}",business_case_id=f"C{n}",
            snapshot={"identity":{"business_case_id":f"C{n}"},
                      "source":{"source_id":f"S{n}"}},
        )
    started, release = Event(), Event()
    calls = []

    def synthetic_pipeline(snapshot, _runtime, **_kwargs):
        calls.append(snapshot["identity"]["business_case_id"])
        started.set()
        assert release.wait(5)
        return {"pipeline_status":"CASE_EXTRACTION_FAILED",
                "failed_stage":"STAGE_A", "error_code":"SYNTHETIC", "latency_trace":{}}

    monkeypatch.setattr(module, "run_r1_agent_extraction", synthetic_pipeline)
    service = HardwareR1WorkbenchService(
        store, source_store=object(), structurer_factory=lambda: object()
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(
            service.run_batch, batch, execution_mode="PARALLEL", concurrency=1
        )
        assert started.wait(5)
        service.cancel_batch(batch)
        release.set()
        result = running.result(timeout=10)
    assert calls == ["C0"]
    assert result["summary"]["CANCELLED"] == 2
    assert result["summary"]["FAILED"] == 1
    assert not service.capacity_gate.leases()


def test_http_maintainer_only_cancel_and_safe_resume(tmp_path):
    store = HardwareR1WorkbenchStore(tmp_path / "db.sqlite")
    batch = store.create_batch()
    store.add_item(batch, source_file="case.docx")
    service = HardwareR1WorkbenchService(
        store, source_store=object(), structurer_factory=lambda: object()
    )
    app = FastAPI()
    app.include_router(create_hardware_r1_workbench_router(service))
    client = TestClient(app)
    base = f"/api/v2/hardware-cases/r1/workbench/batches/{batch}"
    assert client.post(base + "/cancel").status_code == 403
    headers = {"X-Hardware-Case-Role": "MAINTAINER"}
    assert client.post(base + "/cancel", headers=headers).status_code == 200
    assert client.post(
        base + "/reconcile-interrupted", headers=headers
    ).status_code == 409
    assert client.post(base + "/resume-cancelled", headers=headers).status_code == 200
    assert service.get_batch(batch)["cancel_requested"] is False
