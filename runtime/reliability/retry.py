from __future__ import annotations

from dataclasses import dataclass

from runtime.contracts import ExecutionPolicy, RuntimeStatus
from runtime.reliability.errors import RetryBudgetExhaustedError


@dataclass(frozen=True)
class RetryBudgetSnapshot:
    provider_calls_consumed: int
    provider_calls_limit: int
    exhausted: bool


class RetryCoordinator:
    """Pure budget/policy helper; persistence remains owned by TaskStore."""

    @staticmethod
    def provider_call_limit(policy: ExecutionPolicy) -> int:
        return max(0, policy.retry_budget.max_provider_calls_per_step)

    @classmethod
    def snapshot(cls, policy: ExecutionPolicy, consumed: int) -> RetryBudgetSnapshot:
        limit = cls.provider_call_limit(policy)
        return RetryBudgetSnapshot(
            provider_calls_consumed=consumed,
            provider_calls_limit=limit,
            exhausted=consumed >= limit,
        )

    @classmethod
    def reserve_provider_call(
        cls,
        policy: ExecutionPolicy,
        *,
        execution_key: str,
        consumed: int,
    ) -> int:
        snap = cls.snapshot(policy, consumed)
        if snap.exhausted:
            raise RetryBudgetExhaustedError(
                execution_key,
                snap.provider_calls_consumed,
                snap.provider_calls_limit,
            )
        return consumed + 1

    @staticmethod
    def failure_status(*, retryable: bool, hard_budget_exhausted: bool) -> RuntimeStatus:
        if retryable and not hard_budget_exhausted:
            return RuntimeStatus.PARTIAL
        return RuntimeStatus.FAILED
