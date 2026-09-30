"""Hardware production failure classification and recovery policy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailurePolicy:
    category: str
    retryable: bool
    readiness_impact: str
    recovery: str


_FAILURES = {
    "KNOWLEDGE_UNAVAILABLE": FailurePolicy("DEPENDENCY_TRANSIENT", True, "DEGRADED", "RETRY_WITH_RUNTIME_BUDGET"),
    "RUNTIME_EXECUTION_FAILED": FailurePolicy("DEPENDENCY_EXECUTION", True, "DEGRADED", "RUNTIME_OWNS_RETRY"),
    "AUTHENTICATION_REQUIRED": FailurePolicy("ACCESS", False, "NONE", "CALLER_REAUTHENTICATE"),
    "AUTHENTICATION_INVALID": FailurePolicy("ACCESS", False, "NONE", "CALLER_REAUTHENTICATE"),
    "AUTHORIZATION_DENIED": FailurePolicy("ACCESS", False, "NONE", "DENY"),
    "UNKNOWN_SCHEMA_VERSION": FailurePolicy("DATA_INCOMPATIBLE", False, "BLOCKED", "OPERATOR_MIGRATION_OR_RESTORE"),
    "MIGRATION_FAILED_RECOVERY_REQUIRED": FailurePolicy("DATA_RECOVERY", False, "BLOCKED", "RESTORE_AND_VERIFY"),
    "SCHEMA_DRIFT_DETECTED": FailurePolicy("DATA_DRIFT", False, "BLOCKED", "OPERATOR_REPAIR"),
}


def classify_failure(code: str) -> FailurePolicy:
    return _FAILURES.get(
        str(code or "").strip().upper(),
        FailurePolicy("APPLICATION_PERMANENT", False, "NONE", "FIX_INPUT_OR_DEFECT"),
    )


__all__ = ["FailurePolicy", "classify_failure"]
