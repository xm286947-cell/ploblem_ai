"""Adapter from confirmed Major Event knowledge to Historical Case publish candidates.

CASE-PUBLISH-001-A deliberately stops before persistence.  It reads the Major
knowledge store, applies the frozen scope/mapping/validation contract, and
returns existing Historical Case artifact shapes for CASE-PUBLISH-001-B.
"""
from __future__ import annotations

from typing import Any, Mapping
import json

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.problem_refs import normalize_itr


PUBLISHABLE_ENTRY_TYPES = frozenset(
    {"ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION"}
)
PUBLICATION_SOURCE_TYPE = "MAJOR_EVENT"


class PublishValidationError(ValueError):
    """Fail-closed publication validation error with a stable code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _entry_values(entries: list[dict[str, Any]], entry_type: str) -> list[str]:
    values: list[str] = []
    for entry in entries:
        if entry.get("entry_type") != entry_type:
            continue
        value = _text(entry.get("content"))
        if value and value not in values:
            values.append(value)
    return values


def _exact_page(fragment: Mapping[str, Any] | None) -> int | None:
    if not fragment:
        return None
    location_type = (_text(fragment.get("location_type")) or "").upper()
    location_ref = _text(fragment.get("location_ref"))
    if "PAGE" not in location_type or not location_ref or not location_ref.isdigit():
        return None
    page = int(location_ref)
    return page if page > 0 else None


class MajorCasePublishAdapter:
    """Build a validated publish candidate for one Major Event / ITR."""

    def __init__(self, repository: MajorKnowledgeRepository):
        self.repository = repository

    def build_candidate(
        self,
        event_id: str,
        *,
        expected_case_id: str | None = None,
        existing_mapping: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = self.repository.event(event_id)
        if not event:
            raise PublishValidationError("EVENT_NOT_FOUND")

        case_id = _text(event.get("case_id"))
        if not case_id:
            raise PublishValidationError("MAJOR_CASE_NOT_FOUND")
        if expected_case_id and case_id != expected_case_id:
            raise PublishValidationError("EVENT_CASE_MISMATCH")

        case = self.repository.get_case(case_id)
        if not case:
            raise PublishValidationError("MAJOR_CASE_NOT_FOUND")
        if _text(event.get("case_id")) != _text(case.get("case_id")):
            raise PublishValidationError("EVENT_CASE_MISMATCH")
        if case.get("status") != "ACTIVE":
            raise PublishValidationError("MAJOR_CASE_NOT_ACTIVE")

        standard_itr = normalize_itr(_text(event.get("standard_itr")) or "")
        identity = {
            "source_type": PUBLICATION_SOURCE_TYPE,
            "source_id": event_id,
            "business_id": standard_itr,
        }
        self._validate_existing_mapping(identity, existing_mapping)

        all_entries = self.repository.entries(case_id)
        unscoped = [
            entry
            for entry in all_entries
            if entry.get("status") == "CONFIRMED"
            and entry.get("entry_type") in PUBLISHABLE_ENTRY_TYPES
            and entry.get("scope_kind") == "UNSCOPED"
        ]
        selected = [
            entry
            for entry in all_entries
            if entry.get("status") == "CONFIRMED"
            and entry.get("entry_type") in PUBLISHABLE_ENTRY_TYPES
            and (
                (
                    entry.get("scope_kind") == "EVENT"
                    and entry.get("event_id") == event_id
                )
                or entry.get("scope_kind") == "CASE_SHARED"
            )
        ]
        if not selected:
            raise PublishValidationError("NO_PUBLISHABLE_CONFIRMED_FACT")

        knowledge_revision = self._knowledge_revision(selected)
        issue_facts = _entry_values(selected, "ISSUE_FACT")
        root_causes = _entry_values(selected, "ROOT_CAUSE")
        actions = _entry_values(selected, "ACTION")
        verifications = _entry_values(selected, "VERIFICATION")

        warnings: list[str] = []
        if unscoped:
            warnings.append("UNSCOPED_KNOWLEDGE_EXCLUDED")
        if not standard_itr:
            warnings.append("STANDARD_ITR_MISSING")
        if not root_causes:
            warnings.append("ROOT_CAUSE_MISSING")
        if not actions:
            warnings.append("ACTION_MISSING")

        case_detail = self.repository.case_detail(case_id) or case
        raw_evidence = self._raw_evidence(
            selected=selected,
            event=event,
            case_detail=case_detail,
        )

        business_context = {
            "major_case_id": case_id,
            "major_event_id": event_id,
            "standard_itr": standard_itr,
            "event_title": _text(event.get("event_title")),
            "domain": _text(case.get("domain")),
            "group_code": _text(case.get("group_code")),
        }
        metadata = {
            "itr_id": standard_itr,
            "publication_source_type": PUBLICATION_SOURCE_TYPE,
            "publication_source_id": event_id,
            "publication_business_id": standard_itr,
            "major_case_id": case_id,
            "knowledge_revision": knowledge_revision,
        }

        enriched_case = {
            "metadata": metadata,
            "business_context": business_context,
            "problem": {
                "standard_description": "\n".join(issue_facts) if issue_facts else None,
                "phenomenon": [{"value": value} for value in issue_facts],
            },
            "analysis": {
                "root_cause": [{"value": value} for value in root_causes],
            },
            "solution": {
                "corrective_actions": [{"value": value} for value in actions],
                "preventive_actions": [],
                "reusable_actions": [],
                "verification_result": "\n".join(verifications) if verifications else None,
            },
            "knowledge": {
                "case_summary": _text(case.get("title")),
            },
            "status": "ACTIVE",
        }

        return {
            "source_identity": identity,
            "major_case_id": case_id,
            "event_id": event_id,
            "standard_itr": standard_itr,
            "knowledge_revision": knowledge_revision,
            "title": _text(case.get("title")),
            "enriched_case": enriched_case,
            "raw_evidence": raw_evidence,
            "validation_warnings": warnings,
        }

    @staticmethod
    def _validate_existing_mapping(
        identity: Mapping[str, Any],
        existing_mapping: Mapping[str, Any] | None,
    ) -> None:
        if not existing_mapping:
            return
        existing_type = _text(existing_mapping.get("source_type"))
        existing_source = _text(existing_mapping.get("source_id"))
        existing_business = _text(existing_mapping.get("business_id"))
        if existing_type and existing_type != identity["source_type"]:
            raise PublishValidationError("PUBLICATION_IDENTITY_CONFLICT")
        if existing_source and existing_source != identity["source_id"]:
            raise PublishValidationError("PUBLICATION_IDENTITY_CONFLICT")
        if (
            existing_business
            and identity.get("business_id")
            and existing_business != identity["business_id"]
        ):
            raise PublishValidationError("PUBLICATION_IDENTITY_CONFLICT")

    @staticmethod
    def _knowledge_revision(entries: list[dict[str, Any]]) -> str:
        """Reuse Major entry revisions; do not create a second revision model."""
        components: list[str] = []
        for entry in sorted(entries, key=lambda item: str(item.get("entry_id") or "")):
            entry_id = _text(entry.get("entry_id"))
            revision_id = _text(entry.get("current_revision_id"))
            if not entry_id or not revision_id:
                raise PublishValidationError("KNOWLEDGE_REVISION_UNAVAILABLE")
            components.append(f"{entry_id}:{revision_id}")
        return "|".join(components)

    def _raw_evidence(
        self,
        *,
        selected: list[dict[str, Any]],
        event: Mapping[str, Any],
        case_detail: Mapping[str, Any],
    ) -> dict[str, Any]:
        fragment_index: dict[str, dict[str, Any]] = {}
        for document in case_detail.get("documents") or []:
            version_id = _text(document.get("version_id"))
            if not version_id:
                continue
            for fragment in self.repository.fragments(version_id):
                fragment_id = _text(fragment.get("fragment_id"))
                if fragment_id:
                    fragment_index[fragment_id] = {
                        "fragment": fragment,
                        "file_name": _text(document.get("original_filename")),
                    }

        source_links = {
            str(link.get("source_link_id")): link
            for link in self.repository.source_links(str(event["case_id"]))
            if link.get("source_link_id")
        }

        sections: list[dict[str, Any]] = []
        file_names: set[str] = set()
        for entry in selected:
            for evidence in entry.get("evidence") or []:
                fragment_ref = fragment_index.get(str(evidence.get("fragment_id") or ""))
                fragment = fragment_ref["fragment"] if fragment_ref else None
                source_link = source_links.get(str(evidence.get("source_link_id") or ""))
                snapshot: dict[str, Any] = {}
                if source_link:
                    try:
                        parsed = json.loads(source_link.get("snapshot_json") or "{}")
                        if isinstance(parsed, dict):
                            snapshot = parsed
                    except (TypeError, json.JSONDecodeError):
                        snapshot = {}

                file_name = (
                    (fragment_ref or {}).get("file_name")
                    or _text(snapshot.get("file_name"))
                    or _text(snapshot.get("report_filename"))
                )
                if file_name:
                    file_names.add(file_name)

                raw_text = _text(evidence.get("excerpt"))
                if not raw_text and fragment:
                    raw_text = _text(fragment.get("text_content"))

                section = (
                    _text((fragment or {}).get("section_path"))
                    or _text(evidence.get("locator"))
                )
                page = _exact_page(fragment)
                url = _text(snapshot.get("url"))
                source_type = (
                    _text((source_link or {}).get("source_type"))
                    or ("REPORT" if fragment else PUBLICATION_SOURCE_TYPE)
                )
                source_id = (
                    _text((source_link or {}).get("standard_itr"))
                    or _text(event.get("standard_itr"))
                    or _text(event.get("event_id"))
                )

                sections.append(
                    {
                        "entry_id": entry.get("entry_id"),
                        "revision_id": entry.get("current_revision_id"),
                        "entry_type": entry.get("entry_type"),
                        "source_type": source_type,
                        "source_id": source_id,
                        "file_name": file_name,
                        "page": page,
                        "page_numbers": [page] if page is not None else [],
                        "section": section,
                        "section_type": str(entry.get("entry_type") or "").lower(),
                        "raw_text": raw_text,
                        "url": url,
                    }
                )

        standard_itr = _text(event.get("standard_itr"))
        return {
            "source_type": PUBLICATION_SOURCE_TYPE,
            "source_id": standard_itr or _text(event.get("event_id")),
            "itr_id": standard_itr,
            "report_filename": next(iter(file_names)) if len(file_names) == 1 else None,
            "sections": sections,
        }
