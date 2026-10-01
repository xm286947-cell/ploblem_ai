"""R1 Markdown Agent view and evidence gate for Hardware Case.

This layer sits above the deterministic DOCX parser and below Unified Runtime.
It deliberately does not persist Cases, map trees, query Knowledge, publish, or
perform OCR/Vision.  The browser POC can therefore inspect Agent output without
mutating the mature Hardware Case product data model.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from services.hardware_case_contract import evidence_supports_fact


MARKDOWN_VIEW_VERSION = "hardware-markdown-view/v1"
R1_AGENT_INPUT_VERSION = "hardware-case-r1-agent-input/v1"
R1_AGENT_RESULT_VERSION = "hardware-case-r1-agent-result/v1"

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
CORE_FACT_FIELDS = frozenset({"symptom", "root_cause", "actions"})


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
    return str(value or "").replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


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
    """Render ordered parser blocks into an Agent-facing Markdown view.

    Paragraph text is preserved verbatim. Tables are represented as Markdown
    tables. A machine-readable source-locator marker precedes every block.
    No heuristic Heading/Section inference is performed here.
    """
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

        segment = marker if not rendered else marker + "\n" + rendered
        parts.append(segment)
        rendered_blocks.append(
            {
                "block_id": block_id,
                "block_type": block_type,
                "source_locator": dict(block.get("source_locator") or {}),
                "markdown": rendered,
            }
        )

    markdown = "\n\n".join(parts)
    return {
        "view_version": MARKDOWN_VIEW_VERSION,
        "source": dict(snapshot.get("source") or {}),
        "identity": dict(snapshot.get("identity") or {}),
        "markdown": markdown,
        "blocks": rendered_blocks,
        "block_order": [str(block["block_id"]) for block in blocks],
    }


def validate_agent_result(
    snapshot: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed on fabricated block IDs, unsupported facts, or tree mapping."""
    blocks = _snapshot_blocks(snapshot)
    block_index = {str(block["block_id"]): block for block in blocks}
    errors: list[str] = []
    warnings: list[str] = []
    evidence: list[dict[str, Any]] = []
    evidence_seen: set[str] = set()

    if not isinstance(result, dict):
        raise HardwareCaseMarkdownError("AGENT_RESULT_OBJECT_REQUIRED")

    for mapping_key in ("circuit_feature_links", "material_links"):
        mappings = result.get(mapping_key)
        if mappings not in (None, []) and mappings:
            errors.append(f"TREE_MAPPING_FORBIDDEN:{mapping_key}")

    facts = result.get("facts")
    if not isinstance(facts, dict):
        errors.append("FACTS_REQUIRED")
        facts = {}

    fact_checks: list[dict[str, Any]] = []
    for field_name in R1_FACT_FIELDS:
        payload = facts.get(field_name)
        if payload is None:
            continue
        if not isinstance(payload, dict):
            errors.append(f"FACT_INVALID:{field_name}")
            continue
        value = payload.get("value")
        refs = [str(item).strip() for item in payload.get("evidence_block_ids") or [] if str(item).strip()]
        missing = [block_id for block_id in refs if block_id not in block_index]
        if missing:
            errors.extend(f"EVIDENCE_BLOCK_NOT_FOUND:{field_name}:{block_id}" for block_id in missing)

        valid_refs = [block_id for block_id in refs if block_id in block_index]
        nonempty = value not in (None, "", [], {})
        if nonempty and not valid_refs:
            code = f"FACT_EVIDENCE_MISSING:{field_name}"
            if field_name in CORE_FACT_FIELDS:
                errors.append(code)
            else:
                warnings.append(code)

        supporting_text = "\n".join(
            str(block_index[block_id].get("text") or "")
            for block_id in valid_refs
            if str(block_index[block_id].get("text") or "").strip()
        )
        supported = True
        if nonempty and valid_refs:
            supported = evidence_supports_fact(value, supporting_text)
            if not supported:
                errors.append(f"FABRICATED_OR_UNSUPPORTED_FACT:{field_name}")

        for block_id in valid_refs:
            if block_id in evidence_seen:
                continue
            evidence_seen.add(block_id)
            block = block_index[block_id]
            evidence.append(
                {
                    "block_id": block_id,
                    "block_type": block.get("block_type"),
                    "text": block.get("text"),
                    "image_ref": block.get("image_ref"),
                    "source_locator": dict(block.get("source_locator") or {}),
                }
            )

        fact_checks.append(
            {
                "field_name": field_name,
                "has_value": nonempty,
                "evidence_block_ids": refs,
                "supported": supported if nonempty else True,
            }
        )

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "fabricated_fact_count": sum(
            1 for item in errors if item.startswith("FABRICATED_OR_UNSUPPORTED_FACT:")
        ),
        "fact_checks": fact_checks,
        "evidence": evidence,
    }


def run_r1_agent_extraction(
    snapshot: dict[str, Any],
    structurer: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    markdown_view = build_markdown_view(snapshot)
    source_blocks = _snapshot_blocks(snapshot)
    runtime_input = {
        "input_contract": R1_AGENT_INPUT_VERSION,
        "source": dict(snapshot.get("source") or {}),
        "identity": dict(snapshot.get("identity") or {}),
        "markdown_view": markdown_view,
        # Keep the original block payload so evidence IDs always resolve back
        # to deterministic parser output rather than Markdown line numbers.
        "blocks": source_blocks,
        # R1 Agent POC explicitly forbids mapping. Existing structure schema
        # still carries these output fields for mature-product compatibility.
        "tree_candidates": {
            "circuit_feature": [],
            "material_device": [],
        },
    }
    result = structurer(runtime_input)
    validation = validate_agent_result(snapshot, result)
    return {
        "result_version": R1_AGENT_RESULT_VERSION,
        "agent_id": "hardware_case.structure",
        "markdown_view": markdown_view,
        "structured_result": result,
        "evidence_validation": validation,
        "status": "PASS" if validation["status"] == "PASS" else "NEEDS_REVIEW",
    }


__all__ = [
    "MARKDOWN_VIEW_VERSION",
    "R1_AGENT_INPUT_VERSION",
    "R1_AGENT_RESULT_VERSION",
    "HardwareCaseMarkdownError",
    "build_markdown_view",
    "validate_agent_result",
    "run_r1_agent_extraction",
]
