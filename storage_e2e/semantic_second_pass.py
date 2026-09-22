from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from runtime import AgentRequest, RuntimeStatus
from runtime.adapters import StorageFieldResult
from runtime.config import ConfiguredAgentRuntime


PRIMARY_AGENT_ID = "storage.emmc.parameter_extract"
SECOND_PASS_AGENT_ID = "storage.emmc.semantic_reextract"


@dataclass(frozen=True)
class StorageSemanticSecondPassOutcome:
    initial_result: Any
    final_runtime_result: Any
    second_pass_result: Any | None
    second_pass_triggered: bool
    semantic_handoff_ref: str | None
    reviewed_specification: list[StorageFieldResult] | None
    accepted: bool
    business_error: str | None = None


class StorageSemanticSecondPassCoordinator:
    """Storage-owned semantic second-pass orchestration.

    Runtime owns Provider execution, strict JSON/schema validation, retry,
    fail-closed semantic handoff storage and controlled handoff reads.

    Storage owns the decision to consume a semantic handoff and ask a
    Storage-specific second agent to re-extract the required fields.
    """

    def __init__(
        self,
        runtime: ConfiguredAgentRuntime,
        *,
        primary_agent_id: str = PRIMARY_AGENT_ID,
        second_pass_agent_id: str = SECOND_PASS_AGENT_ID,
    ) -> None:
        self.runtime = runtime
        self.primary_agent_id = primary_agent_id
        self.second_pass_agent_id = second_pass_agent_id

    @staticmethod
    def _reviewed(result: Any) -> list[StorageFieldResult]:
        return [
            StorageFieldResult.model_validate(item)
            for item in (result.data or [])
        ]

    @staticmethod
    def _required_field_ids(payload: dict[str, Any]) -> list[str]:
        values = payload.get("required_fields") or []
        return [str(value) for value in values]

    @classmethod
    def _field_set_error(
        cls,
        payload: dict[str, Any],
        reviewed: list[StorageFieldResult],
    ) -> str | None:
        required = cls._required_field_ids(payload)
        if not required:
            return None
        required_set = set(required)
        returned = [item.field_id for item in reviewed]
        returned_set = set(returned)
        if len(returned) != len(returned_set):
            return "SECOND_PASS_DUPLICATE_FIELD_ID"
        if returned_set != required_set:
            return "SECOND_PASS_FIELD_SET_MISMATCH"
        return None

    @staticmethod
    def _handoff_is_eligible(result: Any) -> bool:
        if result.error is None:
            return False
        if result.error.code != "SEMANTIC_REPAIR_REQUIRED":
            return False
        details = result.error.details or {}
        return bool(
            details.get("recoverable_content_available")
            and details.get("content_ref")
            and details.get("content_hash")
            and isinstance(details.get("content_length"), int)
            and details.get("raw_finish_reason") != "length"
        )

    @staticmethod
    def _verify_handoff_content(
        content: str,
        *,
        expected_hash: str,
        expected_length: int,
    ) -> bool:
        if len(content) != expected_length:
            return False
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return actual_hash == expected_hash

    @staticmethod
    def _second_pass_payload(
        original_payload: dict[str, Any],
        *,
        provider_material: str,
    ) -> dict[str, Any]:
        # Deliberately do not forward source_text/PDF content. The second pass
        # analyzes only the controlled first-Provider material plus the Storage
        # extraction scope.
        return {
            "device_type": original_payload.get("device_type"),
            "parameter_scope": original_payload.get("parameter_scope"),
            "required_fields": list(original_payload.get("required_fields") or []),
            "provider_material": provider_material,
        }

    def execute(
        self,
        payload: dict[str, Any],
        *,
        request_id: str,
    ) -> StorageSemanticSecondPassOutcome:
        initial = self.runtime.invoke(
            AgentRequest(
                request_id=request_id,
                agent_id=self.primary_agent_id,
                input=payload,
                metadata={
                    "business_domain": "STORAGE",
                    "semantic_pass": 1,
                },
            )
        )

        if initial.status == RuntimeStatus.COMPLETED:
            reviewed = self._reviewed(initial)
            field_error = self._field_set_error(payload, reviewed)
            return StorageSemanticSecondPassOutcome(
                initial_result=initial,
                final_runtime_result=initial,
                second_pass_result=None,
                second_pass_triggered=False,
                semantic_handoff_ref=None,
                reviewed_specification=(
                    reviewed if field_error is None else None
                ),
                accepted=field_error is None,
                business_error=field_error,
            )

        if not self._handoff_is_eligible(initial):
            return StorageSemanticSecondPassOutcome(
                initial_result=initial,
                final_runtime_result=initial,
                second_pass_result=None,
                second_pass_triggered=False,
                semantic_handoff_ref=None,
                reviewed_specification=None,
                accepted=False,
                business_error="PRIMARY_RESULT_NOT_ELIGIBLE_FOR_SECOND_PASS",
            )

        details = initial.error.details
        content_ref = str(details["content_ref"])
        content = self.runtime.read_semantic_handoff_content(
            task_id=initial.task_id,
            content_ref=content_ref,
        )
        if not isinstance(content, str) or not self._verify_handoff_content(
            content,
            expected_hash=str(details["content_hash"]),
            expected_length=int(details["content_length"]),
        ):
            return StorageSemanticSecondPassOutcome(
                initial_result=initial,
                final_runtime_result=initial,
                second_pass_result=None,
                second_pass_triggered=False,
                semantic_handoff_ref=content_ref,
                reviewed_specification=None,
                accepted=False,
                business_error="SEMANTIC_HANDOFF_INTEGRITY_FAILED",
            )

        second_payload = self._second_pass_payload(
            payload,
            provider_material=content,
        )
        second = self.runtime.invoke(
            AgentRequest(
                request_id=f"{request_id}:semantic-pass-2",
                agent_id=self.second_pass_agent_id,
                input=second_payload,
                metadata={
                    "business_domain": "STORAGE",
                    "semantic_pass": 2,
                    "parent_task_id": initial.task_id,
                    "semantic_handoff_ref": content_ref,
                    "semantic_handoff_hash": details["content_hash"],
                },
            )
        )

        # There is intentionally no recursive third business pass. Runtime may
        # apply its configured transport/validation retry policy inside this
        # second Agent invocation, but a terminal second-pass failure remains
        # fail-closed here.
        if second.status != RuntimeStatus.COMPLETED:
            return StorageSemanticSecondPassOutcome(
                initial_result=initial,
                final_runtime_result=second,
                second_pass_result=second,
                second_pass_triggered=True,
                semantic_handoff_ref=content_ref,
                reviewed_specification=None,
                accepted=False,
                business_error="SECOND_PASS_FAILED",
            )

        reviewed = self._reviewed(second)
        field_error = self._field_set_error(payload, reviewed)
        return StorageSemanticSecondPassOutcome(
            initial_result=initial,
            final_runtime_result=second,
            second_pass_result=second,
            second_pass_triggered=True,
            semantic_handoff_ref=content_ref,
            reviewed_specification=(
                reviewed if field_error is None else None
            ),
            accepted=field_error is None,
            business_error=field_error,
        )


__all__ = [
    "PRIMARY_AGENT_ID",
    "SECOND_PASS_AGENT_ID",
    "StorageSemanticSecondPassCoordinator",
    "StorageSemanticSecondPassOutcome",
]
