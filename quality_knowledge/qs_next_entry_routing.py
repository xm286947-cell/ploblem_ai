"""Three-workbench Quality Scenario entry/domain contract (standalone, read-only).

The workbench is an *entry point*, NOT necessarily a formal production source.
Legacy material-type IDs and association statuses are reused without querying
or changing the frozen PATCH57 repository. This module has no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "quality-scenario-entry/v1"


class EntryWorkbench(str, Enum):
    THOROUGH_SOLUTION = "THOROUGH_SOLUTION"
    MISSED_TEST = "MISSED_TEST"
    SOFTWARE_ASSESSMENT = "SOFTWARE_ASSESSMENT"


class ProblemDomain(str, Enum):
    SOFTWARE = "SOFTWARE"
    HARDWARE = "HARDWARE"
    MECHANICAL = "MECHANICAL"


class FormalSourceType(str, Enum):
    THOROUGH_SOLUTION_ORDER = "THOROUGH_SOLUTION_ORDER"
    MISSED_TEST_ANALYSIS = "MISSED_TEST_ANALYSIS"


@dataclass(frozen=True, slots=True)
class WorkbenchSpec:
    entry_type: EntryWorkbench
    material_type: str
    formal_source_type: FormalSourceType | None
    allowed_domains: frozenset[ProblemDomain]
    route_hint: str | None


ENTRY_SPECS = {
    EntryWorkbench.THOROUGH_SOLUTION: WorkbenchSpec(
        EntryWorkbench.THOROUGH_SOLUTION, "ITR_CS",
        FormalSourceType.THOROUGH_SOLUTION_ORDER,
        frozenset(ProblemDomain), "/materials/cs",
    ),
    EntryWorkbench.MISSED_TEST: WorkbenchSpec(
        EntryWorkbench.MISSED_TEST, "ESCAPE_ANALYSIS",
        FormalSourceType.MISSED_TEST_ANALYSIS,
        frozenset({ProblemDomain.SOFTWARE}), None,
    ),
    EntryWorkbench.SOFTWARE_ASSESSMENT: WorkbenchSpec(
        EntryWorkbench.SOFTWARE_ASSESSMENT, "SOFTWARE_OPERATION",
        None, frozenset({ProblemDomain.SOFTWARE}),
        "/materials/software-operations",
    ),
}
FORMAL_MATERIAL_TYPES = {
    "ITR_CS": FormalSourceType.THOROUGH_SOLUTION_ORDER,
    "ESCAPE_ANALYSIS": FormalSourceType.MISSED_TEST_ANALYSIS,
}
VALID_LINK_STATUSES = frozenset({"LINKED", "MANUAL_LINKED"})
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class EntryContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any, error: str, *, optional: bool = False) -> str:
    if optional and (value is None or value == ""):
        return ""
    if not isinstance(value, str):
        raise EntryContractError(error)
    value = value.strip()
    if (not value and not optional) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise EntryContractError(error)
    return value


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EntryContractError("INVALID_SOURCE_REVISION")
    return value


def _source_hash(value: Any) -> str:
    value = _text(value, "SOURCE_HASH_REQUIRED")
    if not SHA256.fullmatch(value):
        raise EntryContractError("INVALID_SOURCE_HASH")
    return value.lower()


def _domains(value: Any, *, spec: WorkbenchSpec) -> tuple[ProblemDomain, ...]:
    if value is None:
        raw: list[Any] = []
    elif isinstance(value, (tuple, list)):
        raw = list(value)
    else:
        raise EntryContractError("INVALID_DOMAIN_LIST")
    parsed: list[ProblemDomain] = []
    for domain in raw:
        try:
            parsed.append(ProblemDomain(domain))
        except (ValueError, TypeError):
            raise EntryContractError("UNSUPPORTED_PROBLEM_DOMAIN") from None
    if len(parsed) != len(set(parsed)):
        raise EntryContractError("DUPLICATE_PROBLEM_DOMAIN")
    if not set(parsed).issubset(spec.allowed_domains):
        raise EntryContractError("DOMAIN_NOT_ALLOWED_FOR_WORKBENCH")
    # Both existing software-only workbenches have a fixed, controlled domain.
    if not parsed and spec.entry_type != EntryWorkbench.THOROUGH_SOLUTION:
        return (ProblemDomain.SOFTWARE,)
    # For CS, absent domain must stay absent, pending a verified classification.
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    material_type: str
    material_id: str
    source_hash: str
    version_no: int
    canonical_problem_ref: str
    formal_source_type: FormalSourceType | None
    relation: str
    relation_ref: str
    domains: tuple[ProblemDomain, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "material_type": self.material_type,
            "material_id": self.material_id,
            "source_hash": self.source_hash,
            "version_no": self.version_no,
            "canonical_problem_ref": self.canonical_problem_ref,
            "formal_source_type": self.formal_source_type.value if self.formal_source_type else None,
            "relation": self.relation,
            "relation_ref": self.relation_ref,
            "domains": [domain.value for domain in self.domains],
        }


def _snapshot(
    record: Mapping[str, Any],
    *,
    relation: str,
    entry_domain_spec: WorkbenchSpec,
) -> SourceSnapshot:
    material_type = _text(record.get("material_type"), "MATERIAL_TYPE_REQUIRED")
    material_id = _text(record.get("material_id"), "MATERIAL_ID_REQUIRED")
    source_hash = _source_hash(record.get("source_hash"))
    version_no = _revision(record.get("version_no"))
    problem_ref = _text(
        record.get("canonical_problem_ref"), "INVALID_PROBLEM_REF", optional=True
    )
    if relation == "ENTRY":
        if material_type != entry_domain_spec.material_type:
            raise EntryContractError("ENTRY_MATERIAL_TYPE_MISMATCH")
        formal_type = entry_domain_spec.formal_source_type
        relation_ref = ""
    else:
        if material_type not in FORMAL_MATERIAL_TYPES:
            raise EntryContractError("RELATED_SOURCE_NOT_FORMAL")
        formal_type = FORMAL_MATERIAL_TYPES[material_type]
        link_status = _text(record.get("link_status"), "LINK_STATUS_REQUIRED")
        if link_status not in VALID_LINK_STATUSES:
            raise EntryContractError("SOURCE_RELATION_UNVERIFIED")
        relation_ref = _text(record.get("relation_ref"), "SOURCE_RELATION_REF_REQUIRED")
        relation = link_status
    domain_spec = WorkbenchSpec(
        entry_domain_spec.entry_type, material_type, formal_type,
        frozenset(ProblemDomain) if material_type == "ITR_CS" else
        frozenset({ProblemDomain.SOFTWARE}),
        None,
    )
    # Explicit source domain stays separate from the initiating workbench.
    domains = _domains(record.get("domains"), spec=domain_spec)
    return SourceSnapshot(
        material_type, material_id, source_hash, version_no, problem_ref,
        formal_type, relation, relation_ref, domains,
    )


def resolve_quality_scenario_entry(
    entry: Mapping[str, Any],
    related_sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a source *read plan*, never a quality-scenario candidate.

    Callers must obtain snapshots and linked sources from authorized trusted
    stores. The function does not prove authenticity of caller-supplied data.
    Only source references with explicit legacy LINKED or MANUAL_LINKED
    relations qualify; same title or same ITR text never counts as proof.
    """
    if not isinstance(entry, Mapping):
        raise EntryContractError("INVALID_ENTRY")
    try:
        workbench = EntryWorkbench(entry.get("workbench"))
    except (ValueError, TypeError):
        raise EntryContractError("UNSUPPORTED_ENTRY_WORKBENCH") from None
    spec = ENTRY_SPECS[workbench]
    source = _snapshot(entry, relation="ENTRY", entry_domain_spec=spec)
    domains = _domains(entry.get("domains"), spec=spec)
    # A software-only entry cannot request a hardware/mechanical scenario,
    # even if the underlying CS report discusses other domains.
    if source.domains and not set(source.domains).issubset(spec.allowed_domains):
        raise EntryContractError("DOMAIN_NOT_ALLOWED_FOR_WORKBENCH")
    if not isinstance(related_sources, (list, tuple)):
        raise EntryContractError("INVALID_RELATED_SOURCE_LIST")

    source_reads = [source] if source.formal_source_type is not None else []
    seen = {(source.material_type, source.material_id)}
    related: list[SourceSnapshot] = []
    for raw in related_sources:
        if not isinstance(raw, Mapping):
            raise EntryContractError("INVALID_RELATED_SOURCE")
        item = _snapshot(raw, relation="RELATED", entry_domain_spec=spec)
        identity = item.material_type, item.material_id
        if identity in seen:
            raise EntryContractError("DUPLICATE_SOURCE_REF")
        seen.add(identity)
        # Multiple distinct reports must not be grouped based on a string
        # similarity alone. Canonical refs must be resolved upstream.
        if not source.canonical_problem_ref or not item.canonical_problem_ref:
            raise EntryContractError("SOURCE_RELATION_UNVERIFIED")
        if item.canonical_problem_ref != source.canonical_problem_ref:
            raise EntryContractError("SOURCE_CONTEXT_MISMATCH")
        if domains and item.domains and not set(domains).intersection(item.domains):
            raise EntryContractError("RELATED_SOURCE_DOMAIN_MISMATCH")
        related.append(item)
        source_reads.append(item)

    source_types = {s.formal_source_type for s in source_reads}
    coverage = (
        "NONE" if not source_reads else
        "FULL" if len(source_types) == 2 else "PARTIAL"
    )
    # CS may contain any of three domains, possibly several at once, but
    # unknown must not be silently forced into software.
    status = (
        "DOMAIN_REVIEW_REQUIRED" if not domains else
        "FORMAL_SOURCE_REQUIRED" if not source_reads else
        "READY_FOR_SOURCE_READ"
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "entry_workbench": workbench.value,
        "entry_material": source.as_dict(),
        "entry_route_hint": spec.route_hint,
        "problem_domains": [domain.value for domain in domains],
        "domain_status": (
            "EXPLICIT" if entry.get("domains") else
            "WORKBENCH_POLICY" if workbench != EntryWorkbench.THOROUGH_SOLUTION
            else "UNCONFIRMED"
        ),
        "problem_ref_context": source.canonical_problem_ref,
        "related_materials": [s.as_dict() for s in related],
        "formal_source_reads": [s.as_dict() for s in source_reads],
        "source_coverage": coverage,
        "status": status,
        "can_extract_facts": status == "READY_FOR_SOURCE_READ",
        "candidate_created": False,
        "published": False,
    }


__all__ = [
    "CONTRACT_VERSION", "EntryWorkbench", "ProblemDomain",
    "FormalSourceType", "WorkbenchSpec", "ENTRY_SPECS",
    "EntryContractError", "SourceSnapshot", "resolve_quality_scenario_entry",
]
