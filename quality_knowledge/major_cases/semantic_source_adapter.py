"""Bridge formal Major sources into the mature, facts-only semantic fusion.

This adapter is intentionally read-only: it projects existing Source Fact
revisions and parsed document fragments into the in-memory inputs expected by
``EvidenceFusion``. It does not create another parser, store, or review path.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Any

import yaml

from builder.evidence_fusion import EvidenceFusion
from parser.evidence_blocks import EvidenceBlock, EvidenceBlockBuilder
from parser.pdf_extractor import PageText, PdfExtractionResult
from parser.section_classifier import SectionClassifier
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository


SEMANTIC_SLOTS = {
    "TRC_OCCURRENCE": ("analysis.trc.occurrence", "trc", "occurrence"),
    "TRC_ESCAPE": ("analysis.trc.escape", "trc", "escape"),
    "MRC_OCCURRENCE": ("analysis.mrc.occurrence", "mrc", "occurrence"),
    "MRC_ESCAPE": ("analysis.mrc.escape", "mrc", "escape"),
    "CORRECTIVE_ACTION": ("solution.corrective_actions", "solution", "corrective_actions"),
    "PREVENTIVE_ACTION": ("solution.preventive_actions", "solution", "preventive_actions"),
    "MANAGEMENT_ACTION": ("solution.management_actions", "solution", "management_actions"),
    "TECHNICAL_ACTION": ("solution.technical_actions", "solution", "technical_actions"),
}

def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _page_number(location_ref: str) -> int | None:
    match = re.search(r"(?:^|:)page:(\d+)(?:$|:)", str(location_ref or ""))
    return int(match.group(1)) if match else None


class MajorSemanticSourceAdapter:
    """Project existing repository sources through mature semantic fusion."""

    def __init__(self, repository: MajorKnowledgeRepository, project_root: str | Path):
        self.repository = repository
        self.project_root = Path(project_root).resolve()
        self.classifier = SectionClassifier(self.project_root / "config/section_mapping.yaml")
        app_config = yaml.safe_load((self.project_root / "config/app.yaml").read_text(encoding="utf-8"))
        self.fusion = EvidenceFusion(app_config)

    def build_draft(self, case_id: str, event_id: str | None = None) -> dict[str, Any]:
        """Return a fused in-memory draft with formal repository evidence refs."""
        case = self.repository.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        all_source_links = self.repository.source_links(case_id)
        if event_id is None:
            source_links = all_source_links
        else:
            event = self.repository.event(event_id)
            if not event or event.get("case_id") != case_id:
                raise ValueError("MAJOR_ANALYSIS_EVENT_INVALID")
            source_links = [
                link for link in all_source_links
                if link.get("event_id") in {event_id, None}
            ]
        with self.repository.connect() as connection:
            fact_rows = connection.execute(
                """SELECT * FROM kb_source_fact_revision WHERE case_id=?
                   AND UPPER(source_type)='EXCEL' ORDER BY revision_no DESC""",
                (case_id,),
            ).fetchall()
        source_facts = [dict(row) for row in fact_rows]
        if event_id is not None:
            linked_fact_ids = {
                str(link.get("record_id") or "")
                for link in source_links
                if str(link.get("record_id") or "")
            }
            event_fact_ids = {
                str(link.get("record_id") or "")
                for link in source_links
                if link.get("event_id") == event_id and str(link.get("record_id") or "")
            }
            source_facts = [
                fact for fact in source_facts
                if fact.get("source_fact_revision_id") in linked_fact_ids
            ]
            event_facts = [
                fact for fact in source_facts
                if fact.get("source_fact_revision_id") in event_fact_ids
            ]
            source_facts = event_facts or source_facts
        source_fact = source_facts[0] if source_facts else None
        raw_excel = {
            "case_id": case_id,
            "source_excel": source_fact.get("source_ref", "") if source_fact else "",
            "sheet_name": "",
            "excel_row": "",
            "parse_status": "SUCCESS" if source_fact else "MISSING",
            "mapped_fields": _json_object(source_fact.get("normalized_json")) if source_fact else {},
        }
        raw_evidence, fragment_refs, version_ids = self._document_projection(
            case_id, event_id=event_id, source_links=all_source_links
        )
        standard_case = self.fusion.fuse(raw_excel, raw_evidence)
        return {
            "case_id": case_id,
            "event_id": event_id,
            "standard_case": standard_case,
            "semantic_slots": self._semantic_slots(
                standard_case, fragment_refs, source_fact, version_ids, source_links
            ),
            "compatibility_slots": self._compatibility_slots(
                standard_case, fragment_refs, source_fact, source_links
            ),
            "source_fact_revision_id": source_fact.get("source_fact_revision_id") if source_fact else None,
            "document_version_ids": version_ids,
            "fusion_version": "EvidenceFusion",
        }

    @staticmethod
    def _compatibility_slots(
        standard_case: dict[str, Any],
        fragment_refs: dict[str, list[dict]],
        source_fact: dict[str, Any] | None,
        source_links: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        source_links_by_record: dict[str, list[dict[str, Any]]] = {}
        for link in source_links:
            record_id = str(link.get("record_id") or "")
            if record_id:
                source_links_by_record.setdefault(record_id, []).append(link)

        issue_items: list[dict[str, Any]] = []
        description = str(standard_case.get("problem", {}).get("original_description") or "").strip()
        if description and source_fact:
            revision_id = str(source_fact.get("source_fact_revision_id") or "")
            links = source_links_by_record.get(revision_id, [])
            issue_items.append({
                "value": description,
                "source_type": "EXCEL",
                "evidence_refs": [{
                    "source_type": "EXCEL",
                    "source_fact_revision_id": revision_id,
                    "source_link_ids": [str(link.get("source_link_id")) for link in links if link.get("source_link_id")],
                    "locator": f"{source_fact.get('source_ref', '')};field=original_description",
                    "excerpt": description,
                }],
            })
        for ref in fragment_refs.get("problem_description", []):
            issue_items.append({"value": str(ref.get("excerpt") or "").strip(), "source_type": "PDF", "evidence_refs": [{"source_type": "PDF", **ref}]})

        issue_record_ids = {
            str(source_fact.get("source_fact_revision_id") or "") if item["source_type"] == "EXCEL"
            else str(next((ref.get("version_id") for ref in item["evidence_refs"]), "") or "")
            for item in issue_items
        }
        issue_conflicts = [
            link for record_id in issue_record_ids if record_id
            for link in source_links_by_record.get(record_id, [])
            if str(link.get("match_status") or "").upper() == "CONFLICT"
        ]
        issue_values = list(dict.fromkeys(item["value"] for item in issue_items if item["value"]))
        if issue_conflicts:
            issue_status, issue_review = "CONFLICT", "REVIEW_REQUIRED"
        elif not issue_items:
            issue_status, issue_review = "MISSING", "NOT_APPLICABLE"
        elif len(issue_values) > 1:
            issue_status, issue_review = "MULTI_SOURCE", "REVIEW_REQUIRED"
        else:
            issue_status, issue_review = "AVAILABLE", "NOT_REQUIRED"

        verification_items = [
            {
                "value": str(ref.get("excerpt") or "").strip(),
                "source_type": "PDF",
                "evidence_refs": [{"source_type": "PDF", **ref}],
            }
            for ref in fragment_refs.get("verification_result", [])
            if str(ref.get("excerpt") or "").strip()
        ]
        verification_record_ids = {
            str(ref.get("version_id") or "")
            for item in verification_items
            for ref in item["evidence_refs"]
            if ref.get("version_id")
        }
        verification_conflicts = [
            link for record_id in verification_record_ids
            for link in source_links_by_record.get(record_id, [])
            if str(link.get("match_status") or "").upper() == "CONFLICT"
        ]
        return {
            "ISSUE_FACT": {
                "semantic_slot": "problem.original_description",
                "items": issue_items,
                "values": issue_values,
                "effective_value": issue_items[0]["value"] if len(issue_values) == 1 and issue_items else "",
                "status": issue_status,
                "review_status": issue_review,
                "source_relation_conflicts": [str(link.get("source_link_id") or "") for link in issue_conflicts],
            },
            "VERIFICATION": {
                "semantic_slot": "verification.result",
                "items": verification_items,
                "values": list(dict.fromkeys(item["value"] for item in verification_items)),
                "effective_value": "\n\n".join(item["value"] for item in verification_items),
                "status": "CONFLICT" if verification_conflicts else "AVAILABLE" if verification_items else "MISSING",
                "review_status": "REVIEW_REQUIRED" if verification_conflicts else "NOT_REQUIRED" if verification_items else "NOT_APPLICABLE",
                "source_relation_conflicts": [str(link.get("source_link_id") or "") for link in verification_conflicts],
            },
        }

    def _document_projection(
        self,
        case_id: str,
        *,
        event_id: str | None = None,
        source_links: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, list[dict]], list[str]]:
        detail = self.repository.case_detail(case_id) or {}
        links = source_links if source_links is not None else self.repository.source_links(case_id)
        links_by_record: dict[str, list[dict[str, Any]]] = {}
        for link in links:
            record_id = str(link.get("record_id") or "")
            if record_id:
                links_by_record.setdefault(record_id, []).append(link)
        sections: list[dict[str, Any]] = []
        fragment_refs: dict[str, list[dict]] = {}
        version_ids: list[str] = []
        source_names: list[str] = []
        warnings: list[str] = []
        parse_statuses: list[str] = []
        current_documents: dict[str, dict[str, Any]] = {}
        event_count = len(self.repository.events(case_id)) if event_id is not None else 0
        for document in detail.get("documents", []):
            document_id = str(document.get("document_id") or document.get("version_id") or "")
            previous = current_documents.get(document_id)
            if previous is None or int(document.get("version_no") or 0) > int(previous.get("version_no") or 0):
                current_documents[document_id] = document
        for document in current_documents.values():
            version_id = str(document.get("version_id") or "")
            if not version_id:
                continue
            document_links = links_by_record.get(version_id, [])
            if event_id is not None:
                if document_links and not any(
                    link.get("event_id") in {event_id, None} for link in document_links
                ):
                    continue
                if event_count > 1 and not document_links:
                    # In a multi-Event Case, an unlinked document has no
                    # defensible Event scope. Do not silently reuse it for all.
                    continue
            version_ids.append(version_id)
            source_name = str(document.get("original_filename") or document.get("logical_name") or version_id)
            if source_name not in source_names:
                source_names.append(source_name)
            parse_statuses.append(str(document.get("parse_status") or ""))
            warnings.extend(_json_list(document.get("parse_warnings_json")))
            for fragment in self.repository.fragments(version_id):
                fragment_id = str(fragment.get("fragment_id") or "")
                text = str(fragment.get("text_content") or "").strip()
                if not text:
                    continue
                page = _page_number(str(fragment.get("location_ref") or ""))
                section_path = str(fragment.get("section_path") or "").strip()
                if section_path:
                    blocks = [EvidenceBlock(
                        block_id=fragment_id or f"{version_id}:{fragment.get('ordinal', '')}",
                        page_number=page or 0,
                        order_in_page=int(fragment.get("ordinal") or 0),
                        content=f"{section_path}\n{text}",
                    )]
                else:
                    extraction = PdfExtractionResult(
                        report_file=version_id,
                        page_count=1,
                        pages=[PageText(page or 0, text, len(text))],
                        tables=[],
                        total_characters=len(text),
                        warnings=[],
                    )
                    blocks = EvidenceBlockBuilder().build(extraction)
                for block in blocks:
                    classified = self.classifier.classify(block)
                    kind = classified.section_type
                    section_content = classified.content.strip()
                    if kind == "unknown" or not section_content:
                        continue
                    locator = str(fragment.get("location_ref") or f"fragment:{fragment.get('ordinal', '')}")
                    section = {
                        "section_type": kind,
                        "title": classified.title,
                        "content": section_content,
                        "page_numbers": [page] if page else [],
                        "confidence": classified.confidence,
                        "fragment_id": fragment_id,
                        "version_id": version_id,
                        "locator": locator,
                    }
                    sections.append(section)
                    fragment_refs.setdefault(kind, []).append({
                        "fragment_id": fragment_id,
                        "locator": locator,
                        "excerpt": section_content,
                        "version_id": version_id,
                    })
        if not version_ids:
            parse_status = "NO_REPORT"
        elif not sections:
            parse_status = "PARSED_WITH_WARNINGS"
            warnings.append("NO_MAPPED_SECTIONS")
        elif any(status in {"WARNING", "FAILED"} for status in parse_statuses):
            parse_status = "PARSED_WITH_WARNINGS"
        else:
            parse_status = "PARSED"
        return ({
            "matched_report_path": ", ".join(source_names),
            "parse_status": parse_status,
            "sections": sections,
            "parse_warnings": sorted(set(str(item) for item in warnings if item)),
        }, fragment_refs, version_ids)

    @staticmethod
    def _semantic_slots(
        standard_case: dict[str, Any],
        fragment_refs: dict[str, list[dict]],
        source_fact: dict[str, Any] | None,
        version_ids: list[str],
        source_links: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        analysis = standard_case.get("analysis", {})
        solution = standard_case.get("solution", {})
        source_links_by_record: dict[str, list[dict[str, Any]]] = {}
        for link in source_links:
            record_id = str(link.get("record_id") or "")
            if record_id:
                source_links_by_record.setdefault(record_id, []).append(link)

        def attach_source_links(evidence: dict[str, Any], record_id: str) -> None:
            ids = [
                str(link.get("source_link_id") or "")
                for link in source_links_by_record.get(record_id, [])
                if link.get("source_link_id")
            ]
            if ids:
                evidence["source_link_ids"] = ids

        for entry_type, (semantic_slot, parent, field) in SEMANTIC_SLOTS.items():
            items: list[dict[str, Any]] = []
            if parent in {"trc", "mrc"}:
                detail = analysis.get(parent, {}).get(field, {})
                original = str(detail.get("original") or "").strip()
                report = str(detail.get("report") or "").strip()
                excel_field = f"{parent}_{field}"
                if original and source_fact:
                    evidence = {
                        "source_type": "EXCEL",
                        "source_fact_revision_id": source_fact.get("source_fact_revision_id"),
                        "locator": f"{source_fact.get('source_ref', '')};field={excel_field}",
                        "excerpt": original,
                    }
                    attach_source_links(evidence, str(source_fact.get("source_fact_revision_id") or ""))
                    items.append({"value": original, "source_type": "EXCEL", "evidence_refs": [evidence]})
                section_type = excel_field
                for fragment in fragment_refs.get(section_type, []):
                    evidence = {"source_type": "PDF", **fragment}
                    attach_source_links(evidence, str(fragment.get("version_id") or ""))
                    items.append({
                        "value": str(fragment.get("excerpt") or "").strip(),
                        "source_type": "PDF",
                        "evidence_refs": [evidence],
                    })
                effective_value = report or original
            else:
                source_values = solution.get(field, []) or []
                slot_fragments = fragment_refs.get(field, [])
                for index, source_value in enumerate(source_values):
                    value = str(source_value.get("value") or "").strip()
                    if not value:
                        continue
                    fragment = slot_fragments[index] if index < len(slot_fragments) else None
                    evidence_refs = []
                    if fragment:
                        evidence = {"source_type": "PDF", **fragment}
                        attach_source_links(evidence, str(fragment.get("version_id") or ""))
                        evidence_refs.append(evidence)
                    items.append({"value": value, "source_type": "PDF", "evidence_refs": evidence_refs})
                effective_value = "\n\n".join(item["value"] for item in items)

            values = list(dict.fromkeys(item["value"] for item in items))
            source_record_ids = {
                str(source_fact.get("source_fact_revision_id") or "") if item["source_type"] == "EXCEL"
                else str(next((ref.get("version_id") for ref in item["evidence_refs"]), "") or "")
                for item in items
            }
            relation_conflicts = [
                link for record_id in source_record_ids if record_id
                for link in source_links_by_record.get(record_id, [])
                if link and str(link.get("match_status") or "").upper() == "CONFLICT"
            ]
            distinct_values = set(values)
            if relation_conflicts:
                status = "CONFLICT"
                review_status = "REVIEW_REQUIRED"
            elif not items:
                status = "MISSING"
                review_status = "NOT_APPLICABLE"
            elif parent == "solution":
                status = "AVAILABLE"
                review_status = "NOT_REQUIRED"
            elif len(distinct_values) > 1:
                # W1 has no semantic entailment model. Different text is not
                # proof of contradiction; leave the relationship for Review.
                status = "MULTI_SOURCE"
                review_status = "REVIEW_REQUIRED"
            else:
                status = "AVAILABLE"
                review_status = "NOT_REQUIRED"
            result[entry_type] = {
                "semantic_slot": semantic_slot,
                "items": items,
                "values": values,
                "effective_value": effective_value,
                "status": status,
                "review_status": review_status,
                "evidence_refs": [ref for item in items for ref in item["evidence_refs"]],
                "source_fact_revision_id": source_fact.get("source_fact_revision_id") if source_fact else None,
                "document_version_ids": list(version_ids),
                "source_relation_conflicts": [str(link.get("source_link_id") or "") for link in relation_conflicts],
            }
        return result


__all__ = ["MajorSemanticSourceAdapter", "SEMANTIC_SLOTS"]
