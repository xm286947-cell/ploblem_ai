"""Hardware Case MVP V0.1 contract boundary.

M1 freezes business semantics and testable behavior only.  Persistence, Word
parsing, prompt/skill internals, and physical tree construction stay outside
this module until later implementation gates.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


CONTRACT_VERSION = "hardware-case/v1"

CASE_STATUSES = frozenset(
    {
        "PENDING_ANALYSIS",
        "ANALYZING",
        "PENDING_REVIEW",
        "CONFIRMED",
        "PUBLISHED",
        "DEPRECATED",
    }
)
PROCESSING_STATUSES = frozenset({"READY", "STRUCTURE_EXTRACTION_FAILED"})
MAPPING_STATES = frozenset({"UNMAPPED", "SUGGESTED", "CONFIRMED"})
MAPPING_STATUSES = frozenset({"SUGGESTED", "CONFIRMED"})
TREE_TYPES = frozenset({"CIRCUIT_FEATURE", "MATERIAL_DEVICE"})
RELATION_ROLES = frozenset({"PRIMARY", "SECONDARY"})
REVIEW_DISPOSITIONS = frozenset(
    {"UNREVIEWED", "CONFIRMED", "CONFIRMED_UNKNOWN", "DEFERRED"}
)
EVIDENCE_STATUSES = frozenset({"AVAILABLE", "SOURCE_UNAVAILABLE"})
CORE_FACTS = ("symptom", "root_cause", "actions")

BLOCK_CORE_FACTS = "CORE_FACTS_NOT_REVIEWED"
BLOCK_EVIDENCE = "NO_VALID_EVIDENCE"
BLOCK_MAPPING = "NO_CONFIRMED_MAPPING"


class HardwareCaseContractError(RuntimeError):
    """Stable contract error with a machine-readable code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _case_id(case: dict[str, Any]) -> str:
    value = _text(case.get("case_id"))
    if not value:
        raise HardwareCaseContractError("CASE_CONTRACT_INVALID")
    return value


def effective_fact(field: dict[str, Any] | None) -> Any:
    """Return only human-confirmed knowledge to consumer surfaces."""
    if not isinstance(field, dict):
        return None
    disposition = field.get("review_disposition")
    if disposition == "CONFIRMED":
        return field.get("confirmed_value")
    if disposition == "CONFIRMED_UNKNOWN":
        return None
    return None


def core_fact_review_complete(case: dict[str, Any]) -> bool:
    facts = case.get("facts")
    if not isinstance(facts, dict):
        return False
    for name in CORE_FACTS:
        field = facts.get(name)
        if not isinstance(field, dict):
            return False
        if field.get("review_disposition") not in {"CONFIRMED", "CONFIRMED_UNKNOWN"}:
            return False
    return True


def mapping_state(
    mappings: Iterable[dict[str, Any]], case_id: str, tree_type: str
) -> str:
    states = [
        item.get("mapping_status")
        for item in mappings
        if item.get("case_id") == case_id and item.get("tree_type") == tree_type
    ]
    if "CONFIRMED" in states:
        return "CONFIRMED"
    if "SUGGESTED" in states:
        return "SUGGESTED"
    return "UNMAPPED"


def publish_gate(
    case: dict[str, Any],
    mappings: Iterable[dict[str, Any]],
    evidence: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    cid = _case_id(case)
    blockers: list[str] = []
    if not core_fact_review_complete(case):
        blockers.append(BLOCK_CORE_FACTS)
    if not any(
        item.get("case_id") == cid and item.get("evidence_status") == "AVAILABLE"
        for item in evidence
    ):
        blockers.append(BLOCK_EVIDENCE)
    circuit = mapping_state(mappings, cid, "CIRCUIT_FEATURE")
    material = mapping_state(mappings, cid, "MATERIAL_DEVICE")
    if circuit != "CONFIRMED" and material != "CONFIRMED":
        blockers.append(BLOCK_MAPPING)
    return {
        "contract_version": CONTRACT_VERSION,
        "case_id": cid,
        "passed": not blockers,
        "blockers": blockers,
        "core_fact_review_complete": BLOCK_CORE_FACTS not in blockers,
        "valid_evidence_available": BLOCK_EVIDENCE not in blockers,
        "confirmed_mapping_available": BLOCK_MAPPING not in blockers,
        "circuit_mapping_state": circuit,
        "material_mapping_state": material,
    }


class HardwareCaseContractService:
    """Small in-memory M1 reference implementation for contract tests.

    M2 may replace the backing store, but observable behavior in this class is
    the frozen consumer/reviewer contract.
    """

    def __init__(
        self,
        *,
        cases: Iterable[dict[str, Any]] = (),
        trees: Iterable[dict[str, Any]] = (),
        mappings: Iterable[dict[str, Any]] = (),
        evidence: Iterable[dict[str, Any]] = (),
    ) -> None:
        self.cases = {_case_id(item): deepcopy(item) for item in cases}
        self.trees = [deepcopy(item) for item in trees]
        self.mappings = [deepcopy(item) for item in mappings]
        self.evidence = [deepcopy(item) for item in evidence]

    def _case(self, case_id: str) -> dict[str, Any]:
        case = self.cases.get(case_id)
        if case is None:
            raise HardwareCaseContractError("CASE_NOT_FOUND")
        return case

    def get_tree(self, tree_type: str) -> dict[str, Any]:
        if tree_type not in TREE_TYPES:
            raise HardwareCaseContractError("TREE_TYPE_INVALID")
        return {
            "contract_version": CONTRACT_VERSION,
            "tree_type": tree_type,
            "nodes": [
                deepcopy(node)
                for node in self.trees
                if node.get("tree_type") == tree_type and node.get("active", True)
            ],
        }

    def _consumer_visible(
        self, case: dict[str, Any], *, historical: bool = False
    ) -> bool:
        status = case.get("case_status")
        if status == "PUBLISHED":
            return True
        return historical and status == "DEPRECATED"

    def _projection(self, case: dict[str, Any]) -> dict[str, Any]:
        cid = _case_id(case)
        facts = case.get("facts") if isinstance(case.get("facts"), dict) else {}
        evidence = [
            deepcopy(item) for item in self.evidence if item.get("case_id") == cid
        ]
        evidence_health = "AVAILABLE"
        if not evidence:
            evidence_health = "EVIDENCE_MISSING"
        elif not any(item.get("evidence_status") == "AVAILABLE" for item in evidence):
            evidence_health = "SOURCE_UNAVAILABLE"
        return {
            "contract_version": CONTRACT_VERSION,
            "case_id": cid,
            "title": case.get("title"),
            "case_status": case.get("case_status"),
            "processing_status": case.get("processing_status", "READY"),
            "product_context": deepcopy(case.get("product_context") or {}),
            "facts": {name: effective_fact(field) for name, field in facts.items()},
            "circuit_mapping_state": mapping_state(
                self.mappings, cid, "CIRCUIT_FEATURE"
            ),
            "material_mapping_state": mapping_state(
                self.mappings, cid, "MATERIAL_DEVICE"
            ),
            "evidence_health": evidence_health,
            "evidence_warning": (
                "SOURCE_UNAVAILABLE" if evidence_health == "SOURCE_UNAVAILABLE" else None
            ),
        }

    def get_case(
        self, case_id: str, *, role: str = "CONSUMER", historical: bool = False
    ) -> dict[str, Any]:
        case = self._case(case_id)
        if role == "CONSUMER" and not self._consumer_visible(case, historical=historical):
            raise HardwareCaseContractError("CASE_NOT_FOUND")
        if role not in {"CONSUMER", "MAINTAINER"}:
            raise HardwareCaseContractError("ROLE_INVALID")
        return self._projection(case) if role == "CONSUMER" else deepcopy(case)

    def get_evidence(
        self, case_id: str, *, role: str = "CONSUMER", historical: bool = False
    ) -> dict[str, Any]:
        self.get_case(case_id, role=role, historical=historical)
        return {
            "contract_version": CONTRACT_VERSION,
            "case_id": case_id,
            "evidence": [
                deepcopy(item)
                for item in self.evidence
                if item.get("case_id") == case_id
            ],
        }

    def search_cases(
        self,
        query: str = "",
        *,
        role: str = "CONSUMER",
        statuses: Iterable[str] | None = None,
        historical: bool = False,
    ) -> dict[str, Any]:
        if role not in {"CONSUMER", "MAINTAINER"}:
            raise HardwareCaseContractError("ROLE_INVALID")
        requested = set(statuses or ())
        needle = _text(query).lower()
        results: list[dict[str, Any]] = []
        for case in self.cases.values():
            status = case.get("case_status")
            if role == "CONSUMER":
                if not self._consumer_visible(case, historical=historical):
                    continue
            elif requested and status not in requested:
                continue

            projection = self._projection(case)
            mapping_paths = [
                _text(item.get("node_path"))
                for item in self.mappings
                if item.get("case_id") == case.get("case_id")
            ]
            if role == "MAINTAINER":
                raw_facts = case.get("facts") if isinstance(case.get("facts"), dict) else {}
                fact_values: list[str] = []
                for field in raw_facts.values():
                    if not isinstance(field, dict):
                        continue
                    fact_values.extend(
                        [
                            _text(field.get("candidate_value")),
                            _text(field.get("confirmed_value")),
                        ]
                    )
            else:
                fact_values = [_text(value) for value in projection["facts"].values()]
            searchable = [
                _text(case.get("title")),
                *fact_values,
                *mapping_paths,
                *[_text(value) for value in (case.get("product_context") or {}).values()],
            ]
            if needle and not any(needle in value.lower() for value in searchable if value):
                continue
            results.append(projection if role == "CONSUMER" else deepcopy(case))
        return {
            "contract_version": CONTRACT_VERSION,
            "results": results,
        }

    def list_cases_by_tree_node(
        self,
        node_id: str,
        *,
        role: str = "CONSUMER",
        historical: bool = False,
    ) -> dict[str, Any]:
        case_ids = {
            item.get("case_id")
            for item in self.mappings
            if item.get("node_id") == node_id and item.get("mapping_status") == "CONFIRMED"
        }
        results = []
        for cid in case_ids:
            case = self.cases.get(str(cid))
            if not case:
                continue
            if role == "CONSUMER" and not self._consumer_visible(
                case, historical=historical
            ):
                continue
            results.append(self._projection(case) if role == "CONSUMER" else deepcopy(case))
        return {
            "contract_version": CONTRACT_VERSION,
            "node_id": node_id,
            "case_count": len(results),
            "results": results,
        }

    def review_case(
        self,
        case_id: str,
        field_name: str,
        *,
        disposition: str,
        confirmed_value: Any = None,
    ) -> dict[str, Any]:
        if disposition not in REVIEW_DISPOSITIONS:
            raise HardwareCaseContractError("REVIEW_DISPOSITION_INVALID")
        case = self._case(case_id)
        facts = case.setdefault("facts", {})
        field = facts.setdefault(
            field_name,
            {
                "candidate_value": None,
                "confirmed_value": None,
                "review_disposition": "UNREVIEWED",
                "evidence_refs": [],
            },
        )
        field["review_disposition"] = disposition
        field["confirmed_value"] = confirmed_value if disposition == "CONFIRMED" else None
        return deepcopy(field)

    def set_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if mapping.get("tree_type") not in TREE_TYPES:
            raise HardwareCaseContractError("TREE_TYPE_INVALID")
        if mapping.get("mapping_status") not in MAPPING_STATUSES:
            raise HardwareCaseContractError("MAPPING_STATUS_INVALID")
        if mapping.get("relation_role") not in RELATION_ROLES:
            raise HardwareCaseContractError("RELATION_ROLE_INVALID")
        cid = _text(mapping.get("case_id"))
        self._case(cid)
        if mapping.get("relation_role") == "PRIMARY":
            for item in self.mappings:
                if (
                    item.get("case_id") == cid
                    and item.get("tree_type") == mapping.get("tree_type")
                    and item.get("relation_role") == "PRIMARY"
                ):
                    item["relation_role"] = "SECONDARY"
        self.mappings.append(deepcopy(mapping))
        return deepcopy(mapping)

    def check_publish_gate(self, case_id: str) -> dict[str, Any]:
        return publish_gate(self._case(case_id), self.mappings, self.evidence)

    def publish_case(self, case_id: str) -> dict[str, Any]:
        gate = self.check_publish_gate(case_id)
        if not gate["passed"]:
            return gate
        case = self._case(case_id)
        case["case_status"] = "PUBLISHED"
        gate["case_status"] = "PUBLISHED"
        return gate

    def maintenance_anomalies(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for case in self.cases.values():
            if case.get("case_status") != "PUBLISHED":
                continue
            cid = _case_id(case)
            case_evidence = [
                item for item in self.evidence if item.get("case_id") == cid
            ]
            if case_evidence and not any(
                item.get("evidence_status") == "AVAILABLE" for item in case_evidence
            ):
                result.append({"case_id": cid, "code": "SOURCE_UNAVAILABLE"})
        return result
