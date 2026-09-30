"""Hardware performance/capacity measurement gate.

Budgets are configuration, not product semantics. No numeric production SLO is
invented here: a gate is enforceable only when an approved budget is supplied.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping


class HardwareCapacityError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CapacityBudget:
    startup_ready_ms: float
    public_search_p95_ms: float
    public_detail_p95_ms: float
    tree_navigation_p95_ms: float
    max_cases: int
    max_tree_nodes: int
    concurrent_readers: int


_REQUIRED = {
    "startup_ready_ms",
    "public_search_p95_ms",
    "public_detail_p95_ms",
    "tree_navigation_p95_ms",
    "max_cases",
    "max_tree_nodes",
    "concurrent_readers",
}


def load_capacity_budget() -> CapacityBudget:
    raw = os.environ.get("HARDWARE_CAPACITY_BUDGET_JSON", "").strip()
    if not raw:
        raise HardwareCapacityError("CAPACITY_BUDGET_NOT_APPROVED")
    try:
        value = json.loads(raw)
    except Exception as error:
        raise HardwareCapacityError("CAPACITY_BUDGET_INVALID") from error
    if not isinstance(value, Mapping) or set(value) != _REQUIRED:
        raise HardwareCapacityError("CAPACITY_BUDGET_INVALID")
    try:
        budget = CapacityBudget(
            startup_ready_ms=float(value["startup_ready_ms"]),
            public_search_p95_ms=float(value["public_search_p95_ms"]),
            public_detail_p95_ms=float(value["public_detail_p95_ms"]),
            tree_navigation_p95_ms=float(value["tree_navigation_p95_ms"]),
            max_cases=int(value["max_cases"]),
            max_tree_nodes=int(value["max_tree_nodes"]),
            concurrent_readers=int(value["concurrent_readers"]),
        )
    except Exception as error:
        raise HardwareCapacityError("CAPACITY_BUDGET_INVALID") from error
    if min(
        budget.startup_ready_ms,
        budget.public_search_p95_ms,
        budget.public_detail_p95_ms,
        budget.tree_navigation_p95_ms,
        budget.max_cases,
        budget.max_tree_nodes,
        budget.concurrent_readers,
    ) <= 0:
        raise HardwareCapacityError("CAPACITY_BUDGET_INVALID")
    return budget


def evaluate_measurements(
    budget: CapacityBudget,
    measurements: Mapping[str, float | int],
) -> dict:
    limits = {
        "startup_ready_ms": budget.startup_ready_ms,
        "public_search_p95_ms": budget.public_search_p95_ms,
        "public_detail_p95_ms": budget.public_detail_p95_ms,
        "tree_navigation_p95_ms": budget.tree_navigation_p95_ms,
        "case_count": budget.max_cases,
        "tree_node_count": budget.max_tree_nodes,
        "concurrent_readers": budget.concurrent_readers,
    }
    missing = sorted(set(limits) - set(measurements))
    if missing:
        raise HardwareCapacityError("CAPACITY_MEASUREMENT_INCOMPLETE")
    checks = {
        name: float(measurements[name]) <= float(limit)
        for name, limit in limits.items()
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "measurements": dict(measurements),
        "limits": limits,
    }


__all__ = [
    "CapacityBudget",
    "HardwareCapacityError",
    "evaluate_measurements",
    "load_capacity_budget",
]
