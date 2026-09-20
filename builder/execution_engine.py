from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Iterator, Literal, TypeVar

T = TypeVar("T")
R = TypeVar("R")

ExecutionMode = Literal["SEQUENTIAL", "PARALLEL"]


@dataclass(frozen=True)
class ExecutionPlan:
    """Shared execution plan kept intentionally small for the adapter migration."""

    mode: ExecutionMode = "PARALLEL"
    max_concurrency: int = 4
    preserve_input_order: bool = True
    fail_policy: str = "FAIL_FAST"
    thread_name_prefix: str = "shared-exec"

    def validate(self) -> None:
        if self.mode not in {"SEQUENTIAL", "PARALLEL"}:
            raise ValueError(f"EXECUTION_MODE_UNSUPPORTED:{self.mode}")
        if self.max_concurrency < 1:
            raise ValueError("EXECUTION_CONCURRENCY_INVALID")
        if self.fail_policy not in {"FAIL_FAST", "CONTINUE"}:
            raise ValueError(f"EXECUTION_FAIL_POLICY_UNSUPPORTED:{self.fail_policy}")


@dataclass(frozen=True)
class ExecutionItemResult(Generic[T, R]):
    index: int
    item: T
    value: R | None = None
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class ExecutionEngine:
    """Shared execution mechanics.

    V0.7 supports:
    - ordered fail-fast map for compatibility callers;
    - completion-order iteration with optional per-item exception isolation.

    Retry, checkpoint/resume, progress persistence and pipeline orchestration remain
    deferred until a consumer requires them.
    """

    def map(
        self,
        items: Iterable[T],
        worker: Callable[[T], R],
        plan: ExecutionPlan,
    ) -> list[R]:
        plan.validate()
        if not plan.preserve_input_order:
            raise ValueError("EXECUTION_ORDERED_MAP_REQUIRES_INPUT_ORDER")
        materialized = list(items)
        if not materialized:
            return []

        workers = min(plan.max_concurrency, len(materialized))
        if plan.mode == "SEQUENTIAL" or workers <= 1 or len(materialized) == 1:
            return [worker(item) for item in materialized]

        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=plan.thread_name_prefix,
        ) as executor:
            return list(executor.map(worker, materialized))

    def iter_completed(
        self,
        items: Iterable[T],
        worker: Callable[[T], R],
        plan: ExecutionPlan,
    ) -> Iterator[ExecutionItemResult[T, R]]:
        """Yield results as items finish; CONTINUE isolates worker exceptions."""
        plan.validate()
        if plan.preserve_input_order:
            raise ValueError("EXECUTION_COMPLETION_ITERATION_REQUIRES_COMPLETION_ORDER")

        materialized = list(items)
        if not materialized:
            return

        def resolve(index: int, item: T, call: Callable[[], R]) -> ExecutionItemResult[T, R]:
            try:
                return ExecutionItemResult(index=index, item=item, value=call())
            except Exception as error:
                if plan.fail_policy == "FAIL_FAST":
                    raise
                return ExecutionItemResult(index=index, item=item, error=error)

        workers = min(plan.max_concurrency, len(materialized))
        if plan.mode == "SEQUENTIAL" or workers <= 1 or len(materialized) == 1:
            for index, item in enumerate(materialized):
                yield resolve(index, item, lambda item=item: worker(item))
            return

        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=plan.thread_name_prefix,
        ) as executor:
            futures = {
                executor.submit(worker, item): (index, item)
                for index, item in enumerate(materialized)
            }
            for future in as_completed(futures):
                index, item = futures[future]
                try:
                    yield ExecutionItemResult(index=index, item=item, value=future.result())
                except Exception as error:
                    if plan.fail_policy == "FAIL_FAST":
                        raise
                    yield ExecutionItemResult(index=index, item=item, error=error)
