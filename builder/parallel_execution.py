from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TypeVar

import yaml

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class ParallelExecutionConfig:
    enabled: bool = True
    max_workers: int = 4


def load_parallel_execution_config(root: str | Path) -> ParallelExecutionConfig:
    """Load the shared AI parallel-execution configuration.

    The configuration is intentionally independent from a specific model/provider so
    M8.2 and M8.3 use the same concurrency limit.
    """
    path = Path(root) / "config/model.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("parallel_ai") or {}
    enabled = bool(raw.get("enabled", True))
    try:
        max_workers = int(raw.get("max_workers", 4))
    except (TypeError, ValueError):
        max_workers = 4
    return ParallelExecutionConfig(enabled=enabled, max_workers=max(1, max_workers))


def ordered_map(
    items: Iterable[T],
    worker: Callable[[T], R],
    config: ParallelExecutionConfig,
) -> list[R]:
    """Execute independent AI tasks concurrently while preserving input order."""
    materialized = list(items)
    if not materialized:
        return []
    if not config.enabled or config.max_workers <= 1 or len(materialized) == 1:
        return [worker(item) for item in materialized]
    workers = min(config.max_workers, len(materialized))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="repeat-case-ai") as executor:
        return list(executor.map(worker, materialized))
