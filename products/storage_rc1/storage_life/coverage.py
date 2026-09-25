"""Storage-owned document coverage semantics.

This module classifies business fields after a Document Analysis result has been
validated.  It does not call a model and does not own provider, retry, task, or
checkpoint behaviour.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from . import templates


FOUND = "FOUND"
NOT_SPECIFIED = "NOT_SPECIFIED"
NOT_APPLICABLE = "NOT_APPLICABLE"
UNRESOLVED = "UNRESOLVED"
CLOSED_STATES = {FOUND, NOT_SPECIFIED, NOT_APPLICABLE}

IDENTITY_FIELDS = (
    "manufacturer",
    "product_family",
    "covered_part_numbers",
    "document_number",
    "revision",
    "revision_date",
    "document_status",
    "document_variant",
    "language",
)

CRITICAL_FIELDS = {
    "NOR Flash": {"pe_cycles", "retention"},
    "NAND Flash": {"cell_type", "pe_cycles", "retention", "ecc_capability"},
    "eMMC": {
        "life_time_a", "life_time_b", "pre_eol",
        "device_life_time_est_typ_a", "device_life_time_est_typ_b", "pre_eol_info",
    },
    "SSD": {"tbw", "smart_health"},
}

EMMC_DIAGNOSTIC_ALIASES = {
    "device_life_time_est_typ_a", "device_life_time_est_typ_b", "pre_eol_info",
    "bkops_status", "vendor_proprietary_health_report", "vendor_health_monitoring",
    "bad_block_count", "erase_cycle_count", "erase_cycle_granularity",
}


def profile_fields(device_type: str) -> list[str]:
    """Return fields applicable to one device profile, preserving profile order."""
    dtype = templates.normalize_device_type(device_type)
    return list(templates.analysis_fields_for(dtype))


def all_profile_fields() -> list[str]:
    """Return the stable cross-device field universe used to expose NOT_APPLICABLE."""
    out: list[str] = []
    for dtype in templates.device_types():
        for field in templates.analysis_fields_for(dtype):
            if field not in out:
                out.append(field)
    return out


def field_layer(device_type: str, field_key: str) -> str:
    dtype = templates.normalize_device_type(device_type)
    if field_key in IDENTITY_FIELDS:
        return "identity"
    if field_key in CRITICAL_FIELDS.get(dtype, set()):
        return "critical"
    knowledge = templates.parameter_knowledge(dtype, field_key)
    if knowledge.get("role") == "diagnostic" or (
        dtype == "eMMC" and field_key in EMMC_DIAGNOSTIC_ALIASES
    ):
        return "diagnostic"
    return "general"


def build_search_scope(
    pages: Iterable[tuple[int, str, str]],
    selected_pages: Iterable[int],
    device_type: str,
    vendor: str = "",
) -> dict[str, Any]:
    """Record the actual page/section plan consumed by Document Analysis.

    Section names come from the deterministic semantic read plan.  They are
    navigation metadata only and are never promoted to fact evidence.
    """
    dtype = templates.normalize_device_type(device_type)
    selected = {int(page) for page in selected_pages}
    page_list = list(pages)
    plan = templates.build_read_plan(page_list, dtype, vendor)
    sections: list[dict[str, Any]] = []
    searched_fields: dict[str, set[int]] = defaultdict(set)
    covered_pages = set()
    for entry in plan:
        page = int(entry.get("page") or 0)
        if page not in selected:
            continue
        covered_pages.add(page)
        section = str(entry.get("section") or "unmapped")
        fields = [str(x) for x in entry.get("target_fields") or []]
        sections.append({"page": page, "section": section, "target_fields": fields})
        for field in fields:
            searched_fields[field].add(page)

    # Identity fields have stricter search semantics than ordinary specs.
    # Overview/cover pages are enough to check basic identity, while valid part
    # numbers require an ordering/valid-parts section to have actually been read.
    overview_pages = sorted(selected)[:6]
    for field in (
        "manufacturer", "product_family", "document_number", "revision",
        "revision_date", "document_status", "document_variant", "language",
    ):
        searched_fields[field].update(overview_pages)
    for entry in sections:
        marker = entry["section"].upper()
        if any(token in marker for token in ("ORDERING", "VALID PART", "PART NUMBER", "PRODUCT OPTIONS")):
            searched_fields["covered_part_numbers"].add(entry["page"])
        if "REVISION" in marker:
            for field in ("revision", "revision_date", "document_status"):
                searched_fields[field].add(entry["page"])

    # Keep every selected page visible, even when no known semantic heading was
    # detected.  This proves what the model saw without claiming a field search.
    for page in sorted(selected - covered_pages):
        sections.append({"page": page, "section": "unmapped", "target_fields": []})

    sections.sort(key=lambda item: (item["page"], item["section"]))
    return {
        "searched_pages": sorted(selected),
        "searched_sections": sections,
        "searched_fields": {key: sorted(value) for key, value in sorted(searched_fields.items())},
    }


def _valid_found(fact: dict[str, Any], searched_pages: set[int]) -> bool:
    if str(fact.get("status") or "").lower() != "found":
        return False
    if fact.get("value") is None or fact.get("value") == "":
        return False
    evidence = fact.get("resolved_evidence") or (
        (fact.get("evidence") or [None])[0] if isinstance(fact.get("evidence"), list) else None
    )
    if not isinstance(evidence, dict):
        return False
    try:
        page = int(evidence.get("page") or evidence.get("source_page") or 0)
    except (TypeError, ValueError):
        return False
    return bool(page in searched_pages and (evidence.get("quote") or evidence.get("source_text")))


def compute_coverage(
    *,
    device_type: str,
    facts: Iterable[dict[str, Any]],
    searched_pages: Iterable[int],
    searched_fields: dict[str, Iterable[int]] | None = None,
    expected_fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compute four-state, layered Coverage from validated facts and search scope."""
    dtype = templates.normalize_device_type(device_type)
    applicable = list(expected_fields or [*IDENTITY_FIELDS, *profile_fields(dtype)])
    applicable = list(dict.fromkeys(str(x) for x in applicable))
    applicable_set = set(applicable)
    universe = list(dict.fromkeys([*IDENTITY_FIELDS, *all_profile_fields(), *applicable]))
    pages = {int(x) for x in searched_pages}
    searched = {str(key): {int(x) for x in values} for key, values in (searched_fields or {}).items()}
    by_key = {str(item.get("field_key") or ""): item for item in facts if isinstance(item, dict)}

    items: list[dict[str, Any]] = []
    for field in universe:
        layer = field_layer(dtype, field)
        fact = by_key.get(field) or {}
        if field not in applicable_set:
            state = NOT_APPLICABLE
            reason = "outside_device_profile"
        elif _valid_found(fact, pages):
            state = FOUND
            reason = "value_and_evidence_valid"
        elif str(fact.get("status") or "").lower() == "found":
            state = UNRESOLVED
            reason = "found_claim_has_invalid_or_unseen_evidence"
        elif str(fact.get("status") or "").lower() in {"ambiguous", "conflict"}:
            state = UNRESOLVED
            reason = "ambiguous_or_conflict"
        elif field in searched and bool(searched[field] & pages):
            state = NOT_SPECIFIED
            reason = "relevant_section_searched_without_supported_value"
        else:
            state = UNRESOLVED
            reason = "relevant_section_not_searched_or_evidence_invalid"
        items.append({
            "field_key": field,
            "layer": layer,
            "state": state,
            "reason": reason,
            "searched_pages": sorted(searched.get(field, set()) & pages),
        })

    layers = {}
    for layer in ("identity", "critical", "diagnostic", "general"):
        layer_items = [item for item in items if item["layer"] == layer]
        applicable_items = [item for item in layer_items if item["state"] != NOT_APPLICABLE]
        closed = [item for item in applicable_items if item["state"] in CLOSED_STATES]
        counts = {state: sum(1 for item in layer_items if item["state"] == state)
                  for state in (FOUND, NOT_SPECIFIED, NOT_APPLICABLE, UNRESOLVED)}
        layers[layer] = {
            "counts": counts,
            "applicable_count": len(applicable_items),
            "closed_count": len(closed),
            "coverage_ratio": 1.0 if not applicable_items else round(len(closed) / len(applicable_items), 4),
            "complete": len(closed) == len(applicable_items),
            "unresolved_fields": [item["field_key"] for item in applicable_items if item["state"] == UNRESOLVED],
        }

    critical_unresolved = layers["critical"]["unresolved_fields"]
    identity_unresolved = layers["identity"]["unresolved_fields"]
    diagnostic_unresolved = layers["diagnostic"]["unresolved_fields"]
    return {
        "device_type": dtype,
        "states": items,
        "layers": layers,
        "closed": not (identity_unresolved or critical_unresolved or diagnostic_unresolved),
        "critical_unresolved": critical_unresolved,
        "identity_unresolved": identity_unresolved,
        "diagnostic_unresolved": diagnostic_unresolved,
    }


REVIEW_REQUIRED_IDENTITY_FOUND = {"manufacturer", "covered_part_numbers"}


def compute_review_gate(*, coverage: dict[str, Any], facts: Iterable[dict[str, Any]], schema_valid: bool = True) -> dict[str, Any]:
    """Derive Storage's deterministic Review Gate from post-supplement Coverage.

    This gate decides whether a Final Review model is needed; it does not call one.
    NOT_SPECIFIED closes ordinary specification coverage, but vendor and valid part-number
    identity are deliberately stricter because downstream device attribution depends on them.
    """
    states = {str(item.get("field_key") or ""): item for item in (coverage.get("states") or [])}
    queue: list[dict[str, Any]] = []
    if not schema_valid:
        queue.append({"type": "schema_invalid"})

    for field in coverage.get("critical_unresolved") or []:
        queue.append({"type": "critical_unresolved", "field_key": field})
    for field in coverage.get("diagnostic_unresolved") or []:
        queue.append({"type": "diagnostic_unresolved", "field_key": field})
    for field in coverage.get("identity_unresolved") or []:
        queue.append({"type": "identity_unresolved", "field_key": field})

    for field in sorted(REVIEW_REQUIRED_IDENTITY_FOUND):
        item = states.get(field) or {}
        if item.get("state") not in {FOUND, NOT_APPLICABLE}:
            queue.append({"type": "identity_missing", "field_key": field, "coverage_state": item.get("state") or UNRESOLVED})

    for fact in facts or []:
        status = str(fact.get("status") or "").lower()
        if status in {"ambiguous", "conflict"}:
            entry = {"type": status, "field_key": str(fact.get("field_key") or "")}
            if status == "conflict":
                entry["evidence"] = list(fact.get("resolved_conflict_evidence") or [])
            queue.append(entry)

    deduped = []
    seen = set()
    for item in queue:
        marker = (item.get("type"), item.get("field_key"), item.get("coverage_state"))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return {
        "required": bool(deduped),
        "status": "required" if deduped else "not_required",
        "queue": deduped,
    }
