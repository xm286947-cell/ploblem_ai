"""Major-case business mapping onto the Unified Agent Runtime contract.

This module owns only MAJOR_CASE business semantics. Runtime execution state,
retry, checkpoint/resume, coverage mechanics and provider call budgets remain
owned by the shared runtime package.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from runtime.adapters.major_issue import (
    MajorIssueD01RuntimeAdapter,
    MajorIssueObjectSpec,
    RepeatCaseRuntimeAdapter,
)
from runtime.contracts import (
    AtomicGroup,
    AtomicGroupPolicy,
    ContentSource,
    LogicalUnit,
    SourceBundle,
    SourceRef,
)

from .skills import MAJOR_REVIEW_SKILL


BUSINESS_DOMAIN = "MAJOR_CASE"
INTEGRATION_CONTRACT_VERSION = "major-runtime-integration-v1.1"
D01_AGENT_ID = MajorIssueD01RuntimeAdapter.AGENT_ID
D01_WORKFLOW_ID = MajorIssueD01RuntimeAdapter.WORKFLOW_ID
REPEAT_CASE_AGENT_ID = RepeatCaseRuntimeAdapter.AGENT_ID


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MajorCaseRuntimeDomainAdapter:
    """Translate major-case business objects into canonical Runtime content.

    The adapter deliberately does not own provider retry, Runtime Task/Run state,
    checkpoint/resume or provider call budgeting.
    """

    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def expected_objects() -> list[MajorIssueObjectSpec]:
        return [
            MajorIssueObjectSpec(
                object_id=spec["entry_type"],
                unit_id=f"entry:{spec['entry_type']}",
                locator={
                    "business_type": spec["entry_type"],
                    "required_section_labels": list(spec["labels"]),
                },
                metadata={
                    "business_domain": BUSINESS_DOMAIN,
                    "skill_code": MAJOR_REVIEW_SKILL["skill_code"],
                },
            )
            for spec in MAJOR_REVIEW_SKILL["required_sections"]
        ]

    def _validated_scope(
        self,
        case_id: str,
        version_id: str,
        event_id: str,
    ) -> tuple[dict, dict, dict]:
        case = self.repository.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        version = self.repository.version(version_id)
        if not version:
            raise KeyError(version_id)
        event = self.repository.event(event_id)
        if not event:
            raise KeyError(event_id)
        if event["case_id"] != case_id:
            raise ValueError("EVENT_CASE_SCOPE_MISMATCH")
        if version.get("group_code") and version["group_code"] != case["group_code"]:
            raise ValueError("DOCUMENT_CASE_GROUP_MISMATCH")
        return case, version, event

    @staticmethod
    def _source_fingerprint(version: dict) -> str:
        return _stable_hash(
            {
                "version_id": version["version_id"],
                "document_id": version.get("document_id"),
                "content_hash": version.get("content_hash"),
                "parser_version": version.get("parser_version"),
                "version_no": version.get("version_no"),
            }
        )

    def source_ref(
        self,
        case_id: str,
        version_id: str,
        event_id: str,
    ) -> SourceRef:
        case, version, event = self._validated_scope(
            case_id,
            version_id,
            event_id,
        )
        return SourceRef(
            source_id=version_id,
            source_type="MAJOR_REVIEW_DOCUMENT",
            revision=str(version.get("version_no") or ""),
            content_hash=str(version.get("content_hash") or ""),
            fingerprint=self._source_fingerprint(version),
            uri=f"knowledge://major-cases/{case_id}/versions/{version_id}",
            metadata={
                "business_domain": BUSINESS_DOMAIN,
                "case_id": case_id,
                "event_id": event_id,
                "group_code": case["group_code"],
                "media_type": version.get("media_type"),
                "parser_version": version.get("parser_version"),
                "standard_itr": event.get("standard_itr") or "",
            },
        )

    def build_source_bundle(
        self,
        case_id: str,
        version_id: str,
        event_id: str,
    ) -> SourceBundle:
        source = self.source_ref(case_id, version_id, event_id)
        fragments = self.repository.fragments(version_id)
        if not fragments:
            raise ValueError("NO_PARSED_FRAGMENTS")

        section_members: dict[str, list[str]] = defaultdict(list)
        for fragment in fragments:
            section = str(fragment.get("section_path") or "").strip()
            if section:
                section_members[section].append(str(fragment["fragment_id"]))

        group_for_unit: dict[str, str] = {}
        atomic_groups: list[AtomicGroup] = []
        for section, unit_ids in sorted(section_members.items()):
            if len(unit_ids) < 2:
                continue
            group_id = "section:" + _stable_hash(
                {
                    "version_id": version_id,
                    "event_id": event_id,
                    "section": section,
                }
            )[:16]
            atomic_groups.append(
                AtomicGroup(
                    group_id=group_id,
                    unit_ids=unit_ids,
                    policy=AtomicGroupPolicy.SAME_CONTEXT,
                    grouping_hint=section,
                    metadata={
                        "business_domain": BUSINESS_DOMAIN,
                        "reason": "fragments share one business section",
                    },
                )
            )
            for unit_id in unit_ids:
                group_for_unit[unit_id] = group_id

        logical_units: list[LogicalUnit] = []
        for fragment in fragments:
            fragment_id = str(fragment["fragment_id"])
            logical_units.append(
                LogicalUnit(
                    unit_id=fragment_id,
                    source_id=source.source_id,
                    locator={
                        "location_type": fragment.get("location_type"),
                        "location_ref": fragment.get("location_ref"),
                        "section_path": fragment.get("section_path"),
                        "ordinal": fragment.get("ordinal"),
                    },
                    inline_payload={
                        "fragment_id": fragment_id,
                        "section_path": fragment.get("section_path"),
                        "location_ref": fragment.get("location_ref"),
                        "text_content": fragment.get("text_content") or "",
                    },
                    group_id=group_for_unit.get(fragment_id),
                    context_refs=[f"case:{case_id}", f"event:{event_id}"],
                    partition_key=event_id,
                    metadata={
                        "business_domain": BUSINESS_DOMAIN,
                        "fragment_type": fragment.get("fragment_type"),
                        "text_hash": fragment.get("text_hash"),
                    },
                )
            )

        return SourceBundle(
            bundle_id=(
                f"major:{case_id}:{version_id}:{event_id}:"
                f"{source.fingerprint[:12]}"
            ),
            sources=[
                ContentSource(
                    source=source,
                    content_ref=source.uri,
                    partition_key=event_id,
                    metadata={
                        "business_domain": BUSINESS_DOMAIN,
                        "case_id": case_id,
                        "event_id": event_id,
                    },
                )
            ],
            logical_units=logical_units,
            atomic_groups=atomic_groups,
            shared_context={
                "business_domain": BUSINESS_DOMAIN,
                "case_id": case_id,
                "event_id": event_id,
                "skill_code": MAJOR_REVIEW_SKILL["skill_code"],
                "required_entry_types": [
                    item["entry_type"]
                    for item in MAJOR_REVIEW_SKILL["required_sections"]
                ],
                "business_merge": {
                    "key": "entry_type",
                    "dedup": "same entry_type + evidence identity",
                    "conflict": "preserve conflicting evidence for human review",
                },
            },
            default_partition_key=event_id,
            metadata={
                "contract_version": INTEGRATION_CONTRACT_VERSION,
                "partition_policy": "EVENT_ISOLATED",
            },
        )

    @staticmethod
    def request_id(
        *,
        case_id: str,
        version_id: str,
        event_id: str,
        source_fingerprint: str,
        skill_version_id: str = "",
    ) -> str:
        identity = _stable_hash(
            {
                "contract_version": INTEGRATION_CONTRACT_VERSION,
                "case_id": case_id,
                "version_id": version_id,
                "event_id": event_id,
                "source_fingerprint": source_fingerprint,
                "skill_version_id": skill_version_id,
            }
        )[:16]
        return f"major-d01:{case_id}:{version_id}:{event_id}:{identity}"

    def business_gate(self, case_id: str) -> dict[str, Any]:
        entries = self.repository.entries(case_id)
        unresolved = [
            item["entry_id"]
            for item in entries
            if item.get("status") in {"PENDING", "MISSING"}
        ]
        unscoped = [
            item["entry_id"]
            for item in self.repository.unscoped_confirmed_entries(case_id)
        ]
        reasons: list[str] = []
        if not entries:
            reasons.append("NO_KNOWLEDGE_ENTRY")
        if unresolved:
            reasons.append("HUMAN_REVIEW_INCOMPLETE")
        if unscoped:
            reasons.append("UNSCOPED_EVENT_KNOWLEDGE")
        return {
            "passed": not reasons,
            "business_consumable": not reasons,
            "reasons": reasons,
            "pending_entry_ids": unresolved,
            "unscoped_entry_ids": unscoped,
        }

    @staticmethod
    def contract_snapshot() -> dict[str, Any]:
        return {
            "contract_version": INTEGRATION_CONTRACT_VERSION,
            "business_domain": BUSINESS_DOMAIN,
            "d01": {
                "agent_id": D01_AGENT_ID,
                "workflow_id": D01_WORKFLOW_ID,
                "input": (
                    "case_id + version_id + event_id + SourceBundle + "
                    "MajorIssueObjectSpec[]"
                ),
                "output": "MajorIssueD01Outcome",
            },
            "repeat_case": {
                "agent_id": REPEAT_CASE_AGENT_ID,
                "payload_semantics": "opaque to Runtime",
            },
            "request_id": "stable hash of case/version/event/source/skill contract",
            "partition": "event_id",
            "retry_owner": "Unified Agent Runtime",
            "provider_boundary": {
                "actual_requests_per_attempt": 1,
                "adapter_retry": 0,
                "sdk_retry": 0,
                "analyzer_retry": 0,
            },
            "long_content": {
                "source": "document version SourceRef",
                "logical_unit": "kb_fragment",
                "atomic_group": "same section => SAME_CONTEXT",
                "coverage_universe": "all fragments in one document version/event",
                "business_merge": (
                    "entry_type; preserve evidence and explicit conflicts"
                ),
            },
            "business_gate": (
                "no PENDING/MISSING entries and no confirmed UNSCOPED "
                "knowledge in multi-event cases"
            ),
            "legacy_execution_state": "projection_only_after_switch",
            "agent_config": (
                "Canonical Direct on P0.3; PR #9 is optional fixed-head "
                "integration until merged to main"
            ),
        }
