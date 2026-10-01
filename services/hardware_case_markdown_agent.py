"""R1 Markdown, extraction/v2 validation, conflict and Golden preview.

This layer is preview-only. It never mutates mature Hardware Case data, writes
formal Knowledge, maps Trees, searches Knowledge, publishes, or rewrites Source
Fact.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from services.hardware_case_contract import evidence_supports_fact


MARKDOWN_VIEW_VERSION = "hardware-markdown-view/v1"
R1_AGENT_INPUT_VERSION = "hardware-case-r1-agent-input/v2"
R1_EXTRACTION_CONTRACT_VERSION = "hardware-r1-extraction/v2"
R1_AGENT_RESULT_VERSION = "hardware-case-r1-agent-result/v2"
KNOWLEDGE_OBJECT_VERSION = "hardware-case-knowledge-object/v1"
R1_AGENT_ID = "hardware_case.r1_extract"

R1_FACT_FIELDS = (
    "background",
    "symptom",
    "impact",
    "occurrence_condition",
    "analysis_process",
    "failure_mode",
    "root_cause",
    "failure_mechanism",
    "actions",
    "verification_result",
    "conclusion",
)
CONTEXT_FIELDS = (
    "primary_subject",
    "component_or_device",
    "interface",
    "signal",
    "peer_device_or_load",
)
REUSABLE_FIELDS = (
    "engineering_rule",
    "design_constraint",
    "diagnostic_clue",
    "verification_method",
    "applicability",
    "conclusion",
)
EXTRACTION_STATUSES = frozenset({"EXTRACTED", "MISSING", "AMBIGUOUS", "UNSUPPORTED"})
REUSABLE_REVIEW_STATUSES = frozenset({"UNREVIEWED", "CONFIRMED", "REJECTED", "EDITED"})
HARD_GROUNDED_FACT_FIELDS = frozenset({"symptom", "root_cause", "actions", "verification_result"})


class HardwareCaseMarkdownError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _snapshot_blocks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        raise HardwareCaseMarkdownError("SNAPSHOT_OBJECT_REQUIRED")
    if snapshot.get("snapshot_version") != "hardware-document-snapshot/v1":
        raise HardwareCaseMarkdownError("SNAPSHOT_VERSION_UNSUPPORTED")
    structure = snapshot.get("structure")
    if not isinstance(structure, dict):
        raise HardwareCaseMarkdownError("SNAPSHOT_STRUCTURE_REQUIRED")
    blocks = structure.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise HardwareCaseMarkdownError("SNAPSHOT_BLOCKS_REQUIRED")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise HardwareCaseMarkdownError("SNAPSHOT_BLOCK_INVALID")
        block_id = str(block.get("block_id") or "").strip()
        if not block_id or block_id in seen:
            raise HardwareCaseMarkdownError("SNAPSHOT_BLOCK_ID_INVALID")
        seen.add(block_id)
        normalized.append(block)
    return normalized


def _locator_marker(block: dict[str, Any]) -> str:
    locator = block.get("source_locator")
    payload = locator if isinstance(locator, dict) else {}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"<!-- HC_BLOCK {block['block_id']} LOCATOR={encoded} -->"


def _escape_markdown_cell(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def _table_markdown(rows: Any) -> str:
    if not isinstance(rows, list) or not rows:
        return ""
    normalized = [
        [_escape_markdown_cell(cell) for cell in row]
        for row in rows
        if isinstance(row, list)
    ]
    if not normalized:
        return ""
    width = max((len(row) for row in normalized), default=0)
    if width == 0:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    header = normalized[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


def build_markdown_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Render ordered Snapshot blocks into the frozen Agent reading view."""
    blocks = _snapshot_blocks(snapshot)
    parts: list[str] = []
    rendered_blocks: list[dict[str, Any]] = []
    for block in blocks:
        block_id = str(block["block_id"])
        block_type = str(block.get("block_type") or "UNKNOWN").upper()
        marker = _locator_marker(block)
        text = str(block.get("text") or "")
        if block_type == "TABLE":
            rendered = _table_markdown(block.get("table_rows"))
        elif block_type == "IMAGE":
            image_ref = str(block.get("image_ref") or "").strip()
            rendered = f"[IMAGE_REF: {image_ref}]" if image_ref else ""
        elif block_type == "HEADING":
            style = str(block.get("style") or "")
            digits = "".join(ch for ch in style if ch.isdigit())
            level = max(1, min(int(digits or "1"), 6))
            rendered = ("#" * level + " " + text) if text else ""
        else:
            rendered = text
        parts.append(marker if not rendered else marker + "\n" + rendered)
        rendered_blocks.append(
            {
                "block_id": block_id,
                "block_type": block_type,
                "source_locator": dict(block.get("source_locator") or {}),
                "markdown": rendered,
            }
        )
    return {
        "view_version": MARKDOWN_VIEW_VERSION,
        "source": dict(snapshot.get("source") or {}),
        "identity": dict(snapshot.get("identity") or {}),
        "markdown": "\n\n".join(parts),
        "blocks": rendered_blocks,
        "block_order": [str(block["block_id"]) for block in blocks],
    }


def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _normalize_candidate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "value": None,
            "extraction_status": "MISSING",
            "evidence_block_ids": [],
            "confidence": None,
            "warnings": ["FIELD_NOT_RETURNED"],
        }
    value = payload.get("value")
    status = str(payload.get("extraction_status") or "").strip().upper()
    warnings = [str(item) for item in payload.get("warnings") or [] if str(item).strip()]
    if status not in EXTRACTION_STATUSES:
        status = "EXTRACTED" if not _is_empty(value) else "MISSING"
        warnings.append("EXTRACTION_STATUS_NORMALIZED")
    return {
        "value": value,
        "extraction_status": status,
        "evidence_block_ids": [
            str(item).strip()
            for item in payload.get("evidence_block_ids") or []
            if str(item).strip()
        ],
        "confidence": payload.get("confidence"),
        "warnings": list(dict.fromkeys(warnings)),
    }


def _normalize_key_parameter(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    base = _normalize_candidate(payload)
    return {
        "name": name,
        "value": base["value"],
        "unit": payload.get("unit"),
        "extraction_status": base["extraction_status"],
        "evidence_block_ids": base["evidence_block_ids"],
        "confidence": base["confidence"],
        "warnings": base["warnings"],
    }


def _normalize_reusable(payload: Any) -> dict[str, Any]:
    base = _normalize_candidate(payload)
    source = payload if isinstance(payload, dict) else {}
    review_status = str(source.get("review_status") or "UNREVIEWED").strip().upper()
    if review_status not in REUSABLE_REVIEW_STATUSES:
        review_status = "UNREVIEWED"
        base["warnings"].append("REVIEW_STATUS_NORMALIZED")
    return {
        **base,
        "derived_from_fields": [
            str(item).strip()
            for item in source.get("derived_from_fields") or []
            if str(item).strip()
        ],
        "review_status": review_status,
    }


def normalize_extraction_v2(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize missing fields fail-safe without inventing factual values."""
    if not isinstance(result, dict):
        raise HardwareCaseMarkdownError("AGENT_RESULT_OBJECT_REQUIRED")
    context_raw = result.get("engineering_context")
    context_raw = context_raw if isinstance(context_raw, dict) else {}
    facts_raw = result.get("facts")
    facts_raw = facts_raw if isinstance(facts_raw, dict) else {}
    reusable_raw = result.get("reusable_knowledge_candidate")
    reusable_raw = reusable_raw if isinstance(reusable_raw, dict) else {}
    return {
        "contract_version": str(
            result.get("contract_version") or R1_EXTRACTION_CONTRACT_VERSION
        ),
        "engineering_context": {
            **{
                name: _normalize_candidate(context_raw.get(name))
                for name in CONTEXT_FIELDS
            },
            "key_parameters": [
                item
                for item in (
                    _normalize_key_parameter(value)
                    for value in context_raw.get("key_parameters") or []
                )
                if item is not None
            ],
        },
        "facts": {
            name: _normalize_candidate(facts_raw.get(name))
            for name in R1_FACT_FIELDS
        },
        "conflicts": [
            dict(item)
            for item in result.get("conflicts") or []
            if isinstance(item, dict)
        ],
        "reusable_knowledge_candidate": {
            name: _normalize_reusable(reusable_raw.get(name))
            for name in REUSABLE_FIELDS
        },
    }


_TITLE_PREFIX = re.compile(r"^\s*([A-Za-z][A-Za-z0-9+.-]{1,20})(?=[_\-—–\s]|[\u3400-\u9fff])")


def _normalized_subject(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value or "").casefold())


def _title_subject(raw_title: Any) -> str | None:
    match = _TITLE_PREFIX.match(str(raw_title or ""))
    return match.group(1) if match else None


def detect_title_content_subject_conflict(
    snapshot: dict[str, Any],
    extraction: dict[str, Any],
) -> list[dict[str, Any]]:
    """Detect explicit title/body subject disagreement without rewriting Source."""
    identity = snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {}
    raw_title = identity.get("raw_title")
    source_subject = _title_subject(raw_title)
    primary = extraction.get("engineering_context", {}).get("primary_subject", {})
    body_subject = primary.get("value") if isinstance(primary, dict) else None
    if not source_subject or _is_empty(body_subject):
        return []
    source_norm = _normalized_subject(source_subject)
    body_norm = _normalized_subject(body_subject)
    if not source_norm or not body_norm:
        return []
    if source_norm == body_norm or source_norm in body_norm or body_norm in source_norm:
        return []
    evidence_ids = list(primary.get("evidence_block_ids") or [])
    digest = hashlib.sha256(
        f"TITLE_CONTENT_SUBJECT_MISMATCH|{source_norm}|{body_norm}".encode("utf-8")
    ).hexdigest()[:16]
    return [
        {
            "conflict_id": f"CONFLICT-{digest}",
            "type": "TITLE_CONTENT_SUBJECT_MISMATCH",
            "field": "primary_subject",
            "source_values": [
                {"source": "SOURCE_RAW_TITLE", "value": source_subject},
                {"source": "AI_BODY_CANDIDATE", "value": body_subject},
            ],
            "evidence_block_ids": evidence_ids,
            "status": "OPEN",
            "resolution_status": "NEEDS_REVIEW",
            "reviewer_note": None,
        }
    ]


def _merge_conflicts(
    agent_conflicts: list[dict[str, Any]],
    detected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*detected, *agent_conflicts]:
        conflict_type = str(item.get("type") or "").strip()
        field = str(item.get("field") or "").strip()
        if not conflict_type or not field:
            continue
        key = (conflict_type, field)
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(item)
        if not normalized.get("conflict_id"):
            material = json.dumps(
                {
                    "type": conflict_type,
                    "field": field,
                    "source_values": normalized.get("source_values") or [],
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            normalized["conflict_id"] = "CONFLICT-" + hashlib.sha256(
                material.encode("utf-8")
            ).hexdigest()[:16]
        normalized.setdefault("status", "OPEN")
        normalized.setdefault("resolution_status", "NEEDS_REVIEW")
        normalized.setdefault("reviewer_note", None)
        normalized["evidence_block_ids"] = [
            str(value).strip()
            for value in normalized.get("evidence_block_ids") or []
            if str(value).strip()
        ]
        merged.append(normalized)
    return merged


def _candidate_evidence_map(extraction: dict[str, Any]) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for name, payload in extraction.get("facts", {}).items():
        refs = set(payload.get("evidence_block_ids") or [])
        mapping[name] = refs
        mapping[f"facts.{name}"] = refs
    context = extraction.get("engineering_context", {})
    for name in CONTEXT_FIELDS:
        payload = context.get(name) or {}
        refs = set(payload.get("evidence_block_ids") or [])
        mapping[f"engineering_context.{name}"] = refs
        mapping[name] = mapping.get(name, set()) | refs
    for item in context.get("key_parameters") or []:
        name = str(item.get("name") or "").strip()
        if name:
            mapping[f"engineering_context.key_parameters.{name}"] = set(
                item.get("evidence_block_ids") or []
            )
    return mapping


def validate_agent_result(
    snapshot: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Validate v2 candidate Evidence and derived-knowledge traceability."""
    blocks = _snapshot_blocks(snapshot)
    block_index = {str(block["block_id"]): block for block in blocks}
    errors: list[str] = []
    warnings: list[str] = []
    evidence_seen: set[str] = set()
    fabricated_fact_count = 0
    fabricated_block_ids: set[str] = set()

    if result.get("contract_version") != R1_EXTRACTION_CONTRACT_VERSION:
        errors.append("EXTRACTION_CONTRACT_VERSION_INVALID")

    def validate_candidate(
        field_path: str,
        payload: dict[str, Any],
        *,
        hard_semantic_check: bool = False,
    ) -> None:
        nonlocal fabricated_fact_count
        value = payload.get("value")
        status = payload.get("extraction_status")
        refs = [str(item) for item in payload.get("evidence_block_ids") or []]
        if status == "EXTRACTED" and _is_empty(value):
            errors.append(f"EXTRACTED_VALUE_MISSING:{field_path}")
        if status == "MISSING" and not _is_empty(value):
            errors.append(f"MISSING_STATUS_HAS_VALUE:{field_path}")
        if status == "EXTRACTED" and not refs:
            errors.append(f"EXTRACTED_EVIDENCE_MISSING:{field_path}")
        missing = [ref for ref in refs if ref not in block_index]
        for ref in missing:
            fabricated_block_ids.add(ref)
            errors.append(f"EVIDENCE_BLOCK_NOT_FOUND:{field_path}:{ref}")
        valid_refs = [ref for ref in refs if ref in block_index]
        evidence_seen.update(valid_refs)
        if hard_semantic_check and status == "EXTRACTED" and not _is_empty(value) and valid_refs:
            text = "\n".join(
                str(block_index[ref].get("text") or "") for ref in valid_refs
            )
            if text and not evidence_supports_fact(value, text):
                fabricated_fact_count += 1
                errors.append(f"FABRICATED_OR_UNSUPPORTED_FACT:{field_path}")

    context = result.get("engineering_context", {})
    for name in CONTEXT_FIELDS:
        validate_candidate(f"engineering_context.{name}", context.get(name) or {})
    for index, item in enumerate(context.get("key_parameters") or [], start=1):
        validate_candidate(
            f"engineering_context.key_parameters[{index}]",
            item,
        )

    facts = result.get("facts", {})
    for name in R1_FACT_FIELDS:
        validate_candidate(
            name,
            facts.get(name) or {},
            hard_semantic_check=name in HARD_GROUNDED_FACT_FIELDS,
        )

    for index, conflict in enumerate(result.get("conflicts") or [], start=1):
        for ref in conflict.get("evidence_block_ids") or []:
            ref = str(ref)
            if ref not in block_index:
                fabricated_block_ids.add(ref)
                errors.append(f"EVIDENCE_BLOCK_NOT_FOUND:conflict[{index}]:{ref}")
            else:
                evidence_seen.add(ref)

    direct_evidence = _candidate_evidence_map(result)
    reusable = result.get("reusable_knowledge_candidate", {})
    for name in REUSABLE_FIELDS:
        payload = reusable.get(name) or {}
        path = f"reusable_knowledge.{name}"
        validate_candidate(path, payload)
        if payload.get("review_status") != "UNREVIEWED":
            errors.append(f"REUSABLE_REVIEW_STATUS_INVALID:{name}")
        if payload.get("extraction_status") != "EXTRACTED":
            continue
        derived = [str(item) for item in payload.get("derived_from_fields") or []]
        if not derived:
            errors.append(f"REUSABLE_DERIVATION_MISSING:{name}")
            continue
        unknown = [field for field in derived if field not in direct_evidence]
        errors.extend(f"REUSABLE_DERIVED_FIELD_UNKNOWN:{name}:{field}" for field in unknown)
        union: set[str] = set()
        for field in derived:
            union.update(direct_evidence.get(field, set()))
        refs = set(payload.get("evidence_block_ids") or [])
        if not union:
            errors.append(f"REUSABLE_DERIVATION_EVIDENCE_MISSING:{name}")
        elif not refs:
            errors.append(f"REUSABLE_EVIDENCE_MISSING:{name}")
        elif not refs.issubset(union):
            errors.append(f"REUSABLE_EVIDENCE_NOT_TRACEABLE:{name}")

    evidence = []
    for block in blocks:
        block_id = str(block["block_id"])
        if block_id not in evidence_seen:
            continue
        evidence.append(
            {
                "block_id": block_id,
                "block_type": block.get("block_type"),
                "text": block.get("text"),
                "image_ref": block.get("image_ref"),
                "source_locator": dict(block.get("source_locator") or {}),
            }
        )

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "fabricated_fact_count": fabricated_fact_count,
        "fabricated_block_id_count": len(fabricated_block_ids),
        "fabricated_block_ids": sorted(fabricated_block_ids),
        "evidence": evidence,
    }


def build_golden_knowledge_object(
    snapshot: dict[str, Any],
    markdown_view: dict[str, Any],
    extraction: dict[str, Any],
    validation: dict[str, Any],
    *,
    runtime_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build hardware-case-knowledge-object/v1 as a non-persistent Candidate."""
    identity = dict(snapshot.get("identity") or {})
    source = dict(snapshot.get("source") or {})
    blocks = _snapshot_blocks(snapshot)
    facts = extraction["facts"]
    runtime_meta = dict(runtime_meta or {})
    return {
        "contract_version": KNOWLEDGE_OBJECT_VERSION,
        "identity": {
            "business_case_id": identity.get("business_case_id"),
            "raw_title": identity.get("raw_title"),
            "identity_status": identity.get("identity_status"),
        },
        "source_fact": {
            "source_id": source.get("source_id") or identity.get("source_id"),
            "business_case_id": identity.get("business_case_id"),
            "raw_title": identity.get("raw_title"),
            "original_filename": source.get("file_name"),
            "markdown_view": markdown_view,
            "snapshot_version": snapshot.get("snapshot_version"),
            "source_locators": [
                {
                    "block_id": block.get("block_id"),
                    "source_locator": dict(block.get("source_locator") or {}),
                }
                for block in blocks
            ],
        },
        "engineering_context": extraction["engineering_context"],
        "observed_problem": {
            name: facts[name]
            for name in (
                "background",
                "symptom",
                "impact",
                "occurrence_condition",
                "failure_mode",
            )
        },
        "engineering_analysis": {
            name: facts[name]
            for name in ("analysis_process", "root_cause", "failure_mechanism")
        },
        "engineering_resolution": {
            name: facts[name]
            for name in ("actions", "verification_result")
        },
        "reusable_knowledge": extraction["reusable_knowledge_candidate"],
        "evidence": validation.get("evidence") or [],
        "conflicts": extraction.get("conflicts") or [],
        "review": {
            "object_status": "CANDIDATE",
            "reviewer": None,
            "reviewed_at": None,
            "field_decisions": [],
        },
        "provenance": {
            "source_id": source.get("source_id") or identity.get("source_id"),
            "snapshot_version": snapshot.get("snapshot_version"),
            "markdown_version": markdown_view.get("view_version"),
            "agent_id": runtime_meta.get("agent_id") or R1_AGENT_ID,
            "agent_config_version": runtime_meta.get("agent_config_version"),
            "runtime_run_id": runtime_meta.get("run_id"),
            "extraction_contract_version": extraction.get("contract_version"),
        },
    }


def _compact_runtime_input(
    snapshot: dict[str, Any],
    markdown_view: dict[str, Any],
) -> dict[str, Any]:
    """Build the minimal Agent payload without duplicating Markdown/block views."""
    source_blocks = _snapshot_blocks(snapshot)
    evidence_blocks = [
        {
            "block_id": str(block["block_id"]),
            "block_type": str(block.get("block_type") or "UNKNOWN"),
            "text": str(block.get("text") or ""),
            "source_locator": dict(block.get("source_locator") or {}),
        }
        for block in source_blocks
    ]
    return {
        "input_contract": R1_AGENT_INPUT_VERSION,
        "source_fact": {
            "source_id": (snapshot.get("source") or {}).get("source_id"),
            "business_case_id": (snapshot.get("identity") or {}).get("business_case_id"),
            "raw_title": (snapshot.get("identity") or {}).get("raw_title"),
        },
        "markdown": markdown_view.get("markdown") or "",
        "evidence_blocks": evidence_blocks,
    }


def run_r1_agent_extraction(
    snapshot: dict[str, Any],
    structurer: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    markdown_view = build_markdown_view(snapshot)
    runtime_input = _compact_runtime_input(snapshot, markdown_view)
    raw_result = structurer(runtime_input)
    if not isinstance(raw_result, dict):
        raise HardwareCaseMarkdownError("AGENT_RESULT_OBJECT_REQUIRED")
    runtime_meta = dict(raw_result.get("__runtime_meta__") or {})
    raw_without_meta = {
        key: value for key, value in raw_result.items() if key != "__runtime_meta__"
    }
    extraction = normalize_extraction_v2(raw_without_meta)
    detected = detect_title_content_subject_conflict(snapshot, extraction)
    extraction["conflicts"] = _merge_conflicts(
        extraction.get("conflicts") or [],
        detected,
    )
    validation = validate_agent_result(snapshot, extraction)
    knowledge_object = build_golden_knowledge_object(
        snapshot,
        markdown_view,
        extraction,
        validation,
        runtime_meta=runtime_meta,
    )
    return {
        "result_version": R1_AGENT_RESULT_VERSION,
        "extraction_contract_version": R1_EXTRACTION_CONTRACT_VERSION,
        "knowledge_object_contract_version": KNOWLEDGE_OBJECT_VERSION,
        "agent_id": runtime_meta.get("agent_id") or R1_AGENT_ID,
        "runtime": runtime_meta,
        "markdown_view": markdown_view,
        "structured_result": extraction,
        "evidence_validation": validation,
        "knowledge_object": knowledge_object,
        "status": "PASS" if validation["status"] == "PASS" else "NEEDS_REVIEW",
    }


__all__ = [
    "MARKDOWN_VIEW_VERSION",
    "R1_AGENT_INPUT_VERSION",
    "R1_EXTRACTION_CONTRACT_VERSION",
    "R1_AGENT_RESULT_VERSION",
    "KNOWLEDGE_OBJECT_VERSION",
    "HardwareCaseMarkdownError",
    "build_markdown_view",
    "_compact_runtime_input",
    "normalize_extraction_v2",
    "detect_title_content_subject_conflict",
    "validate_agent_result",
    "build_golden_knowledge_object",
    "run_r1_agent_extraction",
]
