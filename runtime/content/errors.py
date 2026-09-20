from __future__ import annotations

from typing import Any


class ContentCoreError(Exception):
    code = "CONTENT_CORE_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code or self.code
        self.details = details or {}


class ContentProjectionRequiredError(ContentCoreError):
    code = "CONTENT_PROJECTION_REQUIRED"


class ContentStrategyNotFoundError(ContentCoreError):
    code = "CONTENT_STRATEGY_NOT_FOUND"


class AtomicUnitTooLargeError(ContentCoreError):
    code = "ATOMIC_UNIT_TOO_LARGE"


class AtomicGroupPartitionMismatchError(ContentCoreError):
    code = "ATOMIC_GROUP_PARTITION_MISMATCH"


class MissingSharedContextError(ContentCoreError):
    code = "MISSING_SHARED_CONTEXT"


class InvalidPartialResultError(ContentCoreError):
    code = "INVALID_PARTIAL_RESULT"


class PartitionMismatchError(ContentCoreError):
    code = "PARTITION_MISMATCH"
