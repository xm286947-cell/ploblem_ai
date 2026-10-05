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

    def build_draft(self, case_id: str) -> dict[str, Any]:
        """Return a fused in-memory draft with formal repository evidence refs."""
        case = self.repository.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        with self.repository.connect() as connection:
            fact_row = connection.execute(
                """SELECT * FROM kb_source_fact_revision WHERE case_id=?
                   ORDER BY revision_no DESC LIMIT 1""",
                (case_id,),
            ).fetchone()
        source_fact = dict(fact_row) if fact_row else None
        raw_excel = {
            "case_id": case_id,
            "source_excel": source_fact.get("source_ref", "") if source_fact else "",
            "sheet_name": "",
            "excel_row": "",
            "parse_status": "SUCCESS" if source_fact else "MISSING",
            "mapped_fields": _json_object(source_fact.get("normalized_json")) if source_fact else {},
        }
        raw_evidence, fragment_refs, version_ids = self._document_projection(case_id)
        standard_case = self.fusion.fuse(raw_excel, raw_evidence)
        return {
            "case_id": case_id,
            "standard_case": standard_case,
            "semantic_slots": self._semantic_slots(standard_case, fragment_refs, source_fact, version_ids),
            "source_fact_revision_id": source_fact.get("source_fact_revision_id") if source_fact else None,
            "document_version_ids": version_ids,
            "fusion_version": "EvidenceFusion",
        }

    def _document_projection(self, case_id: str) -> tuple[dict[str, Any], dict[str, list[dict]], list[str]]:
        detail = self.repository.case_detail(case_id) or {}
        sections: list[dict[str, Any]] = []
        fragment_refs: dict[str, list[dict]] = {}
        version_ids: list[str] = []
        source_names: list[str] = []
        warnings: list[str] = []
        parse_statuses: list[str] = []
        current_documents: dict[str, dict[str, Any]] = {}
        for document in detail.get("documents", []):
            document_id = str(document.get("document_id") or document.get("version_id") or "")
            previous = current_documents.get(document_id)
            if previous is None or int(document.get("version_no") or 0) > int(previous.get("version_no") or 0):
                current_documents[document_id] = document
        for document in current_documents.values():
            version_id = str(document.get("version_id") or "")
            if not version_id:
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
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        analysis = standard_case.get("analysis", {})
        solution = standard_case.get("solution", {})
        for entry_type, (semantic_slot, parent, field) in SEMANTIC_SLOTS.items():
            refs: list[dict[str, Any]] = []
            if parent in {"trc", "mrc"}:
                detail = analysis.get(parent, {}).get(field, {})
                original = str(detail.get("original") or "").strip()
                report = str(detail.get("report") or "").strip()
                excel_field = f"{parent}_{field}"
                if original and source_fact:
                    refs.append({
                        "source_type": "EXCEL",
                        "source_fact_revision_id": source_fact.get("source_fact_revision_id"),
                        "locator": f"{source_fact.get('source_ref', '')};field={excel_field}",
                        "excerpt": original,
                    })
                section_type = excel_field
                refs.extend({"source_type": "PDF", **item} for item in fragment_refs.get(section_type, []))
                values = [report or original] if report or original else []
                conflict = bool(original and report and original != report)
            else:
                source_values = solution.get(field, []) or []
                values = [str(item.get("value") or "").strip() for item in source_values if str(item.get("value") or "").strip()]
                refs.extend({"source_type": "PDF", **item} for item in fragment_refs.get(field, []))
                conflict = False
            result[entry_type] = {
                "semantic_slot": semantic_slot,
                "values": values,
                "status": "MISSING" if not values else ("CONFLICT" if conflict else "AVAILABLE"),
                "evidence_refs": refs,
                "source_fact_revision_id": source_fact.get("source_fact_revision_id") if source_fact else None,
                "document_version_ids": list(version_ids),
            }
        return result


__all__ = ["MajorSemanticSourceAdapter", "SEMANTIC_SLOTS"]
