"""Hardware Case MVP API mounted on the existing /api/v2 surface.

This module is deliberately thin:
- business semantics stay in HardwareCaseBackendService / hardware-case/v1;
- no second web server or port is created;
- CONSUMER is the default read role;
- maintenance mutations require an explicit MAINTAINER role supplied by the
  host platform.  The header fallback exists for current internal integration
  and can later be replaced by the platform auth resolver without changing the
  API contract.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_contract import HardwareCaseContractError


_ALLOWED_ROLES = {"CONSUMER", "MAINTAINER"}


def _role(value: str | None) -> str:
    role = str(value or "CONSUMER").strip().upper()
    if role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="HARDWARE_CASE_ROLE_INVALID")
    return role


def _require_maintainer(value: str | None) -> str:
    role = _role(value)
    if role != "MAINTAINER":
        raise HTTPException(status_code=403, detail="HARDWARE_CASE_MAINTAINER_REQUIRED")
    return role


def _http_error(error: Exception) -> HTTPException:
    code = getattr(error, "code", str(error))
    if code in {"CASE_NOT_FOUND", "TREE_NODE_NOT_FOUND"}:
        return HTTPException(status_code=404, detail=code)
    if code in {
        "PUBLISH_GATE_REQUIRED",
        "TREE_TYPE_MISMATCH",
        "NO_VALID_EVIDENCE",
        "NO_CONFIRMED_MAPPING",
        "CORE_FACTS_NOT_REVIEWED",
    }:
        return HTTPException(status_code=409, detail=code)
    return HTTPException(status_code=400, detail=code)


def create_hardware_case_router(
    service: HardwareCaseBackendService,
    *,
    prefix: str = "/api/v2/hardware-cases",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["hardware-case"])

    @router.get("")
    def search_cases(
        q: str = "",
        historical: bool = False,
        status: list[str] | None = Query(default=None),
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        role = _role(x_hardware_case_role)
        try:
            return service.search_cases(
                q,
                role=role,
                statuses=status,
                historical=historical,
            )
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/trees/{tree_type}")
    def get_tree(tree_type: str) -> dict[str, Any]:
        try:
            return service.get_tree(tree_type.upper())
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.post("/trees/nodes", status_code=201)
    def save_tree_node(
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.save_tree_node(payload)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/tree-nodes/{node_id}/cases")
    def cases_by_tree_node(
        node_id: str,
        historical: bool = False,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        role = _role(x_hardware_case_role)
        try:
            return service.list_cases_by_tree_node(
                node_id,
                role=role,
                historical=historical,
            )
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/maintenance/anomalies")
    def maintenance_anomalies(
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        items = service.maintenance_anomalies()
        return {"items": items, "total": len(items)}

    @router.post("", status_code=201)
    def create_case(
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.create_case(payload)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/{case_id}")
    def get_case(
        case_id: str,
        historical: bool = False,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        role = _role(x_hardware_case_role)
        try:
            return service.get_case(
                case_id,
                role=role,
                historical=historical,
            )
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/{case_id}/mappings")
    def get_case_mappings(
        case_id: str,
        historical: bool = False,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        role = _role(x_hardware_case_role)
        try:
            return service.get_mappings(
                case_id,
                role=role,
                historical=historical,
            )
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/{case_id}/evidence")
    def get_evidence(
        case_id: str,
        historical: bool = False,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        role = _role(x_hardware_case_role)
        try:
            return service.get_evidence(
                case_id,
                role=role,
                historical=historical,
            )
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.post("/{case_id}/review")
    def review_case(
        case_id: str,
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.review_case(
                case_id,
                str(payload.get("field_name") or ""),
                disposition=str(payload.get("disposition") or ""),
                confirmed_value=payload.get("confirmed_value"),
            )
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.post("/{case_id}/mappings", status_code=201)
    def set_mapping(
        case_id: str,
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        normalized = dict(payload)
        normalized["case_id"] = case_id
        try:
            return service.set_mapping(normalized)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.post("/{case_id}/evidence", status_code=201)
    def save_evidence(
        case_id: str,
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        normalized = dict(payload)
        normalized["case_id"] = case_id
        try:
            return service.save_evidence(normalized)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.get("/{case_id}/publish-gate")
    def publish_gate(
        case_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.check_publish_gate(case_id)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    @router.post("/{case_id}/publish")
    def publish_case(
        case_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            return service.publish_case(case_id)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    return router
