"""Validation of native V2 stage responses.

No legacy-shaped output is translated here. A runner must emit the exact V2
stage object, otherwise the failure is persisted with the stage and JSON path.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from quality_knowledge.models.analysis_v2 import (
    CapabilityGapV2DTO,
    EscapeAnalysisV2DTO,
    OccurrenceAnalysisV2DTO,
    RecurrenceAnalysisV2DTO,
)


class V2StageNormalizationError(ValueError):
    """A V2 runner output error with a stable stage/path diagnostic."""

    def __init__(self, stage: str, path: str, code: str, message: str):
        self.stage = stage
        self.path = path
        self.code = code
        super().__init__(f"{code}:stage={stage}:path={path}:{message}")


def normalize_stage_v2(stage: str, payload: Any) -> Any:
    """Return the strict DTO for one stage or raise an explicit V2 error."""

    if stage not in {"occurrence", "escape", "recurrence", "capability_gap"}:
        raise V2StageNormalizationError(stage, "$", "V2_STAGE_UNKNOWN", "unsupported stage")
    if not isinstance(payload, dict):
        raise V2StageNormalizationError(stage, "$", "V2_STAGE_PAYLOAD_MUST_BE_OBJECT", "expected object")

    model_by_stage = {
        "occurrence": OccurrenceAnalysisV2DTO,
        "escape": EscapeAnalysisV2DTO,
        "recurrence": RecurrenceAnalysisV2DTO,
    }
    if stage in model_by_stage:
        expected_keys = set(model_by_stage[stage].model_fields)
        unexpected = sorted(set(payload) - expected_keys)
        if unexpected:
            raise V2StageNormalizationError(
                stage,
                f"$.{unexpected[0]}",
                "V2_STAGE_KEYS_INVALID",
                "unknown V2 key",
            )
        missing = sorted(expected_keys - set(payload))
        if missing:
            raise V2StageNormalizationError(
                stage,
                f"$.{missing[0]}",
                "V2_STAGE_REQUIRED_KEY_MISSING",
                "runner must emit every native V2 field",
            )
    try:
        if stage == "occurrence":
            return OccurrenceAnalysisV2DTO.model_validate(payload)
        if stage == "escape":
            return EscapeAnalysisV2DTO.model_validate(payload)
        if stage == "recurrence":
            return RecurrenceAnalysisV2DTO.model_validate(payload)

        if set(payload) != {"capability_gaps"}:
            raise V2StageNormalizationError(
                stage,
                "$",
                "V2_STAGE_KEYS_INVALID",
                "capability_gap requires only capability_gaps",
            )
        gaps = payload["capability_gaps"]
        if not isinstance(gaps, list):
            raise V2StageNormalizationError(
                stage,
                "$.capability_gaps",
                "V2_CAPABILITY_GAPS_REQUIRED",
                "expected list",
            )
        return [CapabilityGapV2DTO.model_validate(item) for item in gaps]
    except V2StageNormalizationError:
        raise
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ("$",)))
        raise V2StageNormalizationError(
            stage,
            f"$.{location}" if location != "$" else "$",
            "V2_STAGE_VALIDATION_FAILED",
            first.get("msg", "validation failed"),
        ) from error
