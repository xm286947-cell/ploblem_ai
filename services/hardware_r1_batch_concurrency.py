"""W3 bounded case-level orchestration; never a replacement for Unified Runtime.

Only whole Hardware cases enter the executor. Per-case Stage A -> validation ->
Stage B remains owned by the frozen pipeline. A single process-wide executor
bounds aggregate case workers even when different batches are submitted.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

# One shared pool per application process, not one pool per Batch/request.
# Deployments with multiple server processes still require W3-02 distributed
# capacity leasing before claiming a deployment-wide concurrency guarantee.
MAX_CASE_WORKERS = 4
_DEFAULT_PARALLEL_WORKERS = 2
_case_executor = ThreadPoolExecutor(
    max_workers=MAX_CASE_WORKERS,
    thread_name_prefix="hardware-w3-case",
)


def execute_case_batch(
    items: Iterable[T],
    *,
    worker: Callable[[T], Any],
    execution_mode: str = "SEQUENTIAL",
    concurrency: int = _DEFAULT_PARALLEL_WORKERS,
    on_error: Callable[[T, Exception], None] | None = None,
) -> None:
    """Run with a per-batch window on a shared bounded case executor.

    SEQUENTIAL is the W2-compatible default. The PARALLEL window limits queued
    futures per Batch; the shared executor limits active cases per process.
    Errors are isolated per item through on_error; callers must fail closed
    when that callback cannot persist the item's failure.
    """
    if execution_mode not in {"SEQUENTIAL", "PARALLEL"}:
        raise ValueError("HARDWARE_W3_EXECUTION_MODE_INVALID")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= MAX_CASE_WORKERS:
        raise ValueError("HARDWARE_W3_CONCURRENCY_INVALID")

    def run_one(item: T) -> None:
        try:
            worker(item)
        except Exception as error:
            if on_error is None:
                raise
            on_error(item, error)

    if execution_mode == "SEQUENTIAL":
        for item in items:
            run_one(item)
        return

    iterator = iter(items)
    pending = {}

    def submit_one() -> bool:
        try:
            item = next(iterator)
        except StopIteration:
            return False
        pending[_case_executor.submit(run_one, item)] = item
        return True

    for _ in range(concurrency):
        if not submit_one():
            break
    while pending:
        completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
        for future in completed:
            pending.pop(future)
            # A persistence/claim failure must not be hidden as a clean result.
            future.result()
            submit_one()
