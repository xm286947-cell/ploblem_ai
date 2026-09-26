"""Runtime Observation service and fail-closed validation boundary."""

from __future__ import annotations

from runtime.contracts import (
    RuntimeObservation,
    RuntimeObservationAvailability,
    RuntimeObservationQuality,
)
from runtime.store.observation import (
    RuntimeObservationAlreadyExists,
    RuntimeObservationRepository,
)


class RuntimeObservationError(ValueError):
    def __init__(self, code: str, *, details: list[str] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or []


_FORBIDDEN_SOURCE_TOKENS = {"ai", "mock", "datasheet", "fixture", "generated"}


class RuntimeObservationService:
    def __init__(self, repository: RuntimeObservationRepository):
        self.repository = repository

    @staticmethod
    def _source_is_forbidden(source: str | None) -> bool:
        if not source:
            return False
        normalized = source.strip().lower().replace("_", " ").replace("-", " ")
        return any(token in normalized.split() for token in _FORBIDDEN_SOURCE_TOKENS)

    @classmethod
    def validate_formal_consumption(cls, observation: RuntimeObservation) -> None:
        """Raise when an AVAILABLE observation would be consumed formally."""

        if observation.availability_status is not RuntimeObservationAvailability.AVAILABLE:
            return
        errors: list[str] = []
        if observation.capture_time is None:
            errors.append("CAPTURE_TIME_REQUIRED")
        if not observation.source_command_or_interface:
            errors.append("SOURCE_COMMAND_OR_INTERFACE_REQUIRED")
        if not observation.evidence_ref:
            errors.append("EVIDENCE_REF_REQUIRED")
        if observation.quality_status is not RuntimeObservationQuality.VALID:
            errors.append("QUALITY_STATUS_MUST_BE_VALID")
        if not observation.unit or not observation.unit.strip():
            errors.append("UNIT_REQUIRED")
        if observation.normalized_value is None:
            errors.append("NORMALIZED_VALUE_REQUIRED")
        if cls._source_is_forbidden(observation.source_command_or_interface):
            errors.append("RUNTIME_SOURCE_NOT_AUTHORIZED")
        if errors:
            raise RuntimeObservationError("RUNTIME_OBSERVATION_FAIL_CLOSED", details=errors)

    @classmethod
    def validate_shape(cls, observation: RuntimeObservation) -> None:
        errors: list[str] = []
        for field_name in ("observation_id", "device_id", "device_type", "metric_name"):
            if not getattr(observation, field_name).strip():
                errors.append(f"{field_name.upper()}_REQUIRED")
        if observation.availability_status is RuntimeObservationAvailability.NOT_AVAILABLE:
            if observation.normalized_value is not None:
                errors.append("NOT_AVAILABLE_VALUE_MUST_BE_NULL")
            if not observation.missing_reason:
                errors.append("MISSING_REASON_REQUIRED")
        if observation.availability_status is RuntimeObservationAvailability.NOT_SUPPORTED:
            if not observation.missing_reason:
                errors.append("MISSING_REASON_REQUIRED")
        if errors:
            raise RuntimeObservationError("RUNTIME_OBSERVATION_INVALID", details=errors)
        cls.validate_formal_consumption(observation)

    def create(self, observation: RuntimeObservation) -> RuntimeObservation:
        self.validate_shape(observation)
        try:
            return self.repository.create(observation)
        except RuntimeObservationAlreadyExists as error:
            raise RuntimeObservationError(str(error)) from error

    def get(self, observation_id: str) -> RuntimeObservation | None:
        return self.repository.get(observation_id)

    def list(
        self,
        *,
        device_id: str,
        metric_name: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeObservation]:
        return self.repository.list(device_id=device_id, metric_name=metric_name, limit=limit)

    @staticmethod
    def formal_consumption(observation: RuntimeObservation) -> dict[str, object]:
        return {
            "allowed": observation.is_formally_consumable,
            "reason": None
            if observation.is_formally_consumable
            else "RUNTIME_OBSERVATION_NOT_FORMALLY_CONSUMABLE",
        }
