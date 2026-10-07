"""Adapter from confirmed Major Event knowledge to Historical Case publish candidates.

CASE-PUBLISH-001-A deliberately stops before persistence.  It reads the Major
knowledge store, applies the frozen scope/mapping/validation contract, and
returns existing Historical Case artifact shapes for CASE-PUBLISH-001-B.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json
import re

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.problem_refs import normalize_itr


SEMANTIC_SLOT_PATHS = {
    "TRC_OCCURRENCE": ("trc", "occurrence"),
    "TRC_ESCAPE": ("trc", "escape"),
    "MRC_OCCURRENCE": ("mrc", "occurrence"),
    "MRC_ESCAPE": ("mrc", "escape"),
}
ACTION_ENTRY_TYPES = (
    "TECHNICAL_ACTION",
    "MANAGEMENT_ACTION",
    "CORRECTIVE_ACTION",
    "PREVENTIVE_ACTION",
)
CANONICAL_ENTRY_TYPES = frozenset((*SEMANTIC_SLOT_PATHS, *ACTION_ENTRY_TYPES))
COMPATIBILITY_ENTRY_TYPES = frozenset({"ISSUE_FACT", "VERIFICATION"})
PUBLISHABLE_ENTRY_TYPES = CANONICAL_ENTRY_TYPES | COMPATIBILITY_ENTRY_TYPES
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


def _evidence_id(
    *,
    source_type: str,
    source_id: str,
    revision_id: str,
    entry_id: str,
    raw_text: str,
) -> str:
    """Return the same stable evidence identity used by common-evidence/v1.0."""
    identity = {
        "source_type": source_type,
        "source_id": source_id,
        "revision_id": revision_id,
        "entry_id": entry_id,
        "raw_text": raw_text,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "MJR-EVD-" + sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _entries_of_type(entries: list[dict[str, Any]], entry_type: str) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry.get("entry_type") == entry_type]


def _join_text(values: list[str]) -> str:
    return "\n".join(value for value in values if value)


def _cause_detail_empty() -> dict[str, Any]:
    return {
        "original": "",
        "report": "",
        "standard": "",
        "confidence": 0.0,
        "evidence_refs": [],
    }


def _source_modality(
    source_type: str | None,
    file_name: str | None,
    *,
    media_type: str | None,
) -> str | None:
    declared = (source_type or "").upper()
    suffix = (Path(file_name or "").suffix or "").lower()
    media = (media_type or "").lower()
    if (
        "EXCEL" in declared
        or suffix in {".xls", ".xlsx", ".xlsm"}
        or "spreadsheet" in media
        or "excel" in media
    ):
        return "EXCEL"
    if "PDF" in declared or suffix == ".pdf" or "pdf" in media:
        return "PDF"
    if suffix in {".doc", ".docx"} or media in {"doc", "docx"}:
        return media.upper() if media in {"doc", "docx"} else suffix[1:].upper()
    return None


def _exact_page(fragment: Mapping[str, Any] | None) -> int | None:
    if not fragment:
        return None
    location_type = (_text(fragment.get("location_type")) or "").upper()
    location_ref = _text(fragment.get("location_ref"))
    if "PAGE" not in location_type or not location_ref:
        return None
    match = re.fullmatch(r"(?:page\s*[:=#-]?\s*)?(\d+)", location_ref, re.IGNORECASE)
    if not match:
        return None
    page = int(match.group(1))
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

        def in_scope(entry: Mapping[str, Any]) -> bool:
            return (
                entry.get("scope_kind") == "EVENT"
                and entry.get("event_id") == event_id
            ) or entry.get("scope_kind") == "CASE_SHARED"

        relevant = [
            entry
            for entry in all_entries
            if entry.get("entry_type") in PUBLISHABLE_ENTRY_TYPES and in_scope(entry)
        ]
        unscoped = [
            entry
            for entry in all_entries
            if entry.get("entry_type") in PUBLISHABLE_ENTRY_TYPES
            and entry.get("scope_kind") == "UNSCOPED"
            and entry.get("status") in {"CONFIRMED", "CORRECTED"}
        ]

        pending = [entry for entry in relevant if entry.get("status") == "PENDING"]
        if pending:
            raise PublishValidationError("PUBLISH_REVIEW_REQUIRED")

        missing = [entry for entry in relevant if entry.get("status") == "MISSING"]
        if any(
            _text(entry.get("content"))
            or entry.get("evidence")
            or entry.get("origin") != "SOURCE_FUSION"
            or entry.get("assertion_kind") != "UNKNOWN"
            for entry in missing
        ):
            raise PublishValidationError("PUBLISH_SEMANTIC_CONTENT_INVALID")

        reviewed = [
            entry
            for entry in relevant
            if entry.get("status") in {"CONFIRMED", "CORRECTED"}
        ]
        invalid_revision = [
            entry
            for entry in reviewed
            if entry.get("assertion_kind") != "HUMAN_REVISION"
            or entry.get("origin") != "HUMAN"
        ]
        if invalid_revision:
            raise PublishValidationError("PUBLISH_REVISION_NOT_HUMAN")

        selected = reviewed
        if not selected:
            raise PublishValidationError("NO_PUBLISHABLE_HUMAN_REVISION")

        for entry in selected:
            if not _text(entry.get("content")):
                raise PublishValidationError("PUBLISH_SEMANTIC_CONTENT_INVALID")
            if not entry.get("evidence"):
                raise PublishValidationError("PUBLISH_EVIDENCE_INVALID")

        for entry_type in SEMANTIC_SLOT_PATHS:
            if len(_entries_of_type(selected, entry_type)) > 1:
                raise PublishValidationError("PUBLISH_SEMANTIC_SLOT_DUPLICATE")

        knowledge_revision = self._knowledge_revision(selected)
        issue_entries = _entries_of_type(selected, "ISSUE_FACT")
        verifications = _entries_of_type(selected, "VERIFICATION")
        occurrence_values = {
            entry_type: [
                str(entry.get("content") or "").strip()
                for entry in _entries_of_type(selected, entry_type)
            ]
            for entry_type in ("TRC_OCCURRENCE", "MRC_OCCURRENCE")
        }
        action_entries = {
            entry_type: _entries_of_type(selected, entry_type)
            for entry_type in ACTION_ENTRY_TYPES
        }

        warnings: list[str] = []
        if unscoped:
            warnings.append("UNSCOPED_KNOWLEDGE_EXCLUDED")
        if not standard_itr:
            warnings.append("STANDARD_ITR_MISSING")
        if not any(occurrence_values.values()):
            warnings.append("ROOT_CAUSE_MISSING")
        if not any(action_entries.values()):
            warnings.append("ACTION_MISSING")
        for entry in missing:
            entry_type = str(entry.get("entry_type") or "UNKNOWN")
            warning = f"SEMANTIC_SLOT_MISSING:{entry_type}"
            if warning not in warnings:
                warnings.append(warning)

        case_detail = self.repository.case_detail(case_id) or case
        raw_evidence = self._raw_evidence(
            selected=selected,
            event=event,
            case_detail=case_detail,
        )
        sections_by_revision: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for section in raw_evidence["sections"]:
            key = (str(section.get("entry_id") or ""), str(section.get("revision_id") or ""))
            sections_by_revision.setdefault(key, []).append(section)
        for entry in selected:
            key = (
                str(entry.get("entry_id") or ""),
                str(entry.get("current_revision_id") or ""),
            )
            sections = sections_by_revision.get(key, [])
            if not sections or any(not section.get("evidence_id") for section in sections):
                raise PublishValidationError("PUBLISH_EVIDENCE_INVALID")

        issue_facts = [
            self._projected_value(entry, sections_by_revision) for entry in issue_entries
        ]
        verification_items = [
            self._projected_value(entry, sections_by_revision) for entry in verifications
        ]
        analysis = {
            "trc": {"occurrence": _cause_detail_empty(), "escape": _cause_detail_empty()},
            "mrc": {"occurrence": _cause_detail_empty(), "escape": _cause_detail_empty()},
        }
        for entry_type, (group, slot) in SEMANTIC_SLOT_PATHS.items():
            entries = _entries_of_type(selected, entry_type)
            if entries:
                analysis[group][slot] = self._projected_cause(
                    entries[0], sections_by_revision
                )

        root_cause_parts: list[tuple[str, str]] = []
        for entry_type, label in (
            ("TRC_OCCURRENCE", "TRC occurrence"),
            ("MRC_OCCURRENCE", "MRC occurrence"),
        ):
            for entry in _entries_of_type(selected, entry_type):
                root_cause_parts.append((label, str(entry.get("content") or "").strip()))
        if len(root_cause_parts) == 1:
            legacy_root_causes = [root_cause_parts[0][1]]
        elif root_cause_parts:
            legacy_root_causes = [
                f"{label}: {value}" for label, value in root_cause_parts
            ]
        else:
            legacy_root_causes = []

        actions = {
            entry_type: [
                self._projected_value(entry, sections_by_revision) for entry in entries
            ]
            for entry_type, entries in action_entries.items()
        }

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
            "semantic_projection_contract": "major-semantic-publish/v1",
        }

        enriched_case = {
            "metadata": metadata,
            "business_context": business_context,
            "problem": {
                "standard_description": _join_text(
                    [str(item["value"]) for item in issue_facts]
                ) or None,
                "phenomenon": issue_facts,
            },
            "analysis": {
                **analysis,
                "root_cause": (
                    [{"value": _join_text(legacy_root_causes)}]
                    if legacy_root_causes
                    else []
                ),
            },
            "solution": {
                "technical_actions": actions["TECHNICAL_ACTION"],
                "management_actions": actions["MANAGEMENT_ACTION"],
                "corrective_actions": actions["CORRECTIVE_ACTION"],
                "preventive_actions": actions["PREVENTIVE_ACTION"],
                "reusable_actions": [],
                "verification_result": _join_text(
                    [str(item["value"]) for item in verification_items]
                ) or None,
                "verification_evidence": verification_items,
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
            # Internal adapter projection consumed by the public Major ->
            # Knowledge port.  The public contract never exposes repository
            # rows or database identifiers beyond the declared source refs.
            "knowledge_entries": [dict(entry) for entry in selected],
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

    @staticmethod
    def _entry_key(entry: Mapping[str, Any]) -> tuple[str, str]:
        return (
            str(entry.get("entry_id") or ""),
            str(entry.get("current_revision_id") or ""),
        )

    def _projected_value(
        self,
        entry: Mapping[str, Any],
        sections_by_revision: Mapping[tuple[str, str], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        sections = sections_by_revision.get(self._entry_key(entry), [])
        modalities = [section.get("source_modality") for section in sections]
        known_modalities = set(modalities)
        if None in known_modalities or not known_modalities:
            source_type = None
        elif known_modalities == {"EXCEL", "PDF"}:
            source_type = "FUSED"
        elif len(known_modalities) == 1:
            source_type = next(iter(known_modalities))
        else:
            source_type = None
        confidence = entry.get("confidence")
        return {
            "value": str(entry.get("content") or "").strip(),
            "source_type": source_type,
            "source_location": (
                f"major-entry://{entry.get('entry_id')}/{entry.get('current_revision_id')}"
            ),
            "confidence": float(confidence) if isinstance(confidence, (int, float)) else 1.0,
            "evidence_refs": [
                {
                    "source_type": section.get("source_modality") or section.get("source_type"),
                    "source_location": f"evidence://{section['evidence_id']}",
                    "quote": str(section.get("raw_text") or ""),
                }
                for section in sections
            ],
        }

    def _projected_cause(
        self,
        entry: Mapping[str, Any],
        sections_by_revision: Mapping[tuple[str, str], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        sections = sections_by_revision.get(self._entry_key(entry), [])
        value = self._projected_value(entry, sections_by_revision)
        return {
            "original": _join_text([
                str(section.get("raw_text") or "")
                for section in sections
                if section.get("source_modality") == "EXCEL"
            ]),
            "report": _join_text([
                str(section.get("raw_text") or "")
                for section in sections
                if section.get("source_modality") == "PDF"
            ]),
            "standard": value["value"],
            "confidence": value["confidence"],
            "evidence_refs": value["evidence_refs"],
        }

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
                        "media_type": _text(document.get("media_type")),
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
                if evidence.get("source_link_id") and not source_link:
                    raise PublishValidationError("PUBLISH_EVIDENCE_SOURCE_NOT_FOUND")
                if evidence.get("fragment_id") and not fragment:
                    raise PublishValidationError("PUBLISH_EVIDENCE_FRAGMENT_NOT_FOUND")
                if not source_link and fragment:
                    # The I2 compatibility slots may carry a Fragment but omit
                    # source_link_id. Resolve only the exact registered document
                    # relation; never pick an arbitrary source for the excerpt.
                    fragment_version_id = _text(fragment.get("version_id"))
                    event_itr = _text(event.get("standard_itr"))
                    matching_record_links = [
                        link for link in source_links.values()
                        if fragment_version_id
                        and _text(link.get("record_id")) == fragment_version_id
                    ]
                    matching_scope_links = [
                        link for link in matching_record_links
                        if _text(link.get("event_id")) in {None, event.get("event_id")}
                        and (
                            not _text(link.get("standard_itr"))
                            or not event_itr
                            or _text(link.get("standard_itr")) == event_itr
                        )
                    ]
                    if len(matching_scope_links) == 1:
                        source_link = matching_scope_links[0]
                    elif matching_record_links:
                        raise PublishValidationError(
                            "PUBLISH_EVIDENCE_EVENT_MISMATCH"
                            if not matching_scope_links
                            else "PUBLISH_EVIDENCE_SOURCE_MISMATCH"
                        )
                if not source_link:
                    raise PublishValidationError("PUBLISH_EVIDENCE_SOURCE_REQUIRED")
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
                source_record_id = (
                    _text((source_link or {}).get("record_id"))
                    or _text(snapshot.get("record_id"))
                )
                link_event_id = _text((source_link or {}).get("event_id"))
                link_itr = _text((source_link or {}).get("standard_itr"))
                event_itr = _text(event.get("standard_itr"))
                if (
                    (link_event_id and link_event_id != event.get("event_id"))
                    or (link_itr and event_itr and link_itr != event_itr)
                ):
                    raise PublishValidationError("PUBLISH_EVIDENCE_EVENT_MISMATCH")
                if fragment:
                    fragment_version_id = _text(fragment.get("version_id"))
                    source_version_id = (
                        _text(snapshot.get("version_id"))
                        or _text(snapshot.get("document_version_id"))
                        or source_record_id
                    )
                    if (
                        not fragment_version_id
                        or not source_version_id
                        or fragment_version_id != source_version_id
                        or not source_record_id
                        or source_record_id != source_version_id
                    ):
                        raise PublishValidationError("PUBLISH_EVIDENCE_SOURCE_MISMATCH")
                if "SOURCE_FACT" in source_type.upper():
                    source_fact_revision_id = _text(snapshot.get("source_fact_revision_id"))
                    if (
                        source_fact_revision_id
                        and source_record_id
                        and source_fact_revision_id != source_record_id
                    ):
                        raise PublishValidationError("PUBLISH_EVIDENCE_SOURCE_MISMATCH")
                source_modality = _source_modality(
                    _text(snapshot.get("source_type")) or source_type,
                    file_name,
                    media_type=(fragment_ref or {}).get("media_type"),
                )
                entry_id = _text(entry.get("entry_id")) or ""
                revision_id = _text(entry.get("current_revision_id")) or ""
                evidence_id = (
                    _evidence_id(
                        source_type=source_type,
                        source_id=source_id or "",
                        revision_id=revision_id,
                        entry_id=entry_id,
                        raw_text=raw_text or "",
                    )
                    if entry_id and revision_id and raw_text
                    else None
                )
                source_version = revision_id or None
                source_ref = (
                    f"{source_type}:{source_id}@{source_version}"
                    if source_id and source_version
                    else None
                )
                origin_source_version = (
                    _text((fragment or {}).get("version_id"))
                    or _text(snapshot.get("source_fact_revision_id"))
                    or _text(snapshot.get("version_id"))
                    or (
                        source_record_id
                        if "SOURCE_FACT" in source_type.upper()
                        else _text((source_link or {}).get("source_version"))
                    )
                )
                origin_source_ref = (
                    f"{source_type}:{source_record_id}@{origin_source_version}"
                    if source_record_id and origin_source_version
                    else None
                )

                sections.append(
                    {
                        "evidence_id": evidence_id,
                        "entry_id": entry_id or None,
                        "revision_id": revision_id or None,
                        "entry_type": entry.get("entry_type"),
                        "source_modality": source_modality,
                        "source_type": source_type,
                        "source_id": source_id,
                        "source_version": source_version,
                        "source_ref": source_ref,
                        "origin_source_id": source_record_id,
                        "origin_source_version": origin_source_version,
                        "origin_source_ref": origin_source_ref,
                        "source_link_id": _text((source_link or {}).get("source_link_id")),
                        "fragment_id": _text((fragment or {}).get("fragment_id")),
                        "locator": _text(evidence.get("locator")),
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
