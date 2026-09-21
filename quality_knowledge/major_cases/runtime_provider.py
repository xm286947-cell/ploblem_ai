"""Real-provider D01 bridge for the Major Case domain.

Business semantics stay here; HTTP, secret injection, retry and provider-call
budget stay in Unified Runtime.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from runtime.adapters import MajorIssueD01RuntimeAdapter
from runtime.contracts import EvidenceLocator, EvidenceReference, RuntimeStatus, SourceRef
from runtime.providers import OpenAICompatibleProviderAdapter
from runtime.store import SqliteTaskStore

from .runtime_integration import MajorCaseRuntimeDomainAdapter


class MajorD01ProviderObject(BaseModel):
    object_id: str
    content: str
    fragment_ids: list[str] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    explanation: str = ""
    mechanism: str = ""


def _evidence_id(
    *,
    source_fingerprint: str,
    partition_key: str,
    object_id: str,
    fragment_id: str,
) -> str:
    payload = "|".join(
        (source_fingerprint, partition_key, object_id, fragment_id)
    )
    return "evidence-major-d01-" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:24]


class MajorD01ProviderBridge:
    """Map Major D01 business objects to the generic Runtime provider adapter."""

    def __init__(self, provider: OpenAICompatibleProviderAdapter):
        self.provider = provider

    def __call__(
        self,
        provider_input: dict[str, Any],
        pending_specs: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        source = SourceRef.model_validate(provider_input["source"])
        fragments = {
            str(item["fragment_id"]): dict(item)
            for item in provider_input.get("fragments") or []
        }
        partition_key = str(context["d01"]["partition_key"])

        model_payload = {
            "business_domain": "MAJOR_CASE",
            "pending_objects": [
                {
                    "object_id": item["object_id"],
                    "locator": item.get("locator") or {},
                    "metadata": item.get("metadata") or {},
                }
                for item in pending_specs
            ],
            "fragments": [
                {
                    "fragment_id": item["fragment_id"],
                    "section_path": item.get("section_path") or "",
                    "location_ref": item.get("location_ref") or "",
                    "text_content": item.get("text_content") or "",
                }
                for item in fragments.values()
            ],
            "rules": {
                "only_pending_object_ids": True,
                "fragment_ids_must_exist": True,
                "evidence_required_per_object": True,
            },
        }
        raw_items = self.provider(model_payload, context)
        results: list[dict[str, Any]] = []

        expected_ids = {str(item["object_id"]) for item in pending_specs}
        for raw in raw_items:
            item = MajorD01ProviderObject.model_validate(raw)
            if item.object_id not in expected_ids:
                # The D01 adapter also guards this. Fail here before evidence
                # construction so the error remains a validation failure.
                from runtime.reliability import RuntimeStepError
                from runtime.contracts import ErrorCategory
                raise RuntimeStepError(
                    "provider returned unrequested D01 object",
                    code="D01_UNBOUND_OBJECT",
                    category=ErrorCategory.VALIDATION,
                    retryable=False,
                    details={"object_id": item.object_id},
                )

            evidence: list[EvidenceReference] = []
            for fragment_id in item.fragment_ids:
                fragment = fragments.get(fragment_id)
                if fragment is None:
                    from runtime.reliability import RuntimeStepError
                    from runtime.contracts import ErrorCategory
                    raise RuntimeStepError(
                        "provider referenced unknown fragment",
                        code="D01_EVIDENCE_FRAGMENT_UNKNOWN",
                        category=ErrorCategory.VALIDATION,
                        retryable=True,
                        details={
                            "object_id": item.object_id,
                            "fragment_id": fragment_id,
                        },
                    )
                evidence.append(
                    EvidenceReference(
                        evidence_id=_evidence_id(
                            source_fingerprint=source.fingerprint,
                            partition_key=partition_key,
                            object_id=item.object_id,
                            fragment_id=fragment_id,
                        ),
                        source=source,
                        locator=EvidenceLocator(
                            type="SECTION",
                            value={
                                "fragment_id": fragment_id,
                                "section_path": fragment.get("section_path") or "",
                                "location_ref": fragment.get("location_ref") or "",
                            },
                        ),
                        excerpt=str(fragment.get("text_content") or "")[:500],
                        confidence=item.confidence,
                        partition_key=partition_key,
                        metadata={"business_domain": "MAJOR_CASE"},
                    )
                )

            results.append(
                {
                    "object_id": item.object_id,
                    "data": {
                        "content": item.content,
                        "confidence": item.confidence,
                        "explanation": item.explanation,
                        "mechanism": item.mechanism,
                        "fragment_ids": list(item.fragment_ids),
                    },
                    "schema_valid": True,
                    "complete_object": True,
                    "finish_reason": "stop",
                    "evidence": [
                        evidence_item.model_dump(mode="json")
                        for evidence_item in evidence
                    ],
                    "metadata": {
                        "business_domain": "MAJOR_CASE",
                        "provider_contract": "major-d01-provider-v1",
                    },
                }
            )
        return results


class MajorD01RuntimeService:
    """Execute D01 through ConfiguredAgentRuntime and persist review candidates."""

    def __init__(
        self,
        repository,
        runtime,
        store: SqliteTaskStore,
        *,
        agent_config_path: str | Path,
        max_provider_calls: int = 4,
    ):
        self.repository = repository
        self.runtime = runtime
        self.store = store
        self.domain = MajorCaseRuntimeDomainAdapter(repository)
        self.agent_config_path = Path(agent_config_path)

        resolved = runtime.config_loader.load(self.agent_config_path)
        generic_provider = OpenAICompatibleProviderAdapter(
            system_prompt=runtime.config_loader.read_prompt_text(resolved),
            output_schema=runtime.config_loader.get_output_schema(resolved),
            timeout_seconds=resolved.execution_policy.timeout_seconds,
            response_shape=resolved.definition.metadata.get(
                "provider_response_shape"
            ),
        )
        self.model_name = resolved.definition.model or ""
        self.provider_bridge = MajorD01ProviderBridge(generic_provider)
        self.d01 = MajorIssueD01RuntimeAdapter(
            runtime,
            store,
            self.provider_bridge,
            max_provider_calls=max_provider_calls,
            agent_config_path=self.agent_config_path,
        )

    def _provider_input(
        self,
        *,
        version_id: str,
        source: SourceRef,
    ) -> dict[str, Any]:
        fragments = self.repository.fragments(version_id)
        return {
            "source": source.model_dump(mode="json"),
            "fragments": [
                {
                    "fragment_id": item["fragment_id"],
                    "section_path": item.get("section_path") or "",
                    "location_ref": item.get("location_ref") or "",
                    "text_content": item.get("text_content") or "",
                }
                for item in fragments
            ],
        }

    def _persist_outcome(
        self,
        *,
        case_id: str,
        event_id: str,
        version_id: str,
        outcome,
    ) -> list[str]:
        if outcome.status != RuntimeStatus.COMPLETED:
            return []

        fragments = {
            str(item["fragment_id"]): item
            for item in self.repository.fragments(version_id)
        }
        clear_for_event = getattr(
            self.repository,
            "clear_pending_ai_entries_for_event",
            None,
        )
        if callable(clear_for_event):
            clear_for_event(case_id, event_id)

        entry_ids: list[str] = []
        for obj in outcome.committed_objects:
            data = dict(obj.get("data") or {})
            fragment_ids = [
                str(item)
                for item in data.get("fragment_ids") or []
            ]
            evidence = []
            for fragment_id in fragment_ids:
                fragment = fragments.get(fragment_id)
                if fragment is None:
                    continue
                evidence.append(
                    {
                        "fragment_id": fragment_id,
                        "locator": str(fragment.get("location_ref") or ""),
                        "excerpt": str(fragment.get("text_content") or "")[:500],
                    }
                )
            entry = self.repository.add_entry(
                case_id,
                str(obj["object_id"]),
                str(data.get("content") or ""),
                assertion_kind="AI_INFERENCE",
                origin="AI",
                status="PENDING",
                event_id=event_id,
                model_profile=f"runtime:{self.model_name}",
                evidence=evidence,
                confidence=data.get("confidence"),
                explanation=str(data.get("explanation") or ""),
                mechanism=str(data.get("mechanism") or ""),
                analysis_metadata={
                    "runtime_task_id": outcome.task_id,
                    "runtime_run_id": outcome.run_id,
                    "runtime_provider_calls": outcome.provider_calls,
                    "source_version_id": version_id,
                },
            )
            entry_ids.append(entry["entry_id"])
        return entry_ids

    def execute(
        self,
        *,
        case_id: str,
        version_id: str,
        event_id: str,
        skill_version_id: str = "",
    ) -> dict[str, Any]:
        source = self.domain.source_ref(case_id, version_id, event_id)
        request_id = self.domain.request_id(
            case_id=case_id,
            version_id=version_id,
            event_id=event_id,
            source_fingerprint=source.fingerprint,
            skill_version_id=skill_version_id,
        )
        outcome = self.d01.execute_partition(
            case_id=case_id,
            issue_version_id=version_id,
            partition_key=event_id,
            source=source,
            expected_objects=self.domain.expected_objects(),
            provider_input=self._provider_input(
                version_id=version_id,
                source=source,
            ),
            request_id=request_id,
        )
        persisted_entry_ids = self._persist_outcome(
            case_id=case_id,
            event_id=event_id,
            version_id=version_id,
            outcome=outcome,
        )
        business_gate = self.domain.business_gate(case_id)
        return {
            "outcome": outcome.model_dump(mode="json"),
            "persisted_entry_ids": persisted_entry_ids,
            "business_gate": business_gate,
            "business_consumable": bool(
                outcome.business_consumable
                and business_gate["business_consumable"]
            ),
        }


__all__ = [
    "MajorD01ProviderBridge",
    "MajorD01ProviderObject",
    "MajorD01RuntimeService",
]
