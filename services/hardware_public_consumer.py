"""Frozen public read boundary for cross-product Hardware consumers.

This facade intentionally delegates to the existing Hardware Case consumer
contract. It owns no persistence, Runtime, Knowledge, UI, or Overall logic.
"""
from __future__ import annotations
from typing import Any
from services.hardware_case_contract import HardwareCaseContractService

PUBLIC_CONTRACT_VERSION = "hardware-public-consumer/v1"

class HardwarePublicConsumer:
    def __init__(self, service: HardwareCaseContractService) -> None:
        self._service = service

    def _result(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "public_contract_version": PUBLIC_CONTRACT_VERSION,
            "operation": operation,
            "payload": payload,
        }

    def search(self, query: str = "", *, historical: bool = False) -> dict[str, Any]:
        return self._result("SEARCH", self._service.search_cases(query, role="CONSUMER", historical=historical))

    def get_case(self, case_id: str, *, historical: bool = False) -> dict[str, Any]:
        return self._result("GET_CASE", self._service.get_case(case_id, role="CONSUMER", historical=historical))

    def get_tree(self, tree_type: str) -> dict[str, Any]:
        return self._result("GET_TREE", self._service.get_tree(tree_type))

    def cases_by_tree_node(self, node_id: str, *, historical: bool = False) -> dict[str, Any]:
        return self._result("CASES_BY_TREE_NODE", self._service.list_cases_by_tree_node(node_id, role="CONSUMER", historical=historical))

    def get_mappings(self, case_id: str, *, historical: bool = False) -> dict[str, Any]:
        return self._result("GET_MAPPINGS", self._service.get_mappings(case_id, role="CONSUMER", historical=historical))

    def get_evidence(self, case_id: str, *, historical: bool = False) -> dict[str, Any]:
        return self._result("GET_EVIDENCE", self._service.get_evidence(case_id, role="CONSUMER", historical=historical))
