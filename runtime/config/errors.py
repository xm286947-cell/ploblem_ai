from __future__ import annotations

from typing import Any

from runtime.contracts import ErrorCategory
from runtime.reliability.errors import RuntimeExecutionException


class AgentConfigError(RuntimeExecutionException):
    """Base error for configuration failures before provider execution."""

    category = ErrorCategory.CONFIG
    retryable = False


class ConfigValidationError(AgentConfigError):
    code = "CONFIG_VALIDATION_FAILED"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message, details=details or {})


class ConfigReferenceNotFoundError(AgentConfigError):
    code = "CONFIG_REFERENCE_NOT_FOUND"

    def __init__(self, reference_type: str, reference: str):
        super().__init__(
            f"{reference_type} reference not found: {reference}",
            details={
                "reference_type": reference_type,
                "reference": reference,
            },
        )


class SecretEnvNotFoundError(AgentConfigError):
    code = "SECRET_ENV_NOT_FOUND"

    def __init__(self, env_name: str):
        super().__init__(
            f"required environment variable is not set: {env_name}",
            details={"env_name": env_name},
        )


__all__ = [
    "AgentConfigError",
    "ConfigValidationError",
    "ConfigReferenceNotFoundError",
    "SecretEnvNotFoundError",
]
