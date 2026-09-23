"""Persistent Backend Core for Hardware Case MVP (M2)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_contract import (
    HardwareCaseContractError,
    HardwareCaseContractService,
    MAPPING_STATUSES,
    RELATION_ROLES,
    REVIEW_DISPOSITIONS,
    TREE_TYPES,
)


class HardwareCaseBackendService:
    """Repository-backed implementation of the frozen hardware-case/v1 contract."""

    def __init__(self, repository: HardwareCaseRepository):
        self.repository = repository

    def _contract(self) -> HardwareCaseContractService:
        return HardwareCaseContractService(
            cases=self.repository.list_cases(),
            trees=self.repository.list_tree_nodes(),
            mappings=self.repository.list_mappings(),
            evidence=self.repository.list_evidence(),
        )

    def save_case(self, case: dict[str, Any]) -> dict[str, Any]:
        return self.repository.save_case(case)

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
        contract = self._contract()
        field = contract.review_case(
            case_id,
            field_name,
            disposition=disposition,
            confirmed_value=confirmed_value,
        )
        updated = contract.get_case(case_id, role="MAINTAINER")
        self.repository.save_case(updated)
        return field

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
        return self.repository.save_mapping(mapping)

    def add_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        case_id = str(evidence.get("case_id") or "")
        if self.repository.get_case(case_id) is None:
            raise HardwareCaseContractError("CASE_NOT_FOUND")
        return self.repository.save_evidence(evidence)

    def check_publish_gate(self, case_id: str) -> dict[str, Any]:
        return self._contract().check_publish_gate(case_id)

    def publish_case(self, case_id: str) -> dict[str, Any]:
        gate = self.check_publish_gate(case_id)
        if not gate["passed"]:
            return gate
        self.repository.set_case_status(case_id, "PUBLISHED")
        gate = deepcopy(gate)
        gate["case_status"] = "PUBLISHED"
        return gate

    def deprecate_case(self, case_id: str) -> dict[str, Any]:
        try:
            return self.repository.set_case_status(case_id, "DEPRECATED")
        except KeyError as exc:
            raise HardwareCaseContractError("CASE_NOT_FOUND") from exc

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

    def get_case(
        self,
        case_id: str,
        *,
        role: str = "CONSUMER",
        historical: bool = False,
    ) -> dict[str, Any]:
        return self._contract().get_case(
            case_id,
            role=role,
            historical=historical,
        )

    def list_cases_by_tree_node(
        self,
        node_id: str,
        *,
        role: str = "CONSUMER",
        historical: bool = False,
    ) -> dict[str, Any]:
        return self._contract().list_cases_by_tree_node(
            node_id,
            role=role,
            historical=historical,
        )

    def get_tree(self, tree_type: str) -> dict[str, Any]:
        return self._contract().get_tree(tree_type)

    def get_evidence(
        self,
        case_id: str,
        *,
        role: str = "CONSUMER",
        historical: bool = False,
    ) -> dict[str, Any]:
        return self._contract().get_evidence(
            case_id,
            role=role,
            historical=historical,
        )

    def maintenance_anomalies(self) -> list[dict[str, Any]]:
        return self.repository.list_anomalies(active_only=True)
