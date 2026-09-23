"""Backend Core service for Hardware Case MVP V0.1.

The service persists through HardwareCaseRepository and delegates frozen
consumer/review/publish semantics to hardware-case/v1.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_contract import (
    CASE_STATUSES,
    EVIDENCE_STATUSES,
    MAPPING_STATUSES,
    PROCESSING_STATUSES,
    RELATION_ROLES,
    REVIEW_DISPOSITIONS,
    TREE_TYPES,
    HardwareCaseContractError,
    HardwareCaseContractService,
)


class HardwareCaseBackendService:
    def __init__(self, repository: HardwareCaseRepository):
        self.repository = repository

    def _contract(self) -> HardwareCaseContractService:
        return HardwareCaseContractService(
            cases=self.repository.list_cases(),
            trees=self.repository.list_tree_nodes(),
            mappings=self.repository.list_mappings(),
            evidence=self.repository.list_evidence(),
        )

    def create_case(self, case: dict[str, Any]) -> dict[str, Any]:
        status = case.get("case_status", "PENDING_ANALYSIS")
        processing = case.get("processing_status", "READY")
        if status not in CASE_STATUSES:
            raise HardwareCaseContractError("CASE_STATUS_INVALID")
        if processing not in PROCESSING_STATUSES:
            raise HardwareCaseContractError("PROCESSING_STATUS_INVALID")
        return self.repository.save_case(case)

    def get_case(
        self, case_id: str, *, role: str = "CONSUMER", historical: bool = False
    ) -> dict[str, Any]:
        return self._contract().get_case(
            case_id, role=role, historical=historical
        )

    def search_cases(
        self,
        query: str = "",
        *,
        role: str = "CONSUMER",
        statuses: Iterable[str] | None = None,
        historical: bool = False,
    ) -> dict[str, Any]:
        return self._contract().search_cases(
            query,
            role=role,
            statuses=statuses,
            historical=historical,
        )

    def get_tree(self, tree_type: str) -> dict[str, Any]:
        return self._contract().get_tree(tree_type)

    def list_cases_by_tree_node(
        self,
        node_id: str,
        *,
        role: str = "CONSUMER",
        historical: bool = False,
    ) -> dict[str, Any]:
        return self._contract().list_cases_by_tree_node(
            node_id, role=role, historical=historical
        )

    def get_evidence(
        self, case_id: str, *, role: str = "CONSUMER", historical: bool = False
    ) -> dict[str, Any]:
        return self._contract().get_evidence(
            case_id, role=role, historical=historical
        )

    def save_tree_node(self, node: dict[str, Any]) -> dict[str, Any]:
        if node.get("tree_type") not in TREE_TYPES:
            raise HardwareCaseContractError("TREE_TYPE_INVALID")
        path = node.get("path")
        if not isinstance(path, list) or not path:
            raise HardwareCaseContractError("TREE_PATH_INVALID")
        return self.repository.save_tree_node(node)

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
        if self.repository.get_case(case_id) is None:
            raise HardwareCaseContractError("CASE_NOT_FOUND")
        return self.repository.review_fact(
            case_id,
            field_name,
            disposition=disposition,
            confirmed_value=confirmed_value,
        )

    def set_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if mapping.get("tree_type") not in TREE_TYPES:
            raise HardwareCaseContractError("TREE_TYPE_INVALID")
        if mapping.get("mapping_status") not in MAPPING_STATUSES:
            raise HardwareCaseContractError("MAPPING_STATUS_INVALID")
        if mapping.get("relation_role") not in RELATION_ROLES:
            raise HardwareCaseContractError("RELATION_ROLE_INVALID")
        case_id = str(mapping.get("case_id") or "")
        if self.repository.get_case(case_id) is None:
            raise HardwareCaseContractError("CASE_NOT_FOUND")
        node = self.repository.get_tree_node(str(mapping.get("node_id") or ""))
        if node is None:
            raise HardwareCaseContractError("TREE_NODE_NOT_FOUND")
        if node["tree_type"] != mapping.get("tree_type"):
            raise HardwareCaseContractError("TREE_TYPE_MISMATCH")
        normalized = deepcopy(mapping)
        normalized.setdefault("node_path", "/".join(node["path"]))
        return self.repository.save_mapping(normalized)

    def save_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        if evidence.get("evidence_status") not in EVIDENCE_STATUSES:
            raise HardwareCaseContractError("EVIDENCE_STATUS_INVALID")
        if self.repository.get_case(str(evidence.get("case_id") or "")) is None:
            raise HardwareCaseContractError("CASE_NOT_FOUND")
        locator = evidence.get("locator")
        if not isinstance(locator, dict) or not locator:
            raise HardwareCaseContractError("EVIDENCE_LOCATOR_INVALID")
        return self.repository.save_evidence(evidence)

    def set_processing_status(self, case_id: str, status: str) -> dict[str, Any]:
        if status not in PROCESSING_STATUSES:
            raise HardwareCaseContractError("PROCESSING_STATUS_INVALID")
        try:
            self.repository.update_processing_status(case_id, status)
        except KeyError as exc:
            raise HardwareCaseContractError("CASE_NOT_FOUND") from exc
        return self.repository.get_case(case_id) or {}

    def set_case_status(self, case_id: str, status: str) -> dict[str, Any]:
        """Maintenance-only non-publish transition.

        PUBLISHED must always go through publish_case so the backend gate cannot
        be bypassed.
        """
        if status not in CASE_STATUSES:
            raise HardwareCaseContractError("CASE_STATUS_INVALID")
        if status == "PUBLISHED":
            raise HardwareCaseContractError("PUBLISH_GATE_REQUIRED")
        try:
            self.repository.update_case_status(case_id, status)
        except KeyError as exc:
            raise HardwareCaseContractError("CASE_NOT_FOUND") from exc
        return self.repository.get_case(case_id) or {}

    def check_publish_gate(self, case_id: str) -> dict[str, Any]:
        return self._contract().check_publish_gate(case_id)

    def publish_case(self, case_id: str) -> dict[str, Any]:
        gate = self.check_publish_gate(case_id)
        if not gate["passed"]:
            return gate
        try:
            self.repository.update_case_status(case_id, "PUBLISHED")
        except KeyError as exc:
            raise HardwareCaseContractError("CASE_NOT_FOUND") from exc
        result = dict(gate)
        result["case_status"] = "PUBLISHED"
        return result

    def maintenance_anomalies(self) -> list[dict[str, str]]:
        return self._contract().maintenance_anomalies()
