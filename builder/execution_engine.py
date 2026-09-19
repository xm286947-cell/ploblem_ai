from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, TypeVar

T = TypeVar("T")
R = TypeVar("R")

ExecutionMode = Literal["SEQUENTIAL", "PARALLEL"]


@dataclass(frozen=True)
class ExecutionPlan:
    """Minimal shared execution plan used by the first adapter migration."""

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
        if not self.preserve_input_order:
            raise ValueError("EXECUTION_UNORDERED_NOT_IMPLEMENTED")
        if self.fail_policy != "FAIL_FAST":
            raise ValueError(f"EXECUTION_FAIL_POLICY_UNSUPPORTED:{self.fail_policy}")


class ExecutionEngine:
    """Shared execution mechanics with intentionally small V0.6 scope.

    V0.6 only centralizes ordered sequential/parallel map semantics. Retry,
    checkpoint/resume, progress, and structured partial-success results remain
    behind the frozen contract until a consumer requires them.
    """

    def map(
        self,
        items: Iterable[T],
        worker: Callable[[T], R],
        plan: ExecutionPlan,
    ) -> list[R]:
        plan.validate()
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
