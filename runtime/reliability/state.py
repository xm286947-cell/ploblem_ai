from __future__ import annotations

from runtime.contracts import RuntimeStatus


class InvalidRuntimeStatusTransition(ValueError):
    code = "INVALID_RUNTIME_STATUS_TRANSITION"

    def __init__(
        self,
        previous: RuntimeStatus,
        current: RuntimeStatus,
    ):
        super().__init__(
            f"invalid runtime status transition: {previous.value} -> {current.value}"
        )
        self.previous = previous
        self.current = current


class RuntimeStateMachine:
    """Frozen seven-state transition policy for Task truth."""

    ALLOWED: dict[RuntimeStatus, set[RuntimeStatus]] = {
        RuntimeStatus.QUEUED: {
            RuntimeStatus.QUEUED,
            RuntimeStatus.RUNNING,
            RuntimeStatus.WAITING,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
        },
        RuntimeStatus.RUNNING: {
            RuntimeStatus.RUNNING,
            RuntimeStatus.WAITING,
            RuntimeStatus.PARTIAL,
            RuntimeStatus.COMPLETED,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
        },
        RuntimeStatus.WAITING: {
            RuntimeStatus.WAITING,
            RuntimeStatus.RUNNING,
            RuntimeStatus.PARTIAL,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
        },
        RuntimeStatus.PARTIAL: {
            RuntimeStatus.PARTIAL,
            RuntimeStatus.RUNNING,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
        },
        RuntimeStatus.COMPLETED: {
            RuntimeStatus.COMPLETED,
        },
        RuntimeStatus.FAILED: {
            RuntimeStatus.FAILED,
        },
        RuntimeStatus.CANCELLED: {
            RuntimeStatus.CANCELLED,
        },
    }

    @classmethod
    def validate(
        cls,
        previous: RuntimeStatus,
        current: RuntimeStatus,
    ) -> None:
        if current not in cls.ALLOWED[previous]:
            raise InvalidRuntimeStatusTransition(previous, current)

    @classmethod
    def is_allowed(
        cls,
        previous: RuntimeStatus,
        current: RuntimeStatus,
    ) -> bool:
        return current in cls.ALLOWED[previous]
