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


def _cjk_count(value: Any) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", str(value or "")))


def _hard_grounding_language_mismatch(value: Any, evidence_text: Any) -> bool:
    """Fail closed when Chinese Evidence was translated into non-Chinese prose.

    The local hard-grounding check is deliberately deterministic/lexical, not
    a multilingual semantic model. Treat translation as a contract mismatch
    rather than a fabricated fact so the caller gets an actionable error.
    """
    evidence = str(evidence_text or "")
    candidate = str(value or "")
    return _cjk_count(evidence) >= 4 and _cjk_count(candidate) == 0


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
    aggregate_key_parameter_refs: set[str] = set()
    for item in context.get("key_parameters") or []:
        refs = set(item.get("evidence_block_ids") or [])
        aggregate_key_parameter_refs.update(refs)
        name = str(item.get("name") or "").strip()
        if name:
            mapping[f"engineering_context.key_parameters.{name}"] = refs
    # V1.3.1 correctness closure: Stage B may derive from the canonical
    # aggregate key-parameter path. Its traceability evidence is the union of
    # every validated key_parameters[*].evidence_block_ids.
    mapping["engineering_context.key_parameters"] = aggregate_key_parameter_refs
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
            if text and _hard_grounding_language_mismatch(value, text):
                errors.append(f"HARD_GROUNDED_LANGUAGE_MISMATCH:{field_path}")
            elif text and not evidence_supports_fact(value, text):
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


# === V1.3 TWO-STAGE PIPELINE ===
# Keep the frozen V1.2 function above for injected legacy regressions. The
# public name is rebound below and dispatches to V1.3 whenever the production
# Runtime facade exposes independent Stage A / Stage B entrypoints.
import time as _time

_V12_RUN_R1_AGENT_EXTRACTION = run_r1_agent_extraction
R1_PIPELINE_VERSION = "hardware-r1-agent-pipeline/v1.3.3"
R1_PIPELINE_RESULT_VERSION = "hardware-case-r1-agent-result/v3"
R1_STAGE_A_AGENT_ID = "hardware_case.r1_case_extract"
R1_STAGE_B_AGENT_ID = "hardware_case.r1_reuse_derive"
R1_REUSE_INPUT_VERSION = "hardware-case-r1-reuse-input/v1"
R1_EXECUTION_TRACE_VERSION = "hardware-r1-execution-trace/v1.4"


def _ms(start: float) -> int:
    return max(0, int((_time.perf_counter() - start) * 1000))


def _v13_candidate(payload: Any) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    value = source.get("value")
    status = str(
        source.get("status")
        or source.get("extraction_status")
        or ("EXTRACTED" if not _is_empty(value) else "MISSING")
    ).strip().upper()
    if status not in EXTRACTION_STATUSES:
        status = "EXTRACTED" if not _is_empty(value) else "MISSING"
    return {
        "value": value,
        "extraction_status": status,
        "evidence_block_ids": [
            str(item).strip()
            for item in source.get("evidence_block_ids") or []
            if str(item).strip()
        ],
        # V1.3: these are local deterministic defaults, never model-owned.
        "confidence": None,
        "warnings": [],
    }


def _v13_key_parameter(payload: Any) -> dict[str, Any] | None:
    source = payload if isinstance(payload, dict) else {}
    name = str(source.get("name") or "").strip()
    if not name:
        return None
    base = _v13_candidate(source)
    return {
        "name": name,
        "value": base["value"],
        "unit": source.get("unit"),
        "extraction_status": base["extraction_status"],
        "evidence_block_ids": base["evidence_block_ids"],
        "confidence": None,
        "warnings": [],
    }


def _canonical_derived_field_path(value: Any) -> str:
    """Normalize model JSON leaf paths to the Stage B field contract.

    Stage B sees nested JSON and may naturally emit paths such as
    facts.root_cause.value. Traceability is field-level, not JSON-leaf-level,
    so deterministic normalization removes only the model-owned '.value'
    suffix and maps supported aliases to one canonical field path. Unknown or
    indexed paths remain unchanged and therefore still fail closed.
    """
    field = str(value or "").strip()
    if not field:
        return ""
    if field.endswith(".value"):
        field = field[:-6]

    if field in R1_FACT_FIELDS:
        return f"facts.{field}"
    if field.startswith("facts."):
        name = field[len("facts."):]
        if name in R1_FACT_FIELDS:
            return f"facts.{name}"

    if field in CONTEXT_FIELDS:
        return f"engineering_context.{field}"
    if field.startswith("engineering_context."):
        name = field[len("engineering_context."):]
        if name in CONTEXT_FIELDS or name == "key_parameters":
            return f"engineering_context.{name}"

    return field


def _v13_reusable(payload: Any) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    base = _v13_candidate(source)
    normalized_derived: list[str] = []
    for item in source.get("derived_from_fields") or []:
        field = _canonical_derived_field_path(item)
        if field and field not in normalized_derived:
            normalized_derived.append(field)
    return {
        **base,
        "derived_from_fields": normalized_derived,
        # V1.3: review state is always local.
        "review_status": "UNREVIEWED",
    }


def normalize_stage_a_v13(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise HardwareCaseMarkdownError("AGENT_RESULT_OBJECT_REQUIRED")
    context = result.get("engineering_context")
    context = context if isinstance(context, dict) else {}
    facts = result.get("facts")
    facts = facts if isinstance(facts, dict) else {}
    return {
        "contract_version": R1_EXTRACTION_CONTRACT_VERSION,
        "engineering_context": {
            **{
                name: _v13_candidate(context.get(name))
                for name in CONTEXT_FIELDS
            },
            "key_parameters": [
                item
                for item in (
                    _v13_key_parameter(value)
                    for value in context.get("key_parameters") or []
                )
                if item is not None
            ],
        },
        "facts": {
            name: _v13_candidate(facts.get(name))
            for name in R1_FACT_FIELDS
        },
        "conflicts": [],
        "reusable_knowledge_candidate": {
            name: _v13_reusable(None)
            for name in REUSABLE_FIELDS
        },
    }


def normalize_stage_b_v13(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise HardwareCaseMarkdownError("AGENT_RESULT_OBJECT_REQUIRED")
    reusable = result.get("reusable_knowledge_candidate")
    reusable = reusable if isinstance(reusable, dict) else {}
    return {
        name: _v13_reusable(reusable.get(name))
        for name in REUSABLE_FIELDS
    }


def _stage_a_reuse_projection(extraction: dict[str, Any]) -> dict[str, Any]:
    def compact(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "value": item.get("value"),
            "extraction_status": item.get("extraction_status"),
            "evidence_block_ids": list(item.get("evidence_block_ids") or []),
        }

    context = extraction.get("engineering_context") or {}
    return {
        "engineering_context": {
            **{
                name: compact(context.get(name) or {})
                for name in CONTEXT_FIELDS
            },
            "key_parameters": [
                {
                    "name": item.get("name"),
                    "value": item.get("value"),
                    "unit": item.get("unit"),
                    "extraction_status": item.get("extraction_status"),
                    "evidence_block_ids": list(
                        item.get("evidence_block_ids") or []
                    ),
                }
                for item in context.get("key_parameters") or []
            ],
        },
        "facts": {
            name: compact((extraction.get("facts") or {}).get(name) or {})
            for name in R1_FACT_FIELDS
        },
    }


def _stage_b_input(
    snapshot: dict[str, Any],
    extraction: dict[str, Any],
) -> dict[str, Any]:
    refs: set[str] = set()
    context = extraction.get("engineering_context") or {}
    for name in CONTEXT_FIELDS:
        refs.update((context.get(name) or {}).get("evidence_block_ids") or [])
    for item in context.get("key_parameters") or []:
        refs.update(item.get("evidence_block_ids") or [])
    for name in R1_FACT_FIELDS:
        refs.update(
            ((extraction.get("facts") or {}).get(name) or {}).get(
                "evidence_block_ids"
            )
            or []
        )
    evidence_blocks = []
    for block in _snapshot_blocks(snapshot):
        block_id = str(block["block_id"])
        if block_id not in refs:
            continue
        evidence_blocks.append(
            {
                "block_id": block_id,
                "block_type": str(block.get("block_type") or "UNKNOWN"),
                "text": str(block.get("text") or ""),
                "source_locator": dict(block.get("source_locator") or {}),
            }
        )
    return {
        "input_contract": R1_REUSE_INPUT_VERSION,
        **_stage_a_reuse_projection(extraction),
        "evidence_blocks": evidence_blocks,
    }


def _runtime_summary(
    stage_a: dict[str, Any] | None,
    stage_b: dict[str, Any] | None,
) -> dict[str, Any]:
    a = dict((stage_a or {}).get("runtime") or {})
    b = dict((stage_b or {}).get("runtime") or {})
    return {
        # Top-level Runtime identity follows the latest executed stage, while
        # each stage retains its own independent Run/Task identity below.
        "run_id": b.get("run_id") or a.get("run_id"),
        "task_id": b.get("task_id") or a.get("task_id"),
        "agent_id": b.get("agent_id") or a.get("agent_id") or R1_STAGE_A_AGENT_ID,
        "agent_config_version": b.get("agent_config_version") or a.get("agent_config_version"),
        "agent_config_hash": b.get("agent_config_hash") or a.get("agent_config_hash"),
        "stage_a": a,
        "stage_b": b,
    }


def _trace_base(snapshot: dict[str, Any]) -> dict[str, Any]:
    metadata = snapshot.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    seed = metadata.get("r1_latency_trace")
    seed = seed if isinstance(seed, dict) else {}
    return {
        "EXECUTION_TRACE_VERSION": R1_EXECUTION_TRACE_VERSION,
        "EXECUTION_MODE": "RUN_RESUME",
        "PARSE_MS": int(seed.get("PARSE_MS") or 0),
        "MARKDOWN_MS": int(seed.get("MARKDOWN_MS") or 0),
        "CACHE_KEY_VERSION": "v2",
        "STAGE_A_EXECUTION_MODE": "NOT_RUN",
        "STAGE_A_TOTAL_MS": 0,
        "STAGE_A_PROVIDER_CALL_COUNT": 0,
        "STAGE_A_INITIAL_CALL_COUNT": 0,
        "STAGE_A_TRANSPORT_RETRY_COUNT": 0,
        "STAGE_A_PROVIDER_CALL_MS": [],
        "STAGE_A_PROMPT_TOKENS": "UNKNOWN",
        "STAGE_A_COMPLETION_TOKENS": "UNKNOWN",
        "STAGE_A_VALIDATION_RETRY_COUNT": 0,
        "STAGE_A_CACHE_RECOVERY_CALL_COUNT": 0,
        "STAGE_A_FORCE_RETRY_CALL_COUNT": 0,
        "STAGE_A_CACHE_HIT": False,
        "STAGE_A_CACHE_REJECTED_BY_VALIDATOR": False,
        "STAGE_A_CACHED_FROM_RUN_ID": None,
        "CASE_VALIDATION_MS": 0,
        "CONFLICT_MS": 0,
        "STAGE_B_EXECUTION_MODE": "NOT_RUN",
        "STAGE_B_TOTAL_MS": 0,
        "STAGE_B_PROVIDER_CALL_COUNT": 0,
        "STAGE_B_INITIAL_CALL_COUNT": 0,
        "STAGE_B_TRANSPORT_RETRY_COUNT": 0,
        "STAGE_B_PROVIDER_CALL_MS": [],
        "STAGE_B_PROMPT_TOKENS": "UNKNOWN",
        "STAGE_B_COMPLETION_TOKENS": "UNKNOWN",
        "STAGE_B_VALIDATION_RETRY_COUNT": 0,
        "STAGE_B_CACHE_RECOVERY_CALL_COUNT": 0,
        "STAGE_B_FORCE_RETRY_CALL_COUNT": 0,
        "STAGE_B_CACHE_HIT": False,
        "STAGE_B_CACHE_REJECTED_BY_VALIDATOR": False,
        "STAGE_B_CACHED_FROM_RUN_ID": None,
        "STAGE_B_INPUT_HASH": None,
        "REUSABLE_VALIDATION_MS": 0,
        "GOLDEN_BUILD_MS": 0,
        "PREVIEW_SAVE_MS": None,
        "TOTAL_MS": 0,
    }

def _apply_stage_trace(
    trace: dict[str, Any],
    *,
    stage: str,
    total_ms: int,
    runtime_meta: dict[str, Any],
) -> None:
    prefix = "STAGE_A" if stage == "A" else "STAGE_B"
    if runtime_meta.get("execution_mode"):
        trace[f"{prefix}_EXECUTION_MODE"] = runtime_meta.get("execution_mode")
    trace[f"{prefix}_TOTAL_MS"] = int(trace.get(f"{prefix}_TOTAL_MS") or 0) + int(total_ms)
    for key in (
        "PROVIDER_CALL_COUNT",
        "INITIAL_CALL_COUNT",
        "TRANSPORT_RETRY_COUNT",
        "VALIDATION_RETRY_COUNT",
        "CACHE_RECOVERY_CALL_COUNT",
        "FORCE_RETRY_CALL_COUNT",
    ):
        runtime_key = key.lower()
        trace[f"{prefix}_{key}"] = int(
            trace.get(f"{prefix}_{key}") or 0
        ) + int(runtime_meta.get(runtime_key) or 0)
    trace[f"{prefix}_PROVIDER_CALL_MS"] = [
        *list(trace.get(f"{prefix}_PROVIDER_CALL_MS") or []),
        *list(runtime_meta.get("provider_call_ms") or []),
    ]
    prompt_tokens = runtime_meta.get("prompt_tokens", "UNKNOWN")
    completion_tokens = runtime_meta.get("completion_tokens", "UNKNOWN")
    if prompt_tokens != "UNKNOWN":
        previous = trace.get(f"{prefix}_PROMPT_TOKENS", "UNKNOWN")
        trace[f"{prefix}_PROMPT_TOKENS"] = (
            int(prompt_tokens)
            if previous == "UNKNOWN"
            else int(previous) + int(prompt_tokens)
        )
    if completion_tokens != "UNKNOWN":
        previous = trace.get(f"{prefix}_COMPLETION_TOKENS", "UNKNOWN")
        trace[f"{prefix}_COMPLETION_TOKENS"] = (
            int(completion_tokens)
            if previous == "UNKNOWN"
            else int(previous) + int(completion_tokens)
        )
    trace[f"{prefix}_CACHE_HIT"] = bool(
        trace.get(f"{prefix}_CACHE_HIT")
        or runtime_meta.get("cache_hit")
    )
    trace[f"{prefix}_CACHE_REJECTED_BY_VALIDATOR"] = bool(
        trace.get(f"{prefix}_CACHE_REJECTED_BY_VALIDATOR")
        or runtime_meta.get("cache_rejected_by_validator")
    )
    if not trace.get(f"{prefix}_CACHED_FROM_RUN_ID"):
        trace[f"{prefix}_CACHED_FROM_RUN_ID"] = runtime_meta.get(
            "cached_from_run_id"
        )
    if runtime_meta.get("cache_key_version"):
        trace["CACHE_KEY_VERSION"] = runtime_meta.get("cache_key_version")
    if stage == "B" and runtime_meta.get("stage_b_input_hash"):
        trace["STAGE_B_INPUT_HASH"] = runtime_meta.get("stage_b_input_hash")

def _finish_trace(trace: dict[str, Any]) -> dict[str, Any]:
    additive = (
        "PARSE_MS",
        "MARKDOWN_MS",
        "STAGE_A_TOTAL_MS",
        "CASE_VALIDATION_MS",
        "CONFLICT_MS",
        "STAGE_B_TOTAL_MS",
        "REUSABLE_VALIDATION_MS",
        "GOLDEN_BUILD_MS",
    )
    trace["TOTAL_MS"] = sum(
        int(trace.get(key) or 0) for key in additive
    ) + int(trace.get("PREVIEW_SAVE_MS") or 0)
    return trace


def _pipeline_failure(
    *,
    snapshot: dict[str, Any],
    markdown_view: dict[str, Any],
    trace: dict[str, Any],
    failed_stage: str,
    error_code: str,
    stage_a: dict[str, Any] | None,
    stage_b: dict[str, Any] | None,
    extraction: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    runtime = _runtime_summary(stage_a, stage_b)
    failed = stage_a if failed_stage == "STAGE_A" else stage_b
    failed_meta = dict((failed or {}).get("runtime") or {})
    return {
        "result_version": R1_PIPELINE_RESULT_VERSION,
        "pipeline_version": R1_PIPELINE_VERSION,
        "extraction_contract_version": R1_EXTRACTION_CONTRACT_VERSION,
        "knowledge_object_contract_version": KNOWLEDGE_OBJECT_VERSION,
        "pipeline_status": (
            "PARTIAL_REUSABLE_KNOWLEDGE_FAILED"
            if partial
            else "CASE_EXTRACTION_FAILED"
        ),
        "status": "PARTIAL" if partial else "FAILED",
        "case_extraction": "PASS" if partial else "FAILED",
        "reusable_knowledge": "FAILED" if partial else "NOT_RUN",
        "failed_stage": failed_stage,
        "error_code": error_code,
        "raw_error_code": (failed or {}).get("raw_error_code"),
        "run_id": failed_meta.get("run_id"),
        "task_id": failed_meta.get("task_id"),
        "execution_trace_version": R1_EXECUTION_TRACE_VERSION,
        "execution_mode": trace.get("EXECUTION_MODE"),
        "provider_call_count": int(
            trace.get("STAGE_A_PROVIDER_CALL_COUNT") or 0
        ) + int(trace.get("STAGE_B_PROVIDER_CALL_COUNT") or 0),
        "validation_retry_count": int(
            trace.get("STAGE_A_VALIDATION_RETRY_COUNT") or 0
        ) + int(trace.get("STAGE_B_VALIDATION_RETRY_COUNT") or 0),
        "runtime": runtime,
        "markdown_view": markdown_view,
        "stage_a_result": extraction,
        "structured_result": extraction,
        "evidence_validation": validation or {
            "status": "NOT_RUN",
            "errors": [],
            "warnings": [],
            "fabricated_fact_count": 0,
            "fabricated_block_id_count": 0,
            "fabricated_block_ids": [],
            "evidence": [],
        },
        "knowledge_object": None,
        "latency_trace": _finish_trace(trace),
        "latency_trace_complete": False,
    }


def run_r1_agent_extraction(
    snapshot: dict[str, Any],
    structurer: Callable[[dict[str, Any]], dict[str, Any]] | Any,
    *,
    force_retry: bool = False,
    retry_failed_stage: str | None = None,
) -> dict[str, Any]:
    # Frozen V1.2 injected-callable seam remains for existing regressions.
    if not (
        hasattr(structurer, "run_stage_a")
        and hasattr(structurer, "run_stage_b")
    ):
        return _V12_RUN_R1_AGENT_EXTRACTION(snapshot, structurer)

    retry_stage = str(retry_failed_stage or "").strip().upper() or None
    if retry_stage not in {None, "STAGE_A", "STAGE_B"}:
        raise HardwareCaseMarkdownError("RETRY_FAILED_STAGE_INVALID")
    if force_retry and retry_stage is not None:
        raise HardwareCaseMarkdownError("EXECUTION_MODE_CONFLICT")

    markdown_view = build_markdown_view(snapshot)
    runtime_input = _compact_runtime_input(snapshot, markdown_view)
    source_id = str(
        ((snapshot.get("source") or {}).get("source_id"))
        or ((snapshot.get("identity") or {}).get("source_id"))
        or ""
    )
    markdown_hash = hashlib.sha256(
        str(markdown_view.get("markdown") or "").encode("utf-8")
    ).hexdigest()
    trace = _trace_base(snapshot)
    trace["EXECUTION_MODE"] = (
        "FORCE_FULL_RUN"
        if force_retry
        else "RETRY_FAILED_STAGE"
        if retry_stage
        else "RUN_RESUME"
    )

    def commit_stage(stage: str, result: dict[str, Any]) -> None:
        commit = getattr(structurer, "commit_stage_success", None)
        if callable(commit):
            commit(stage, result)

    def reject_stage(stage: str, result: dict[str, Any]) -> bool:
        reject = getattr(structurer, "reject_stage_cache", None)
        if not callable(reject):
            return False
        reject(stage, result)
        return True

    def stage_a_call(*, recovery: bool = False) -> dict[str, Any]:
        started = _time.perf_counter()
        kwargs: dict[str, Any] = {
            "source_id": source_id,
            "markdown_hash": markdown_hash,
        }
        if recovery:
            kwargs.update(
                force_retry=False,
                bypass_cache=True,
                cache_rejected_by_validator=True,
                execution_mode="CACHE_RECOVERY",
            )
        elif retry_stage == "STAGE_B":
            kwargs.update(
                force_retry=False,
                require_cache_hit=True,
                execution_mode="CACHE_HIT",
            )
        elif retry_stage == "STAGE_A":
            kwargs.update(
                force_retry=True,
                execution_mode="RETRY_FAILED_STAGE",
            )
        elif force_retry:
            kwargs.update(
                force_retry=True,
                execution_mode="FORCE_FULL_RUN",
            )
        else:
            kwargs.update(force_retry=False)
        result = structurer.run_stage_a(runtime_input, **kwargs)
        _apply_stage_trace(
            trace,
            stage="A",
            total_ms=_ms(started),
            runtime_meta=dict(result.get("runtime") or {}),
        )
        return result

    stage_a = stage_a_call()
    if not stage_a.get("ok"):
        error_code = str(
            stage_a.get("error_code") or "RUNTIME_EXECUTION_FAILED"
        )
        if retry_stage == "STAGE_B" and error_code == "STAGE_LAST_GOOD_CACHE_REQUIRED":
            error_code = "STAGE_A_LAST_GOOD_REQUIRED_FOR_STAGE_B_RETRY"
        return _pipeline_failure(
            snapshot=snapshot,
            markdown_view=markdown_view,
            trace=trace,
            failed_stage="STAGE_A",
            error_code=error_code,
            stage_a=stage_a,
            stage_b=None,
        )

    extraction = normalize_stage_a_v13(stage_a["data"])
    validation_started = _time.perf_counter()
    stage_a_validation = validate_agent_result(snapshot, extraction)
    trace["CASE_VALIDATION_MS"] = _ms(validation_started)

    if stage_a_validation["status"] != "PASS":
        stage_a_cache_hit = bool(
            (stage_a.get("runtime") or {}).get("cache_hit")
        )
        if stage_a_cache_hit and reject_stage("STAGE_A", stage_a):
            if retry_stage == "STAGE_B":
                return _pipeline_failure(
                    snapshot=snapshot,
                    markdown_view=markdown_view,
                    trace=trace,
                    failed_stage="STAGE_A",
                    error_code="STAGE_A_LAST_GOOD_INVALID_FOR_STAGE_B_RETRY",
                    stage_a=stage_a,
                    stage_b=None,
                    extraction=extraction,
                    validation=stage_a_validation,
                )
            stage_a = stage_a_call(recovery=True)
            if not stage_a.get("ok"):
                return _pipeline_failure(
                    snapshot=snapshot,
                    markdown_view=markdown_view,
                    trace=trace,
                    failed_stage="STAGE_A",
                    error_code=str(
                        stage_a.get("error_code")
                        or "RUNTIME_EXECUTION_FAILED"
                    ),
                    stage_a=stage_a,
                    stage_b=None,
                )
            extraction = normalize_stage_a_v13(stage_a["data"])
            validation_started = _time.perf_counter()
            stage_a_validation = validate_agent_result(snapshot, extraction)
            trace["CASE_VALIDATION_MS"] += _ms(validation_started)

    if stage_a_validation["status"] != "PASS":
        return _pipeline_failure(
            snapshot=snapshot,
            markdown_view=markdown_view,
            trace=trace,
            failed_stage="STAGE_A",
            error_code="EVIDENCE_VALIDATION_FAILED",
            stage_a=stage_a,
            stage_b=None,
            extraction=extraction,
            validation=stage_a_validation,
        )

    # Runtime/schema completion becomes Stage Success only here.
    commit_stage("STAGE_A", stage_a)

    conflict_started = _time.perf_counter()
    extraction["conflicts"] = detect_title_content_subject_conflict(
        snapshot,
        extraction,
    )
    trace["CONFLICT_MS"] = _ms(conflict_started)

    reuse_input = _stage_b_input(snapshot, extraction)

    def stage_b_call(*, recovery: bool = False) -> dict[str, Any]:
        started = _time.perf_counter()
        kwargs: dict[str, Any] = {
            "source_id": source_id,
            "markdown_hash": markdown_hash,
        }
        if recovery:
            kwargs.update(
                force_retry=False,
                bypass_cache=True,
                cache_rejected_by_validator=True,
                execution_mode="CACHE_RECOVERY",
            )
        elif retry_stage == "STAGE_B":
            kwargs.update(
                force_retry=True,
                execution_mode="RETRY_FAILED_STAGE",
            )
        elif force_retry:
            kwargs.update(
                force_retry=True,
                execution_mode="FORCE_FULL_RUN",
            )
        else:
            kwargs.update(force_retry=False)
        result = structurer.run_stage_b(reuse_input, **kwargs)
        _apply_stage_trace(
            trace,
            stage="B",
            total_ms=_ms(started),
            runtime_meta=dict(result.get("runtime") or {}),
        )
        return result

    stage_b = stage_b_call()
    if not stage_b.get("ok"):
        return _pipeline_failure(
            snapshot=snapshot,
            markdown_view=markdown_view,
            trace=trace,
            failed_stage="STAGE_B",
            error_code=str(
                stage_b.get("error_code") or "RUNTIME_EXECUTION_FAILED"
            ),
            stage_a=stage_a,
            stage_b=stage_b,
            extraction=extraction,
            validation=stage_a_validation,
            partial=True,
        )

    extraction["reusable_knowledge_candidate"] = normalize_stage_b_v13(
        stage_b["data"]
    )
    reusable_validation_started = _time.perf_counter()
    final_validation = validate_agent_result(snapshot, extraction)
    trace["REUSABLE_VALIDATION_MS"] = _ms(reusable_validation_started)

    if final_validation["status"] != "PASS":
        stage_b_cache_hit = bool(
            (stage_b.get("runtime") or {}).get("cache_hit")
        )
        if stage_b_cache_hit and reject_stage("STAGE_B", stage_b):
            stage_b = stage_b_call(recovery=True)
            if not stage_b.get("ok"):
                return _pipeline_failure(
                    snapshot=snapshot,
                    markdown_view=markdown_view,
                    trace=trace,
                    failed_stage="STAGE_B",
                    error_code=str(
                        stage_b.get("error_code")
                        or "RUNTIME_EXECUTION_FAILED"
                    ),
                    stage_a=stage_a,
                    stage_b=stage_b,
                    extraction=extraction,
                    validation=stage_a_validation,
                    partial=True,
                )
            extraction["reusable_knowledge_candidate"] = normalize_stage_b_v13(
                stage_b["data"]
            )
            reusable_validation_started = _time.perf_counter()
            final_validation = validate_agent_result(snapshot, extraction)
            trace["REUSABLE_VALIDATION_MS"] += _ms(
                reusable_validation_started
            )

    if final_validation["status"] != "PASS":
        return _pipeline_failure(
            snapshot=snapshot,
            markdown_view=markdown_view,
            trace=trace,
            failed_stage="STAGE_B",
            error_code="REUSABLE_TRACEABILITY_INVALID",
            stage_a=stage_a,
            stage_b=stage_b,
            extraction=extraction,
            validation=final_validation,
            partial=True,
        )

    # Stage B becomes cache-eligible only after reusable traceability passes.
    commit_stage("STAGE_B", stage_b)

    runtime = _runtime_summary(stage_a, stage_b)
    golden_started = _time.perf_counter()
    knowledge_object = build_golden_knowledge_object(
        snapshot,
        markdown_view,
        extraction,
        final_validation,
        runtime_meta=dict(stage_a.get("runtime") or {}),
    )
    trace["GOLDEN_BUILD_MS"] = _ms(golden_started)
    knowledge_object["provenance"]["case_extraction_run_id"] = (
        (stage_a.get("runtime") or {}).get("run_id")
    )
    knowledge_object["provenance"]["reusable_derivation_run_id"] = (
        (stage_b.get("runtime") or {}).get("run_id")
    )

    has_review_conflict = any(
        item.get("resolution_status") == "NEEDS_REVIEW"
        for item in extraction.get("conflicts") or []
    )
    trace = _finish_trace(trace)
    return {
        "result_version": R1_PIPELINE_RESULT_VERSION,
        "pipeline_version": R1_PIPELINE_VERSION,
        "execution_trace_version": R1_EXECUTION_TRACE_VERSION,
        "execution_mode": trace.get("EXECUTION_MODE"),
        "extraction_contract_version": R1_EXTRACTION_CONTRACT_VERSION,
        "knowledge_object_contract_version": KNOWLEDGE_OBJECT_VERSION,
        "pipeline_status": "GOLDEN_PREVIEW_READY",
        "status": "NEEDS_REVIEW" if has_review_conflict else "PASS",
        "case_extraction": "PASS",
        "reusable_knowledge": "PASS",
        "failed_stage": None,
        "error_code": None,
        "run_id": (stage_b.get("runtime") or {}).get("run_id"),
        "task_id": (stage_b.get("runtime") or {}).get("task_id"),
        "provider_call_count": int(
            trace.get("STAGE_A_PROVIDER_CALL_COUNT") or 0
        ) + int(trace.get("STAGE_B_PROVIDER_CALL_COUNT") or 0),
        "validation_retry_count": int(
            trace.get("STAGE_A_VALIDATION_RETRY_COUNT") or 0
        ) + int(trace.get("STAGE_B_VALIDATION_RETRY_COUNT") or 0),
        "runtime": runtime,
        "markdown_view": markdown_view,
        "stage_a_result": extraction,
        "structured_result": extraction,
        "evidence_validation": final_validation,
        "knowledge_object": knowledge_object,
        "latency_trace": trace,
        "latency_trace_complete": False,
    }


__all__ = list(dict.fromkeys([
    *globals().get("__all__", []),
    "R1_PIPELINE_VERSION",
    "R1_PIPELINE_RESULT_VERSION",
    "R1_STAGE_A_AGENT_ID",
    "R1_STAGE_B_AGENT_ID",
    "R1_REUSE_INPUT_VERSION",
    "R1_EXECUTION_TRACE_VERSION",
    "normalize_stage_a_v13",
    "normalize_stage_b_v13",
    "run_r1_agent_extraction",
]))
