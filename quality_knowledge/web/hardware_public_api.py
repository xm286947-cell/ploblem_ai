"""HTTP binding for the frozen hardware-public-consumer/v1 contract.

Cross-product consumers use this facade instead of Hardware repositories,
private databases, or maintainer APIs. Business semantics remain owned by the
existing Hardware backend/contract service.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from services.hardware_case_contract import HardwareCaseContractError
from services.hardware_public_consumer import HardwarePublicConsumer


PUBLIC_PREFIX = "/api/public/hardware/v1"


def _http_error(error: HardwareCaseContractError) -> HTTPException:
    code = error.code
    status = 404 if code in {"CASE_NOT_FOUND", "TREE_NODE_NOT_FOUND"} else 400
    return HTTPException(status_code=status, detail=code)


def create_hardware_public_router(service: Any) -> APIRouter:
    public = HardwarePublicConsumer(service)
    router = APIRouter(prefix=PUBLIC_PREFIX, tags=["hardware-public-v1"])

    @router.get("/cases")
    def search_cases(q: str = "", historical: bool = False) -> dict[str, Any]:
        try:
            return public.search(q, historical=historical)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/cases/{case_id}/mappings")
    def get_mappings(case_id: str, historical: bool = False) -> dict[str, Any]:
        try:
            return public.get_mappings(case_id, historical=historical)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/cases/{case_id}/evidence")
    def get_evidence(case_id: str, historical: bool = False) -> dict[str, Any]:
        try:
            return public.get_evidence(case_id, historical=historical)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/cases/{case_id}")
    def get_case(case_id: str, historical: bool = False) -> dict[str, Any]:
        try:
            return public.get_case(case_id, historical=historical)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/trees/{tree_type}")
    def get_tree(tree_type: str) -> dict[str, Any]:
        try:
            return public.get_tree(tree_type.upper())
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/tree-nodes/{node_id}/cases")
    def cases_by_tree_node(
        node_id: str, historical: bool = False
    ) -> dict[str, Any]:
        try:
            return public.cases_by_tree_node(node_id, historical=historical)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    return router


__all__ = ["PUBLIC_PREFIX", "create_hardware_public_router"]
