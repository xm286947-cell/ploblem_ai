"""Production orchestration for the Major Source to Historical Case flow.

This service deliberately composes the existing Major repository, D01 runtime
adapter and publisher.  It does not create a second persistence model or give
callers a shortcut around human review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from runtime import EvidenceLocator, EvidenceReference, LightweightExecutionEngine, SourceRef, SqliteTaskStore
from runtime.adapters import MajorIssueD01RuntimeAdapter, MajorIssueObjectSpec
from quality_knowledge.major_cases.document_parser import parse_document
from quality_knowledge.major_cases.identity import MajorCaseIdentityConflict, MajorCaseIdentityResolver
from quality_knowledge.major_cases.semantic_source_adapter import MajorSemanticSourceAdapter
from quality_knowledge.problem_refs import InvalidSourceProblemItrRef, SourceProblemItrRefV1
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from repositories import JsonArtifactRepository
from services.major_case_publisher import MajorCasePublisher, PublishCommitError
from services.major_case_publish import PublishValidationError


ENTRY_TYPES = ("ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION")


class MajorProductionError(RuntimeError):
    """Stable error used by the Web entry point."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MajorCaseProductionService:
    """The only Web-facing path from a new source to a formal publication."""

    def __init__(
        self,
        repository: MajorKnowledgeRepository,
        artifact_repository: JsonArtifactRepository,
        runtime_db_path: str | Path,
        provider: Any | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.identity_resolver = MajorCaseIdentityResolver(repository)
        self.artifact_repository = artifact_repository
        self.publisher = MajorCasePublisher(repository, artifact_repository)
        self.provider = provider
        self.semantic_adapter = MajorSemanticSourceAdapter(
            repository,
            project_root or Path(__file__).resolve().parents[1],
        )
        self.store = SqliteTaskStore(runtime_db_path)
        self.runtime = LightweightExecutionEngine(self.store)

    def intake_source(
        self,
        *,
        title: str,
        group_code: str,
        domain: str,
        standard_itr: str,
        source_name: str,
        source_bytes: bytes,
    ) -> dict[str, Any]:
        if not all(value.strip() for value in (title, group_code, standard_itr, source_name)):
            raise MajorProductionError("MAJOR_SOURCE_IDENTITY_REQUIRED")
        try:
            public_ref = SourceProblemItrRefV1.from_input(standard_itr)
        except InvalidSourceProblemItrRef as error:
            raise MajorProductionError("INVALID_REF") from error
        standard_itr = public_ref.public_ref
        if not source_bytes:
            raise MajorProductionError("MAJOR_SOURCE_EMPTY")
        suffix = Path(source_name).suffix.lower()
        if suffix not in {".pdf", ".docx", ".doc"}:
            raise MajorProductionError("MAJOR_SOURCE_TYPE_UNSUPPORTED")

        try:
            resolution = self.identity_resolver.resolve_or_create_case(
                group_code=group_code,
                title=title,
                domain=domain,
                source_key=f"ITR:{standard_itr}",
                standard_itrs=[standard_itr],
            )
            case = resolution["case"]
            event = resolution["events"][0]
        except MajorCaseIdentityConflict as error:
            raise MajorProductionError(error.code) from error
        self.repository.update_case_status(case["case_id"], "ACTIVE")
        with TemporaryDirectory(prefix="major-source-") as directory:
            source_path = Path(directory) / Path(source_name).name
            source_path.write_bytes(source_bytes)
            document = self.repository.ingest_file(case["case_id"], source_path)
            parsed = parse_document(self.repository.attachment_path(document["version_id"]))
            fragments = self.repository.save_parse_result(document["version_id"], parsed)

        source_link = self.repository.add_source_link(
            case["case_id"],
            event["event_id"],
            {
                "record_id": document["version_id"],
                "source_type": "MAJOR_SOURCE_DOCUMENT",
                "source_system": "MAJOR_SOURCE_INTAKE",
                "group_code": group_code,
                "source_hash": document["content_hash"],
                "file_name": source_name,
                "version_id": document["version_id"],
                "document_id": document["document_id"],
                "fragment_count": len(fragments),
            },
            standard_itr=standard_itr,
            role="CURRENT_EVENT",
            status="LINKED",
        )
        return {
            "case": case,
            "event": event,
            "document": document,
            "source_link": source_link,
            "parse": {"fragment_count": len(fragments), "warnings": parsed.warnings},
        }

    def analyze(self, case_id: str, event_id: str | None = None) -> dict[str, Any]:
        detail = self.repository.case_detail(case_id)
        if not detail:
            raise MajorProductionError("MAJOR_CASE_NOT_FOUND")
        event = self._resolve_analysis_event(case_id, event_id)
        draft = self.semantic_adapter.build_draft(case_id, event["event_id"])
        has_typed_source = bool(draft.get("source_fact_revision_id")) or any(
            slot.get("items") for slot in draft.get("semantic_slots", {}).values()
        )
        if has_typed_source:
            return self._analyze_source_fusion(case_id, draft, event)
        return self._analyze_legacy(case_id, detail, event)

    def _resolve_analysis_event(self, case_id: str, event_id: str | None) -> dict[str, Any]:
        events = self.repository.events(case_id)
        if event_id:
            event = self.repository.event(str(event_id))
            if not event or event.get("case_id") != case_id:
                raise MajorProductionError("MAJOR_ANALYSIS_EVENT_INVALID")
            return event
        if len(events) == 1:
            return events[0]
        if not events:
            raise MajorProductionError("MAJOR_ANALYSIS_EVENT_REQUIRED")
        raise MajorProductionError("MAJOR_ANALYSIS_EVENT_SELECTION_REQUIRED")

    def _analyze_source_fusion(
        self,
        case_id: str,
        draft: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        source_links = self.repository.source_links(case_id)
        links_by_id = {str(link.get("source_link_id")): link for link in source_links}
        candidates: list[dict[str, Any]] = []
        for entry_type, slot in draft["semantic_slots"].items():
            slot_items = list(slot.get("items") or [])
            if not slot_items:
                candidates.append({
                    "entry_type": entry_type,
                    "content": "",
                    "status": "MISSING",
                    "evidence": [],
                    "analysis_metadata": self._semantic_metadata(draft, slot),
                    "explanation": "SOURCE_SEMANTIC_MISSING",
                })
                continue

            if entry_type.endswith("_ACTION"):
                for index, source_item in enumerate(slot_items, start=1):
                    evidence = self._semantic_evidence(source_item, links_by_id, event["event_id"])
                    candidates.append({
                        "entry_type": entry_type,
                        "content": str(source_item.get("value") or "").strip(),
                        "status": "PENDING",
                        "evidence": evidence,
                        "analysis_metadata": {
                            **self._semantic_metadata(draft, slot),
                            "action_item_index": index,
                            "source_values": [self._source_value_metadata(source_item)],
                        },
                        "explanation": "SOURCE_FUSION_ACTION_ITEM",
                    })
                continue

            status = str(slot.get("status") or "AVAILABLE")
            if status in {"MULTI_SOURCE", "CONFLICT"}:
                content = "\n".join(
                    f"[{item.get('source_type') or 'SOURCE'}] {str(item.get('value') or '').strip()}"
                    for item in slot_items
                    if str(item.get("value") or "").strip()
                )
            else:
                content = str(slot.get("effective_value") or slot_items[0].get("value") or "").strip()
            evidence = [
                ref
                for source_item in slot_items
                for ref in self._semantic_evidence(source_item, links_by_id, event["event_id"])
            ]
            candidates.append({
                "entry_type": entry_type,
                "content": content,
                "status": "PENDING",
                "evidence": evidence,
                "analysis_metadata": {
                    **self._semantic_metadata(draft, slot),
                    "source_values": [self._source_value_metadata(item) for item in slot_items],
                },
                "explanation": "SOURCE_FUSION_CAUSE_SLOT",
            })

        for entry_type, slot in draft.get("compatibility_slots", {}).items():
            slot_items = list(slot.get("items") or [])
            metadata = self._semantic_metadata(draft, slot)
            if not slot_items:
                candidates.append({
                    "entry_type": entry_type,
                    "content": "",
                    "status": "MISSING",
                    "evidence": [],
                    "analysis_metadata": metadata,
                    "explanation": "SOURCE_COMPATIBILITY_SEMANTIC_MISSING",
                })
                continue
            if entry_type == "VERIFICATION":
                for index, source_item in enumerate(slot_items, start=1):
                    candidates.append({
                        "entry_type": entry_type,
                        "content": str(source_item.get("value") or "").strip(),
                        "status": "PENDING",
                        "evidence": self._semantic_evidence(source_item, links_by_id, event["event_id"]),
                        "analysis_metadata": {
                            **metadata,
                            "verification_item_index": index,
                            "source_values": [self._source_value_metadata(source_item)],
                        },
                        "explanation": "SOURCE_FUSION_VERIFICATION",
                    })
                continue
            status = str(slot.get("status") or "AVAILABLE")
            if status in {"MULTI_SOURCE", "CONFLICT"}:
                content = "\n".join(
                    f"[{item.get('source_type') or 'SOURCE'}] {str(item.get('value') or '').strip()}"
                    for item in slot_items
                    if str(item.get("value") or "").strip()
                )
            else:
                content = str(slot.get("effective_value") or slot_items[0].get("value") or "").strip()
            evidence = [
                ref
                for source_item in slot_items
                for ref in self._semantic_evidence(source_item, links_by_id, event["event_id"])
            ]
            candidates.append({
                "entry_type": entry_type,
                "content": content,
                "status": "PENDING",
                "evidence": evidence,
                "analysis_metadata": {
                    **metadata,
                    "source_values": [self._source_value_metadata(item) for item in slot_items],
                },
                "explanation": "SOURCE_FUSION_ISSUE_FACT",
            })

        created = self.repository.replace_pending_source_fusion_entries(
            case_id, event["event_id"], candidates
        )
        standardization = self._standardize_source_candidates(
            case_id, event["event_id"], created, draft
        )
        created = [self.repository.entry(item["entry_id"]) or item for item in created]
        return {
            "mode": "SOURCE_FUSION",
            "case_id": case_id,
            "event_id": event["event_id"],
            "candidates": created,
            "standardization": standardization,
        }

    def _standardize_source_candidates(
        self,
        case_id: str,
        event_id: str,
        candidates: list[dict[str, Any]],
        draft: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_types = {"TRC_OCCURRENCE", "TRC_ESCAPE", "MRC_OCCURRENCE", "MRC_ESCAPE"}
        eligible = [
            item for item in candidates
            if item.get("origin") == "SOURCE_FUSION"
            and item.get("entry_type") in allowed_types
            and item.get("status") == "PENDING"
            and item.get("analysis_metadata", {}).get("source_status") == "AVAILABLE"
            and item.get("analysis_metadata", {}).get("review_status") == "NOT_REQUIRED"
            and item.get("evidence")
        ]
        if not eligible:
            return {"status": "SKIPPED_NO_UNAMBIGUOUS_EVIDENCE", "candidate_count": 0}
        if self.provider is None:
            return {"status": "SKIPPED_PROVIDER_NOT_CONFIGURED", "candidate_count": len(eligible)}

        identity = {
            "case_id": case_id,
            "event_id": event_id,
            "source_fact_revision_id": draft.get("source_fact_revision_id"),
            "document_version_ids": list(draft.get("document_version_ids") or []),
            "entries": [
                {
                    "entry_id": item["entry_id"],
                    "revision_id": item["current_revision_id"],
                    "content": item["content"],
                    "evidence": [
                        {
                            "fragment_id": evidence.get("fragment_id"),
                            "source_link_id": evidence.get("source_link_id"),
                            "locator": evidence.get("locator"),
                            "excerpt": evidence.get("excerpt"),
                        }
                        for evidence in item.get("evidence") or []
                    ],
                }
                for item in eligible
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        source_id = str(draft.get("source_fact_revision_id") or next(
            iter(draft.get("document_version_ids") or []), f"{case_id}:{event_id}"
        ))
        source = SourceRef(
            source_id=source_id,
            source_type="MAJOR_SEMANTIC_SOURCE",
            revision=fingerprint[:16],
            content_hash=fingerprint,
            fingerprint=fingerprint,
            uri=f"major-semantic://{case_id}/{event_id}/{fingerprint[:16]}",
            metadata={"business_domain": "MAJOR_CASE", "event_id": event_id},
        )
        specs = [
            MajorIssueObjectSpec(
                object_id=str(item["entry_id"]),
                unit_id=str(item["entry_id"]),
                locator={
                    "entry_type": item["entry_type"],
                    "semantic_slot": item.get("analysis_metadata", {}).get("semantic_slot"),
                    "source_entry_id": item["entry_id"],
                },
                metadata={
                    "business_domain": "MAJOR_CASE",
                    "operation": "EVIDENCE_BOUND_STANDARDIZATION",
                    "source_status": "AVAILABLE",
                },
            )
            for item in eligible
        ]
        provider_input = {
            "operation": "EVIDENCE_BOUND_STANDARDIZATION",
            "case_id": case_id,
            "event_id": event_id,
            "entries": [
                {
                    "entry_id": item["entry_id"],
                    "entry_type": item["entry_type"],
                    "source_content": item["content"],
                    "source_values": item.get("analysis_metadata", {}).get("source_values", []),
                    "evidence": [
                        {
                            "evidence_id": evidence.get("evidence_id"),
                            "fragment_id": evidence.get("fragment_id"),
                            "source_link_id": evidence.get("source_link_id"),
                            "locator": evidence.get("locator"),
                            "excerpt": evidence.get("excerpt"),
                        }
                        for evidence in item.get("evidence") or []
                    ],
                }
                for item in eligible
            ],
            "rules": {
                "standardize_terms_only": True,
                "must_preserve_source_meaning": True,
                "must_not_add_facts_or_causes": True,
                "must_not_complete_missing_values": True,
                "must_not_choose_between_sources": True,
                "output_is_suggestion_only": True,
                "use_only_requested_entry_ids": True,
            },
        }
        request_id = f"major-standardize:{case_id}:{event_id}:{fingerprint[:20]}"
        try:
            adapter = MajorIssueD01RuntimeAdapter(self.runtime, self.store, self.provider)
            outcome = adapter.execute_partition(
                case_id=case_id,
                issue_version_id=source_id,
                partition_key=event_id,
                source=source,
                expected_objects=specs,
                provider_input=provider_input,
                request_id=request_id,
            )
            if not outcome.business_consumable:
                return {
                    "status": "FAILED",
                    "candidate_count": len(eligible),
                    "runtime_task_id": outcome.task_id,
                    "provider_calls": outcome.provider_calls,
                }
            outputs = {str(item["object_id"]): item for item in outcome.committed_objects}
            proposals = []
            for item in eligible:
                output = outputs.get(str(item["entry_id"]), {})
                data = output.get("data")
                content = data.get("content") if isinstance(data, dict) else data
                if not str(content or "").strip():
                    return {
                        "status": "FAILED",
                        "candidate_count": len(eligible),
                        "runtime_task_id": outcome.task_id,
                        "provider_calls": outcome.provider_calls,
                    }
                proposals.append({
                    "entry_id": item["entry_id"],
                    "content": str(content).strip(),
                    "runtime_task_id": outcome.task_id,
                    "provider_calls": outcome.provider_calls,
                })
            self.repository.save_semantic_standardization_proposals(case_id, event_id, proposals)
            return {
                "status": "COMPLETED",
                "candidate_count": len(eligible),
                "runtime_task_id": outcome.task_id,
                "provider_calls": outcome.provider_calls,
            }
        except Exception as error:
            return {
                "status": "FAILED",
                "candidate_count": len(eligible),
                "error_code": str(getattr(error, "code", "UNIFIED_RUNTIME_STANDARDIZATION_FAILED")),
            }

    @staticmethod
    def _source_value_metadata(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "value": str(item.get("value") or ""),
            "source_type": str(item.get("source_type") or ""),
            "evidence_refs": list(item.get("evidence_refs") or []),
        }

    @classmethod
    def _semantic_metadata(cls, draft: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
        return {
            "semantic_slot": slot.get("semantic_slot"),
            "source_status": slot.get("status"),
            "review_status": slot.get("review_status"),
            "source_fact_revision_id": draft.get("source_fact_revision_id"),
            "document_version_ids": list(draft.get("document_version_ids") or []),
            "fusion_version": draft.get("fusion_version"),
            "source_relation_conflicts": list(slot.get("source_relation_conflicts") or []),
        }

    @staticmethod
    def _semantic_evidence(
        source_item: dict[str, Any],
        links_by_id: dict[str, dict[str, Any]],
        event_id: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for reference in source_item.get("evidence_refs") or []:
            link_ids = [str(value) for value in reference.get("source_link_ids") or [] if value]
            matching_links = [links_by_id[item] for item in link_ids if item in links_by_id]
            event_links = [
                link for link in matching_links
                if link.get("event_id") in {event_id, None}
            ]
            source_link_id = str(event_links[0].get("source_link_id")) if event_links else ""
            fragment_id = str(reference.get("fragment_id") or "")
            if source_item.get("source_type") == "EXCEL" and not source_link_id:
                raise MajorProductionError("MAJOR_SEMANTIC_SOURCE_LINK_MISSING")
            if not fragment_id and not source_link_id:
                raise MajorProductionError("MAJOR_SEMANTIC_EVIDENCE_REFERENCE_MISSING")
            result.append({
                "fragment_id": fragment_id or None,
                "source_link_id": source_link_id or None,
                "locator": str(reference.get("locator") or ""),
                "excerpt": str(reference.get("excerpt") or ""),
            })
        if not result:
            raise MajorProductionError("MAJOR_SEMANTIC_EVIDENCE_REFERENCE_MISSING")
        return result

    def _analyze_legacy(
        self,
        case_id: str,
        detail: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        if self.provider is None:
            raise MajorProductionError("MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED")
        links = [link for link in self.repository.source_links(case_id) if link.get("event_id") == event["event_id"]]
        if not links:
            raise MajorProductionError("MAJOR_SOURCE_NOT_FOUND")
        link = links[0]
        version_id = str(link.get("record_id") or "")
        version = self.repository.version(version_id)
        if not version:
            raise MajorProductionError("MAJOR_SOURCE_VERSION_NOT_FOUND")
        source = SourceRef(
            source_id=version_id,
            source_type="MAJOR_SOURCE_DOCUMENT",
            revision=str(version.get("version_no") or "1"),
            content_hash=str(version["content_hash"]),
            fingerprint=str(version["content_hash"]),
            uri=f"major-source://{version_id}",
        )
        fragments = self.repository.fragments(version_id)
        source_text = "\n".join(str(item.get("text_content") or "") for item in fragments[:20])
        specs = [
            MajorIssueObjectSpec(
                object_id=f"{event['event_id']}:{entry_type}",
                unit_id=entry_type,
                locator={"entry_type": entry_type},
                metadata={"entry_type": entry_type},
            )
            for entry_type in ENTRY_TYPES
        ]
        adapter = MajorIssueD01RuntimeAdapter(self.runtime, self.store, self.provider)
        outcome = adapter.execute_partition(
            case_id=case_id,
            issue_version_id=version_id,
            partition_key=event["event_id"],
            source=source,
            expected_objects=specs,
            provider_input={
                "case_id": case_id,
                "event_id": event["event_id"],
                "standard_itr": event.get("standard_itr"),
                "title": detail.get("title"),
                "source_text": source_text,
            },
        )
        if not outcome.business_consumable:
            raise MajorProductionError("MAJOR_ANALYSIS_INCOMPLETE")

        # New analysis can replace only pending AI candidates; confirmed human
        # revisions remain immutable until another explicit review action.
        self.repository.clear_pending_ai_entries_for_event(case_id, event["event_id"])
        created: list[dict[str, Any]] = []
        objects = {item["object_id"]: item for item in outcome.committed_objects}
        for entry_type in ENTRY_TYPES:
            item = objects.get(f"{event['event_id']}:{entry_type}")
            if not item:
                raise MajorProductionError("MAJOR_ANALYSIS_INCOMPLETE")
            data = item.get("data")
            content = data.get("content") if isinstance(data, dict) else data
            content = str(content or "").strip()
            if not content:
                raise MajorProductionError("MAJOR_ANALYSIS_EMPTY_CANDIDATE")
            created.append(self.repository.add_entry(
                case_id,
                entry_type,
                content,
                assertion_kind="AI_INFERENCE",
                origin="AI",
                status="PENDING",
                event_id=event["event_id"],
                evidence=[{
                    "source_link_id": link["source_link_id"],
                    "locator": f"analysis:{entry_type}",
                    "excerpt": content,
                }],
                confidence=0.0,
                explanation="D01_PROVIDER_CANDIDATE",
                analysis_metadata={"runtime_task_id": outcome.task_id, "provider_calls": outcome.provider_calls},
            ))
        return {"case_id": case_id, "event_id": event["event_id"], "runtime_task_id": outcome.task_id, "candidates": created}

    def confirm_entry(
        self,
        entry_id: str,
        *,
        reviewer: str,
        event_id: str | None = None,
        content: str = "",
        reason: str = "",
        action: str = "",
    ) -> dict[str, Any]:
        entry = self.repository.entry(entry_id)
        if not entry:
            raise MajorProductionError("MAJOR_ENTRY_NOT_FOUND")
        if event_id and entry.get("event_id") != event_id:
            raise MajorProductionError("MAJOR_REVIEW_EVENT_MISMATCH")
        if not event_id:
            events = self.repository.events(entry.get("case_id") or "")
            if len(events) != 1 or events[0].get("event_id") != entry.get("event_id"):
                raise MajorProductionError("MAJOR_REVIEW_EVENT_SELECTION_REQUIRED")
        if entry.get("status") != "PENDING" or entry.get("origin") not in {"AI", "SOURCE_FUSION"}:
            raise MajorProductionError("MAJOR_CONFIRMATION_REQUIRES_PENDING_AI_CANDIDATE")
        if not reviewer.strip():
            raise MajorProductionError("MAJOR_REVIEWER_REQUIRED")
        metadata = entry.get("analysis_metadata") or {}
        review_required = entry.get("origin") == "SOURCE_FUSION" and metadata.get("review_status") == "REVIEW_REQUIRED"
        review_action = str(action or "CONFIRM").strip().upper()
        if review_action not in {"CONFIRM", "CORRECT"}:
            raise MajorProductionError("INVALID_MAJOR_REVIEW_ACTION")
        if review_required and (not content.strip() or not reason.strip()):
            raise MajorProductionError("MAJOR_SEMANTIC_REVIEW_DECISION_REQUIRED")
        if entry.get("origin") == "SOURCE_FUSION" and review_action == "CORRECT" and not reason.strip():
            raise MajorProductionError("MAJOR_SEMANTIC_CORRECTION_REASON_REQUIRED")
        revised_content = content.strip() or str(entry.get("content") or "")
        if review_action == "CORRECT" and revised_content == str(entry.get("content") or ""):
            raise MajorProductionError("MAJOR_CORRECTION_MUST_CHANGE_CONTENT")
        if (
            review_action == "CONFIRM"
            and not review_required
            and revised_content != str(entry.get("content") or "")
        ):
            raise MajorProductionError("MAJOR_EDIT_REQUIRES_CORRECT_ACTION")
        return self.repository.revise_entry(
            entry_id,
            revised_content,
            "CORRECTED" if review_action == "CORRECT" else "CONFIRMED",
            reviewer=reviewer.strip(),
            reason=reason.strip() or "HUMAN_CONFIRMED",
        )

    def publish(self, event_id: str) -> dict[str, Any]:
        try:
            return self.publisher.publish_event(event_id)
        except (PublishValidationError, PublishCommitError) as error:
            raise MajorProductionError(error.code) from error

    def detail(self, case_id: str) -> dict[str, Any]:
        detail = self.repository.case_detail(case_id)
        if not detail:
            raise MajorProductionError("MAJOR_CASE_NOT_FOUND")
        return detail
