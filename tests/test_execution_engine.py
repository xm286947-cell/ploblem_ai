from __future__ import annotations

import threading
import time

import pytest

from builder.execution_engine import ExecutionEngine, ExecutionPlan
from builder.parallel_execution import ParallelExecutionConfig, ordered_map


def test_execution_engine_parallel_preserves_input_order() -> None:
    thread_ids: set[int] = set()
    lock = threading.Lock()

    def worker(value: int) -> int:
        with lock:
            thread_ids.add(threading.get_ident())
        time.sleep(0.03)
        return value * 10

    result = ExecutionEngine().map(
        range(6),
        worker,
        ExecutionPlan(mode="PARALLEL", max_concurrency=3),
    )
    assert result == [0, 10, 20, 30, 40, 50]
    assert len(thread_ids) >= 2


def test_execution_engine_sequential_uses_one_thread() -> None:
    thread_ids: set[int] = set()

    def worker(value: int) -> int:
        thread_ids.add(threading.get_ident())
        return value

    result = ExecutionEngine().map(
        [1, 2, 3],
        worker,
        ExecutionPlan(mode="SEQUENTIAL", max_concurrency=4),
    )
    assert result == [1, 2, 3]
    assert len(thread_ids) == 1


def test_execution_engine_completion_order_isolates_failure() -> None:
    def worker(value: int) -> int:
        if value == 2:
            raise RuntimeError("TEST_ITEM_FAILED")
        if value == 1:
            time.sleep(0.04)
        return value * 10

    results = list(ExecutionEngine().iter_completed(
        [1, 2, 3],
        worker,
        ExecutionPlan(
            mode="PARALLEL",
            max_concurrency=3,
            preserve_input_order=False,
            fail_policy="CONTINUE",
        ),
    ))
    assert sorted(item.item for item in results) == [1, 2, 3]
    failed = next(item for item in results if item.item == 2)
    assert isinstance(failed.error, RuntimeError)
    assert str(failed.error) == "TEST_ITEM_FAILED"
    assert {item.item: item.value for item in results if item.succeeded} == {1: 10, 3: 30}


def test_execution_engine_completion_order_fail_fast() -> None:
    def worker(value: int) -> int:
        if value == 2:
            raise RuntimeError("TEST_EXECUTION_FAILED")
        return value

    with pytest.raises(RuntimeError, match="TEST_EXECUTION_FAILED"):
        list(ExecutionEngine().iter_completed(
            [1, 2, 3],
            worker,
            ExecutionPlan(
                mode="PARALLEL",
                max_concurrency=2,
                preserve_input_order=False,
                fail_policy="FAIL_FAST",
            ),
        ))


def test_parallel_execution_api_remains_compatible() -> None:
    result = ordered_map(
        [1, 2, 3],
        lambda value: value + 1,
        ParallelExecutionConfig(enabled=True, max_workers=2),
    )
    assert result == [2, 3, 4]
