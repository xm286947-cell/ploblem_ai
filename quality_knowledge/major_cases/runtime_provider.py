"""Real-provider D01 bridge for the Major Case domain.

Business semantics stay here; HTTP, secret injection, retry and provider-call
budget stay in Unified Runtime.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from runtime.content import (
    AdaptiveLongContentRecoveryExecutor,
    AdaptiveLongContentRecoveryPolicy,
    LongContentRecoveryExecutor,
)
from runtime.contracts import (
    CommittedPartialResult,
    ContentChunk,
    ContentPlan,
    ErrorCategory,
    EvidenceLocator,
    EvidenceReference,
    LongContentPolicy,
    MergeContext,
    MergeResult,
    PartialResultCandidate,
    RuntimeStatus,
    SourceBundle,
    SourceRef,
)
from runtime.providers import OpenAICompatibleProviderAdapter
from runtime.reliability import RuntimeStepError
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


class MajorD01ResultMerger:
    """Major-domain merge semantics over Runtime committed chunk partials.

    Runtime owns chunk execution/persistence. This merger owns only the
    business meaning of combining D01 candidates across chunks.
    """

    def __init__(self, expected_object_ids: list[str]) -> None:
        self.expected_object_ids = list(expected_object_ids)
        self._expected = set(self.expected_object_ids)

    @staticmethod
    def _normalized_content(value: str) -> str:
        return " ".join(str(value or "").split())

    def merge(
        self,
        inputs: list[CommittedPartialResult],
        context: MergeContext,
    ) -> MergeResult:
        partitions = {item.partition_key for item in inputs}
        if (
            context.partition_policy == "ISOLATED"
            and len(partitions) > 1
        ):
            raise ValueError("D01_CROSS_EVENT_MERGE_FORBIDDEN")

        present_ids = {item.partial_id for item in inputs}
        expected_partial_ids = set(context.expected_partial_ids)
        missing_partials = sorted(expected_partial_ids - present_ids)

        grouped: dict[str, list[MajorD01ProviderObject]] = {
            object_id: []
            for object_id in self.expected_object_ids
        }
        for partial in inputs:
            payload = partial.data or []
            if not isinstance(payload, list):
                raise ValueError("D01_PARTIAL_PAYLOAD_MUST_BE_LIST")
            for raw in payload:
                item = MajorD01ProviderObject.model_validate(raw)
                if item.object_id not in self._expected:
                    raise ValueError(
                        "D01_UNEXPECTED_OBJECT_IN_MERGE:"
                        + item.object_id
                    )
                grouped[item.object_id].append(item)

        merged_objects: list[dict[str, Any]] = []
        conflict_object_ids: list[str] = []
        for object_id in self.expected_object_ids:
            candidates = grouped[object_id]
            if not candidates:
                continue

            by_content: dict[str, MajorD01ProviderObject] = {}
            for candidate in candidates:
                normalized = self._normalized_content(
                    candidate.content
                )
                existing = by_content.get(normalized)
                if (
                    existing is None
                    or (candidate.confidence or 0.0)
                    > (existing.confidence or 0.0)
                ):
                    by_content[normalized] = candidate

            unique = list(by_content.values())
            unique.sort(
                key=lambda item: (
                    -(item.confidence or 0.0),
                    self._normalized_content(item.content),
                )
            )
            primary = unique[0]
            conflict = len(unique) > 1
            if conflict:
                conflict_object_ids.append(object_id)

            fragment_ids = list(
                dict.fromkeys(
                    fragment_id
                    for candidate in candidates
                    for fragment_id in candidate.fragment_ids
                )
            )
            explanations = list(
                dict.fromkeys(
                    item.explanation.strip()
                    for item in candidates
                    if item.explanation.strip()
                )
            )
            mechanisms = list(
                dict.fromkeys(
                    item.mechanism.strip()
                    for item in candidates
                    if item.mechanism.strip()
                )
            )
            alternatives = [
                {
                    "content": item.content,
                    "confidence": item.confidence,
                    "fragment_ids": list(item.fragment_ids),
                    "explanation": item.explanation,
                    "mechanism": item.mechanism,
                }
                for item in unique
            ]

            merged_objects.append(
                {
                    "object_id": object_id,
                    "data": {
                        "content": primary.content,
                        "confidence": primary.confidence,
                        "explanation": " | ".join(explanations),
                        "mechanism": " | ".join(mechanisms),
                        "fragment_ids": fragment_ids,
                        "conflict": conflict,
                        "alternatives": alternatives,
                    },
                    "metadata": {
                        "business_domain": "MAJOR_CASE",
                        "merge_rule": "object_id+evidence",
                        "candidate_count": len(candidates),
                        "unique_content_count": len(unique),
                        "conflict": conflict,
                    },
                }
            )

        return MergeResult(
            merge_key=context.merge_key,
            data=merged_objects,
            evidence=[],
            derived_from_partial_ids=[
                item.partial_id for item in inputs
            ],
            complete=not missing_partials,
            missing_partial_ids=missing_partials,
            warnings=(
                [
                    "D01_CONFLICT_REQUIRES_HUMAN_REVIEW:"
                    + object_id
                    for object_id in conflict_object_ids
                ]
                + (
                    ["MISSING_COMMITTED_PARTIAL"]
                    if missing_partials
                    else []
                )
            ),
            metadata={
                "business_domain": "MAJOR_CASE",
                "expected_object_ids": self.expected_object_ids,
                "conflict_object_ids": conflict_object_ids,
                "partition_policy": context.partition_policy,
            },
        )


class MajorD01RuntimeService:
    """Execute D01 through Runtime adaptive long-content orchestration.

    Major owns SourceBundle projection, D01 schema/evidence semantics and the
    business merge. Runtime owns provider execution, retry/budget, chunk
    planning, partial persistence, crash/restart, adaptive truncation recovery,
    coverage and merge orchestration.
    """

    def __init__(
        self,
        repository,
        runtime,
        store: SqliteTaskStore,
        *,
        agent_config_path: str | Path,
        max_provider_calls: int = 12,
        initial_max_units_per_chunk: int = 8,
        initial_max_payload_chars: int = 24000,
    ):
        self.repository = repository
        self.runtime = runtime
        self.store = store
        self.domain = MajorCaseRuntimeDomainAdapter(repository)
        self.agent_config_path = Path(agent_config_path)
        self.max_provider_calls = max(1, int(max_provider_calls))
        self.initial_policy = LongContentPolicy(
            max_units_per_chunk=max(
                1,
                int(initial_max_units_per_chunk),
            ),
            max_payload_chars=max(
                1,
                int(initial_max_payload_chars),
            ),
            overlap_units=0,
            metadata={
                "business_domain": "MAJOR_CASE",
                "strategy": "major_issue_d01@2",
            },
        )

        self.expected_specs = self.domain.expected_objects()
        self.expected_object_ids = [
            item.object_id for item in self.expected_specs
        ]
        self._expected_object_id_set = set(
            self.expected_object_ids
        )

        self.long_content = LongContentRecoveryExecutor(
            runtime,
            store,
            chunk_payload_builder=self._chunk_payload,
            partial_candidate_builder=self._partial_candidate,
            merger=MajorD01ResultMerger(
                self.expected_object_ids
            ),
            final_validator=self._validate_merged_output,
            business_gate=self._extraction_gate,
        )
        resolved = self.long_content.bind_configured_agent(
            self.agent_config_path
        )
        self.model_name = resolved.definition.model or ""

        generic_provider = self.long_content.provider_handler
        if generic_provider is None:
            raise RuntimeError("D01_RUNTIME_PROVIDER_NOT_BOUND")
        self._generic_provider = generic_provider
        self.long_content.provider_handler = (
            self._validated_chunk_provider
        )

        self.adaptive = AdaptiveLongContentRecoveryExecutor(
            self.long_content,
            recovery_policy=AdaptiveLongContentRecoveryPolicy(
                max_replans=4,
                max_provider_calls=self.max_provider_calls,
                shrink_factor=0.5,
                min_units_per_chunk=1,
                min_payload_chars=512,
            ),
        )

    def _chunk_payload(
        self,
        bundle: SourceBundle,
        plan: ContentPlan,
        chunk: ContentChunk,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        units = {
            item.unit_id: item
            for item in bundle.logical_units
        }
        ordered_ids = list(chunk.unit_ids) + [
            item
            for item in chunk.overlap_unit_ids
            if item not in chunk.unit_ids
        ]
        fragments: list[dict[str, Any]] = []
        for unit_id in ordered_ids:
            unit = units.get(unit_id)
            if unit is None:
                continue
            payload = dict(unit.inline_payload or {})
            fragments.append(
                {
                    "fragment_id": str(
                        payload.get("fragment_id")
                        or unit.unit_id
                    ),
                    "section_path": str(
                        payload.get("section_path")
                        or unit.locator.get("section_path")
                        or ""
                    ),
                    "location_ref": str(
                        payload.get("location_ref")
                        or unit.locator.get("location_ref")
                        or ""
                    ),
                    "text_content": str(
                        payload.get("text_content") or ""
                    ),
                }
            )

        source = bundle.sources[0].source
        return {
            "source": source.model_dump(mode="json"),
            "pending_objects": [
                item.model_dump(mode="json")
                for item in self.expected_specs
            ],
            "fragments": fragments,
            "chunk": {
                "chunk_id": chunk.chunk_id,
                "unit_ids": list(chunk.unit_ids),
                "overlap_unit_ids": list(
                    chunk.overlap_unit_ids
                ),
                "partition_key": chunk.partition_key,
                "plan_id": plan.plan_id,
            },
            "rules": {
                "only_pending_object_ids": True,
                "fragment_ids_must_exist": True,
                "evidence_required_per_object": True,
                "omit_unsupported_objects": True,
            },
        }

    def _validated_chunk_provider(
        self,
        payload: Any,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw_items = self._generic_provider(payload, context)
        if not isinstance(raw_items, list):
            raise RuntimeStepError(
                "D01 provider result must be a JSON array",
                code="D01_PROVIDER_RESULT_NOT_ARRAY",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )

        allowed_fragment_ids = {
            str(item.get("fragment_id") or "")
            for item in (
                dict(payload or {}).get("fragments") or []
            )
            if str(item.get("fragment_id") or "")
        }
        result: list[dict[str, Any]] = []
        for raw in raw_items:
            item = MajorD01ProviderObject.model_validate(raw)
            if item.object_id not in self._expected_object_id_set:
                raise RuntimeStepError(
                    "provider returned unrequested D01 object",
                    code="D01_UNBOUND_OBJECT",
                    category=ErrorCategory.VALIDATION,
                    retryable=False,
                    details={"object_id": item.object_id},
                )
            unknown = [
                fragment_id
                for fragment_id in item.fragment_ids
                if fragment_id not in allowed_fragment_ids
            ]
            if unknown:
                raise RuntimeStepError(
                    "provider referenced fragment outside current chunk",
                    code="D01_EVIDENCE_FRAGMENT_UNKNOWN",
                    category=ErrorCategory.VALIDATION,
                    retryable=True,
                    details={
                        "object_id": item.object_id,
                        "fragment_ids": unknown,
                    },
                )
            result.append(item.model_dump(mode="json"))
        return result

    @staticmethod
    def _partial_candidate(
        chunk: ContentChunk,
        data: Any,
        execution_key: str,
        step_id: str,
    ) -> PartialResultCandidate:
        return PartialResultCandidate(
            chunk_id=chunk.chunk_id,
            execution_key=execution_key,
            unit_ids=list(chunk.unit_ids),
            data=data,
            partition_key=chunk.partition_key,
            schema_valid=True,
            complete_object=True,
            finish_reason="stop",
            evidence_ids=[],
            metadata={
                "step_id": step_id,
                "business_domain": "MAJOR_CASE",
                "strategy": "major_issue_d01@2",
                "overlap_unit_ids": list(
                    chunk.overlap_unit_ids
                ),
            },
        )

    def _validate_merged_output(self, data: Any) -> bool:
        if not isinstance(data, list):
            return False
        seen: set[str] = set()
        for raw in data:
            if not isinstance(raw, dict):
                return False
            object_id = str(raw.get("object_id") or "")
            payload = raw.get("data")
            if (
                object_id not in self._expected_object_id_set
                or not isinstance(payload, dict)
            ):
                return False
            if not str(payload.get("content") or "").strip():
                return False
            fragment_ids = payload.get("fragment_ids") or []
            if not isinstance(fragment_ids, list) or not fragment_ids:
                return False
            seen.add(object_id)
        return bool(seen)

    def _extraction_gate(
        self,
        merge: MergeResult | None,
        coverages,
        bundle: SourceBundle,
        plan: ContentPlan,
    ) -> bool:
        if merge is None or not merge.complete:
            return False
        data = merge.data
        if not self._validate_merged_output(data):
            return False
        present = {
            str(item.get("object_id") or "")
            for item in data
            if isinstance(item, dict)
        }
        return self._expected_object_id_set.issubset(present)

    def _persist_outcome(
        self,
        *,
        case_id: str,
        event_id: str,
        version_id: str,
        outcome,
    ) -> list[str]:
        if (
            outcome.status != RuntimeStatus.COMPLETED
            or outcome.merge is None
            or not outcome.gate.passed
        ):
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
        for obj in outcome.merge.data or []:
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
                        "locator": str(
                            fragment.get("location_ref") or ""
                        ),
                        "excerpt": str(
                            fragment.get("text_content") or ""
                        )[:500],
                    }
                )

            metadata = dict(obj.get("metadata") or {})
            metadata.update(
                {
                    "runtime_task_ids": list(outcome.task_ids),
                    "runtime_plan_ids": list(outcome.plan_ids),
                    "runtime_provider_calls": (
                        outcome.provider_calls
                    ),
                    "runtime_replans": outcome.replans,
                    "runtime_truncated_task_ids": list(
                        outcome.truncated_task_ids
                    ),
                    "source_version_id": version_id,
                    "merge_conflict": bool(
                        data.get("conflict")
                        or metadata.get("conflict")
                    ),
                    "alternatives": list(
                        data.get("alternatives") or []
                    ),
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
                explanation=str(
                    data.get("explanation") or ""
                ),
                mechanism=str(data.get("mechanism") or ""),
                analysis_metadata=metadata,
            )
            entry_ids.append(entry["entry_id"])
        return entry_ids

    def _compat_outcome(self, outcome) -> dict[str, Any]:
        payload = outcome.model_dump(mode="json")
        committed_objects = (
            list(outcome.merge.data or [])
            if outcome.merge is not None
            else []
        )
        payload["committed_objects"] = committed_objects
        payload["task_id"] = (
            outcome.task_ids[-1]
            if outcome.task_ids
            else ""
        )
        payload["run_id"] = ""
        if outcome.task_ids:
            snapshot = self.runtime.get_task(
                outcome.task_ids[-1]
            )
            payload["run_id"] = (
                snapshot.current_run_id or ""
            )
        return payload

    def execute(
        self,
        *,
        case_id: str,
        version_id: str,
        event_id: str,
        skill_version_id: str = "",
    ) -> dict[str, Any]:
        source = self.domain.source_ref(
            case_id,
            version_id,
            event_id,
        )
        bundle = self.domain.build_source_bundle(
            case_id,
            version_id,
            event_id,
        )
        request_id = self.domain.request_id(
            case_id=case_id,
            version_id=version_id,
            event_id=event_id,
            source_fingerprint=source.fingerprint,
            skill_version_id=skill_version_id,
        )
        outcome = self.adaptive.execute(
            bundle,
            recovery_request_id=request_id,
            initial_policy=self.initial_policy,
            strategy_ref="major_issue_d01@2",
            metadata={
                "business_domain": "MAJOR_CASE",
                "case_id": case_id,
                "version_id": version_id,
                "event_id": event_id,
                "skill_version_id": skill_version_id,
            },
        )
        persisted_entry_ids = self._persist_outcome(
            case_id=case_id,
            event_id=event_id,
            version_id=version_id,
            outcome=outcome,
        )
        business_gate = self.domain.business_gate(case_id)
        return {
            "outcome": self._compat_outcome(outcome),
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
