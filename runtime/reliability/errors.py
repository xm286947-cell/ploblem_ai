from __future__ import annotations

from typing import Any

from runtime.contracts import ErrorCategory, RuntimeErrorInfo


class RuntimeExecutionException(Exception):
    code = "RUNTIME_EXECUTION_ERROR"
    category = ErrorCategory.EXECUTION
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        category: ErrorCategory | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code or self.code
        self.category = category or self.category
        self.retryable = self.retryable if retryable is None else retryable
        self.details = details or {}

    def as_error_info(self) -> RuntimeErrorInfo:
        return RuntimeErrorInfo(
            code=self.code,
            category=self.category,
            message=str(self),
            retryable=self.retryable,
            details=self.details,
        )


class IdempotencyConflictError(RuntimeExecutionException):
    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, request_id: str, task_id: str):
        super().__init__(
            f"request_id {request_id!r} already belongs to a different request fingerprint",
            details={"request_id": request_id, "task_id": task_id},
        )
        self.request_id = request_id
        self.task_id = task_id


class ExistingTaskNotCompleteError(RuntimeExecutionException):
    code = "TASK_ALREADY_EXISTS"

    def __init__(self, task_id: str, status: str):
        super().__init__(
            f"task {task_id} already exists with status {status}",
            retryable=True,
            details={"task_id": task_id, "status": status},
        )
        self.task_id = task_id


class TaskNotResumableError(RuntimeExecutionException):
    code = "TASK_NOT_RESUMABLE"

    def __init__(self, task_id: str, status: str):
        super().__init__(
            f"task {task_id} cannot be resumed from status {status}",
            details={"task_id": task_id, "status": status},
        )


class RetryBudgetExhaustedError(RuntimeExecutionException):
    code = "RETRY_BUDGET_EXHAUSTED"

    def __init__(self, execution_key: str, consumed: int, limit: int):
        super().__init__(
            f"provider call budget exhausted for {execution_key}: {consumed}/{limit}",
            retryable=False,
            details={
                "execution_key": execution_key,
                "provider_calls_consumed": consumed,
                "provider_calls_limit": limit,
            },
        )


class RuntimeStepError(RuntimeExecutionException):
    """Categorized handler/provider error used by the D3 retry coordinator."""

    pass


class SimulatedCrash(BaseException):
    """Fault-injection signal that intentionally bypasses normal error handling."""

    def __init__(self, point: str):
        super().__init__(f"simulated crash at {point}")
        self.point = point
