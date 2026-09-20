from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import TypeAdapter, ValidationError

from runtime import (
    AgentDefinition,
    AgentRequest,
    ErrorCategory,
    ExecutionPolicy,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
)
from runtime.adapters import StorageFieldResult, StorageGoldenFieldComparator
from runtime.engine import LightweightExecutionEngine


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    finish_reason: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class E2ERunResult:
    runtime_result: Any
    reviewed_specification: list[StorageFieldResult] | None
    golden_report: Any | None


ProviderCall = Callable[[dict[str, Any], dict[str, Any]], ProviderResponse]


class JsonTruncationAwareStorageAdapter:
    """Storage Domain Adapter for E2E-01.

    Business owns schema/golden semantics. Runtime owns validation retry,
    provider-call budget, state, checkpoint and final commit.
    """

    AGENT_ID = "storage.emmc.parameter_extract.e2e01"

    def __init__(
        self,
        runtime: LightweightExecutionEngine,
        provider_call: ProviderCall,
    ) -> None:
        self.runtime = runtime
        self.provider_call = provider_call
        self._field_list = TypeAdapter(list[StorageFieldResult])
        self._golden = StorageGoldenFieldComparator()

        self.runtime.register_agent(
            self.AGENT_ID,
            self._handler,
            AgentDefinition(
                agent_id=self.AGENT_ID,
                label="Storage eMMC Parameter Extraction E2E-01",
                output_schema="list[StorageFieldResult]",
                metadata={
                    "business_domain": "STORAGE",
                    "device_type": "eMMC",
                    "e2e_case": "JSON_TRUNCATION",
                    "what_how_boundary": "DOMAIN_SCHEMA_RUNTIME_EXECUTION",
                },
            ),
        )

    @staticmethod
    def _default_policy(max_provider_calls: int = 3) -> ExecutionPolicy:
        return ExecutionPolicy(
            validation_retry=RetryPolicy(max_attempts=max_provider_calls),
            transport_retry=RetryPolicy(max_attempts=1),
            step_retry=RetryPolicy(max_attempts=1),
            retry_budget=RetryBudget(
                max_provider_calls_per_step=max_provider_calls,
                max_step_attempts=1,
                max_validation_cycles_per_step_attempt=max_provider_calls,
                max_transport_attempts_per_model_call=1,
            ),
            model_policy={
                "runtime_retry_owner": True,
                "sdk_retry": 0,
            },
        )

    def _handler(self, payload: dict[str, Any], context: dict[str, Any]) -> Any:
        response = self.provider_call(payload, context)

        if (response.finish_reason or "").lower() == "length":
            raise RuntimeStepError(
                "provider output was truncated (finish_reason=length)",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={
                    "finish_reason": response.finish_reason,
                    "provider_call_seq": context.get("runtime", {}).get(
                        "provider_call_seq"
                    ),
                },
            )

        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise RuntimeStepError(
                f"provider output is not complete JSON: {exc}",
                code="INVALID_JSON",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={
                    "provider_call_seq": context.get("runtime", {}).get(
                        "provider_call_seq"
                    ),
                },
            ) from exc

        try:
            fields = self._field_list.validate_python(parsed)
        except ValidationError as exc:
            raise RuntimeStepError(
                f"provider JSON failed Storage output schema: {exc}",
                code="STORAGE_SCHEMA_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={
                    "provider_call_seq": context.get("runtime", {}).get(
                        "provider_call_seq"
                    ),
                },
            ) from exc

        return [field.model_dump(mode="json") for field in fields]

    def execute(
        self,
        payload: dict[str, Any],
        *,
        request_id: str,
        golden: list[StorageFieldResult | dict[str, Any]] | None = None,
        max_provider_calls: int = 3,
        accepted_differences: dict[str, str] | None = None,
    ) -> E2ERunResult:
        result = self.runtime.invoke(
            AgentRequest(
                request_id=request_id,
                agent_id=self.AGENT_ID,
                input=payload,
                execution_policy=self._default_policy(max_provider_calls),
                metadata={
                    "business_domain": "STORAGE",
                    "device_type": "eMMC",
                    "e2e_case": "JSON_TRUNCATION",
                },
            )
        )

        if result.status != RuntimeStatus.COMPLETED:
            return E2ERunResult(
                runtime_result=result,
                reviewed_specification=None,
                golden_report=None,
            )

        reviewed = [StorageFieldResult.model_validate(item) for item in result.data]
        golden_report = None
        if golden is not None:
            golden_report = self._golden.compare(
                golden,
                reviewed,
                accepted_differences=accepted_differences,
            )

        return E2ERunResult(
            runtime_result=result,
            reviewed_specification=reviewed,
            golden_report=golden_report,
        )
