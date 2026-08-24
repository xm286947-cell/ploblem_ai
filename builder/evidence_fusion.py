from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
import json


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _excel_ref(raw_excel: dict, field_name: str, value: str) -> dict:
    return {
        "source_type": "EXCEL",
        "source_location": (
            f"{raw_excel.get('source_excel', '')}#"
            f"{raw_excel.get('sheet_name', '')}!row={raw_excel.get('excel_row', '')};field={field_name}"
        ),
        "quote": value,
    }


def _pdf_ref(raw_evidence: dict, section: dict, value: str) -> dict:
    pages = section.get("page_numbers") or section.get("pages") or []
    if isinstance(pages, int):
        pages = [pages]
    page_text = ",".join(str(item) for item in pages)
    location = raw_evidence.get("matched_report_path", "")
    if page_text:
        location = f"{location}#pages={page_text}"
    return {
        "source_type": "PDF",
        "source_location": location,
        "quote": value,
    }


def _evidence_value(
    value: str,
    source_type: str,
    source_location: str,
    confidence: float,
    refs: List[dict],
) -> dict:
    return {
        "value": _text(value),
        "source_type": source_type,
        "source_location": source_location,
        "confidence": confidence,
        "evidence_refs": refs,
    }


def _empty_cause_detail() -> dict:
    return {
        "original": "",
        "report": "",
        "standard": "",
        "confidence": 0.0,
        "evidence_refs": [],
    }


def _section_type(section: dict) -> str:
    return _text(
        section.get("section_type")
        or section.get("type")
        or section.get("name")
    )


def _section_content(section: dict) -> str:
    direct = section.get("content")
    if direct is not None:
        return _text(direct)

    blocks = section.get("blocks", [])
    texts: List[str] = []
    for block in blocks:
        if isinstance(block, str):
            text = block.strip()
        else:
            text = _text(block.get("text") or block.get("content"))
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def _sections_by_type(raw_evidence: dict) -> Dict[str, List[dict]]:
    result: Dict[str, List[dict]] = {}
    for section in raw_evidence.get("sections", []):
        kind = _section_type(section)
        if kind:
            result.setdefault(kind, []).append(section)
    return result


def _pdf_values(raw_evidence: dict, section_type: str) -> List[dict]:
    values: List[dict] = []
    for section in _sections_by_type(raw_evidence).get(section_type, []):
        content = _section_content(section)
        if not content:
            continue
        ref = _pdf_ref(raw_evidence, section, content)
        values.append(
            _evidence_value(
                value=content,
                source_type="PDF",
                source_location=ref["source_location"],
                confidence=float(section.get("confidence", 0.9) or 0.9),
                refs=[ref],
            )
        )
    return values


def _joined_pdf_text(raw_evidence: dict, section_type: str) -> str:
    return "\n\n".join(item["value"] for item in _pdf_values(raw_evidence, section_type))


def _cause_detail(raw_excel: dict, raw_evidence: dict, excel_field: str, pdf_section: str) -> dict:
    mapped = raw_excel.get("mapped_fields", {})
    original = _text(mapped.get(excel_field))
    report_values = _pdf_values(raw_evidence, pdf_section)
    report = "\n\n".join(item["value"] for item in report_values)
    refs: List[dict] = []

    if original:
        refs.append(_excel_ref(raw_excel, excel_field, original))
    for item in report_values:
        refs.extend(item["evidence_refs"])

    confidence = 0.0
    if original and report:
        confidence = 0.95
    elif report:
        confidence = 0.9
    elif original:
        confidence = 0.75

    return {
        "original": original,
        "report": report,
        "standard": "",
        "confidence": confidence,
        "evidence_refs": refs,
    }


def _parse_status(raw_excel: dict, raw_evidence: dict) -> str:
    evidence_status = _text(raw_evidence.get("parse_status"))
    excel_status = _text(raw_excel.get("parse_status"))

    if evidence_status == "NO_REPORT":
        return "NO_REPORT"
    if evidence_status in {"REPORT_NOT_FOUND", "AMBIGUOUS"}:
        return "REPORT_NOT_FOUND"
    if evidence_status == "REPORT_PARSE_FAILED":
        return "REPORT_PARSE_FAILED"
    if excel_status != "SUCCESS" or evidence_status in {"PARSED_WITH_WARNINGS", "NOT_PARSED"}:
        return "PARTIAL_SUCCESS"
    return "SUCCESS"


def _evidence_status(raw_evidence: dict) -> str:
    status = _text(raw_evidence.get("parse_status"))
    mapping = {
        "PARSED": "REPORT_PARSED",
        "PARSED_WITH_WARNINGS": "REPORT_PARSED_WITH_WARNINGS",
        "NOT_PARSED": "REPORT_MATCHED_NOT_PARSED",
        "NO_REPORT": "NO_REPORT",
        "REPORT_NOT_FOUND": "REPORT_NOT_FOUND",
        "AMBIGUOUS": "REPORT_MATCH_AMBIGUOUS",
        "REPORT_PARSE_FAILED": "REPORT_PARSE_FAILED",
    }
    return mapping.get(status, status or "UNKNOWN")


class EvidenceFusion:
    """Build a facts-only Standard Case from Raw Excel and Raw Evidence."""

    def __init__(self, app_config: dict) -> None:
        self.app = app_config["app"]

    def fuse(self, raw_excel: dict, raw_evidence: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        mapped = raw_excel.get("mapped_fields", {})
        case_id = _text(raw_excel.get("case_id"))
        original_description = _text(mapped.get("original_description"))
        report_description = _joined_pdf_text(raw_evidence, "problem_description")

        organization_path = [
            value for value in [
                _text(mapped.get("ipmt")),
                _text(mapped.get("spdt")),
                _text(mapped.get("responsible_department_level2")),
            ] if value
        ]

        root_cause_values = _pdf_values(raw_evidence, "root_cause")
        preventive_values = _pdf_values(raw_evidence, "preventive_actions")

        quality_flags: List[str] = []
        evidence_parse_status = _text(raw_evidence.get("parse_status"))
        if evidence_parse_status in {"NO_REPORT", "REPORT_NOT_FOUND", "AMBIGUOUS"}:
            quality_flags.append("REPORT_MISSING")
        if not root_cause_values:
            quality_flags.append("MISSING_ROOT_CAUSE")
        if not preventive_values:
            quality_flags.append("MISSING_PREVENTIVE_ACTION")
        if raw_evidence.get("parse_warnings") or evidence_parse_status in {"PARSED_WITH_WARNINGS", "REPORT_PARSE_FAILED"}:
            quality_flags.append("REPORT_PARSE_WARNING")

        standard_case = {
            "metadata": {
                "case_id": case_id,
                "itr_id": _text(mapped.get("itr_id")),
                "assessment_year": _text(mapped.get("assessment_year")),
                "assessment_month": _text(mapped.get("assessment_month")),
                "report_filename": _text(mapped.get("report_filename")),
                "source_excel": _text(raw_excel.get("source_excel")),
                "source_report": _text(raw_evidence.get("matched_report_path")),
                "builder_version": _text(self.app.get("builder_version")),
                "schema_version": _text(self.app.get("schema_version")),
                "fusion_rule_version": _text(self.app.get("fusion_rule_version")),
                "prompt_version": "",
                "model_version": "",
                "source_file_version": _text(self.app.get("source_file_version")),
                "created_at": now,
                "updated_at": now,
                "generated_at": now,
                "parse_status": _parse_status(raw_excel, raw_evidence),
                "evidence_status": _evidence_status(raw_evidence),
            },
            "business_context": {
                "ipmt": _text(mapped.get("ipmt")),
                "spdt": _text(mapped.get("spdt")),
                "responsible_department_level2": _text(mapped.get("responsible_department_level2")),
                "organization_path": organization_path,
                "product": _text(mapped.get("product")),
                "domain": _text(mapped.get("domain")),
            },
            "problem": {
                "original_description": original_description,
                "report_description": report_description,
                "standard_description": "",
                "problem_summary": "",
                "phenomenon": [],
                "failure_object": [],
                "trigger_condition": [],
                "impact": _pdf_values(raw_evidence, "impact"),
                "event_replay": _pdf_values(raw_evidence, "event_replay"),
            },
            "analysis": {
                "trc": {
                    "occurrence": _cause_detail(raw_excel, raw_evidence, "trc_occurrence", "trc_occurrence"),
                    "escape": _cause_detail(raw_excel, raw_evidence, "trc_escape", "trc_escape"),
                },
                "mrc": {
                    "occurrence": _cause_detail(raw_excel, raw_evidence, "mrc_occurrence", "mrc_occurrence"),
                    "escape": _cause_detail(raw_excel, raw_evidence, "mrc_escape", "mrc_escape"),
                },
                "five_why": _pdf_values(raw_evidence, "five_why"),
                "root_cause": root_cause_values,
                "failure_mechanism": [],
                "contributing_factors": [],
            },
            "classification": {
                "original": {
                    "cause_level1": _text(mapped.get("cause_level1")),
                    "cause_level2": _text(mapped.get("cause_level2")),
                    "cause_level3": _text(mapped.get("cause_level3")),
                    "cause_level4": _text(mapped.get("cause_level4")),
                },
                "report_verified": {
                    "cause_level1": "",
                    "cause_level2": "",
                    "cause_level3": "",
                    "cause_level4": "",
                    "evidence_refs": [],
                },
                "ai_inferred": {
                    "cause_level1": "",
                    "cause_level2": "",
                    "cause_level3": "",
                    "cause_level4": "",
                    "reason": "",
                    "confidence": 0.0,
                },
                "classification_conflict": False,
                "conflict_description": "",
            },
            "solution": {
                "original_solution": [],
                "corrective_actions": _pdf_values(raw_evidence, "corrective_actions"),
                "preventive_actions": preventive_values,
                "management_actions": _pdf_values(raw_evidence, "management_actions"),
                "technical_actions": _pdf_values(raw_evidence, "technical_actions"),
                "reusable_actions": [],
                "action_status": _pdf_values(raw_evidence, "action_status"),
            },
            "knowledge": {
                "case_summary": "",
                "normalized_problem": "",
                "phenomenon_tags": [],
                "failure_object_tags": [],
                "trigger_tags": [],
                "failure_mechanism_tags": [],
                "cause_tags": [],
                "solution_tags": [],
                "keywords": [],
                "retrieval_text": "",
                "quality_flags": sorted(set(quality_flags)),
                "ai_model": "",
                "prompt_version": "",
                "generated_at": "",
            },
        }
        return standard_case
