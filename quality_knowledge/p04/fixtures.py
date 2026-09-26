"""Small deterministic fixture provider for P04 development and tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .adapter import P04Provider, ProviderSnapshot


P04_INSIGHT_FIXTURE_V1: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "QS-FIX-001",
        "scenario_name": "发布前配置校验",
        "status": "PUBLISHED",
        "lifecycle": "RELEASE",
        "business_activity": "CONFIGURATION",
        "quality_focus": "CORRECTNESS",
        "failure_mode": "INVALID_CONFIGURATION",
        "trigger_summary": "边界配置组合",
        "failure_mode_summary": "配置校验未拦截",
        "product": "PRODUCT-A",
        "product_family": "FAMILY-A",
        "customer": "CUSTOMER-A",
        "industry": "INDUSTRY-A",
        "ipmt": "IPMT-A",
        "spdt": "SPDT-A",
        "spdt_ipmt": "IPMT-A",
        "source_problem_ids": ["PROBLEM-001"],
    },
    {
        "scenario_id": "QS-FIX-002",
        "scenario_name": "运行期通信恢复",
        "status": "PUBLISHED",
        "lifecycle": "OPERATE",
        "business_activity": "MONITORING",
        "quality_focus": "RELIABILITY",
        "failure_mode": "RECOVERY_TIMEOUT",
        "trigger_summary": "通信短时中断",
        "failure_mode_summary": "恢复窗口超时",
        "product": "PRODUCT-A",
        "product_family": "FAMILY-A",
        "customer": "CUSTOMER-B",
        "industry": "INDUSTRY-A",
        "ipmt": "IPMT-A",
        "spdt": "SPDT-B",
        "spdt_ipmt": "IPMT-A",
        "source_problem_ids": ["PROBLEM-002", "PROBLEM-003"],
    },
    {
        "scenario_id": "QS-FIX-003",
        "scenario_name": "未映射来源问题",
        "status": "PUBLISHED",
        "lifecycle": "DESIGN",
        "business_activity": "DESIGN_REVIEW",
        "quality_focus": "SAFETY",
        "failure_mode": "MISSING_REQUIREMENT",
        "trigger_summary": "需求变更评审",
        "failure_mode_summary": "约束未覆盖",
        "product": "PRODUCT-B",
        "product_family": "FAMILY-B",
        "customer": None,
        "industry": None,
        "ipmt": "IPMT-B",
        "spdt": None,
        "source_problem_ids": [],
    },
)


class FixtureP04Provider:
    """Explicit test provider; never installed as the production default."""

    def __init__(
        self,
        scenarios: Iterable[dict[str, Any]] | None = None,
        *,
        result_revision: str = "fixture-v1",
    ) -> None:
        self._scenarios = tuple(deepcopy(tuple(scenarios or P04_INSIGHT_FIXTURE_V1)))
        self.result_revision = result_revision

    def snapshot(self) -> ProviderSnapshot:
        return ProviderSnapshot(
            scenarios=tuple(deepcopy(self._scenarios)),
            result_revision=self.result_revision,
        )

    def replace(self, scenarios: Iterable[dict[str, Any]], *, result_revision: str) -> None:
        self._scenarios = tuple(deepcopy(tuple(scenarios)))
        self.result_revision = result_revision
