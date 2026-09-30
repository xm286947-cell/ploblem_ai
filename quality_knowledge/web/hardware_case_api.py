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
import os

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_contract import HardwareCaseContractError
from services.hardware_case_source_store import HardwareCaseSourceError, HardwareCaseSourceStore
from services.hardware_case_intake import HardwareCaseIntakeError, HardwareCaseIntakeService
from services.hardware_iam import HardwareIAM, HardwareIAMError
from services.hardware_observability import emit_event


_ALLOWED_ROLES = {"CONSUMER", "MAINTAINER"}


def _iam_http(error: HardwareIAMError) -> HTTPException:
    status = 401 if error.code.startswith("AUTHENTICATION_") else 403
    return HTTPException(status_code=status, detail=error.code)


def _principal(
    authorization: str | None,
    legacy_role: str | None,
    *,
    required_role: str,
) -> str:
    mode = os.environ.get("HARDWARE_IAM_MODE", "ENFORCED").strip().upper()
    if mode == "LEGACY_TEST":
        role = str(legacy_role or "CONSUMER").strip().upper()
        if role not in _ALLOWED_ROLES:
            raise HTTPException(status_code=403, detail="HARDWARE_CASE_ROLE_INVALID")
        if required_role == "MAINTAINER" and role != "MAINTAINER":
            raise HTTPException(status_code=403, detail="HARDWARE_CASE_MAINTAINER_REQUIRED")
        return role
    iam = HardwareIAM.from_environment()
    if not iam.configured:
        raise HTTPException(status_code=503, detail="HARDWARE_IAM_NOT_CONFIGURED")
    try:
        principal = iam.authorize(authorization, required_role=required_role)
    except HardwareIAMError as error:
        emit_event("hardware_access_denied", error_code=error.code)
        raise _iam_http(error) from error
    return principal.role


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
    source_store: HardwareCaseSourceStore | None = None,
    intake_service: HardwareCaseIntakeService | None = None,
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
        items = service.maintenance_anomalies()
        return {"items": items, "total": len(items)}

    if source_store is not None:
        @router.post("/sources/upload", status_code=201)
        async def upload_source(
            file: UploadFile = File(...),
            source_ref: str | None = Form(default=None),
            x_hardware_case_role: str | None = Header(
                default=None, alias="X-Hardware-Case-Role"
            ),
        ) -> dict[str, Any]:
            _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
            payload = await file.read()
            ref = str(source_ref or "").strip()
            if not ref:
                prefix_name = "word" if str(file.filename or "").lower().endswith(".docx") else "file"
                ref = f"{prefix_name}:{file.filename or 'source'}"
            try:
                return source_store.register_bytes(
                    ref,
                    str(file.filename or "source.bin"),
                    payload,
                    mime_type=file.content_type,
                )
            except HardwareCaseSourceError as error:
                code = error.code
                if code in {"SOURCE_REF_CONFLICT", "HASH_MISMATCH"}:
                    raise HTTPException(status_code=409, detail=code) from error
                if code in {"SOURCE_TOO_LARGE", "SOURCE_FILENAME_INVALID", "SOURCE_REF_REQUIRED"}:
                    raise HTTPException(status_code=400, detail=code) from error
                raise HTTPException(status_code=404, detail=code) from error

    if intake_service is not None:
        def _intake_error(error: Exception) -> HTTPException:
            code = getattr(error, "code", "INTAKE_FAILED")
            status = 404 if code == "INTAKE_NOT_FOUND" else 409 if code in {
                "INTAKE_ALREADY_PROCESSED", "INTAKE_PROCESSING", "ACTIVE_TREES_REQUIRED", "CASE_ALREADY_EXISTS", "SOURCE_REF_CONFLICT",
            } else 400
            return HTTPException(status_code=status, detail=code)

        @router.post("/intakes", status_code=201)
        async def upload_intake(
            file: UploadFile = File(...),
            x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role"),
        ) -> dict[str, Any]:
            _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
            try:
                return intake_service.upload(str(file.filename or ""), await file.read())
            except (HardwareCaseIntakeError, HardwareCaseSourceError) as error:
                raise _intake_error(error) from error

        @router.get("/intakes")
        def list_intakes(x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role"), authorization: str | None = Header(default=None, alias="Authorization")) -> dict[str, Any]:
            _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
            items = intake_service.list()
            return {"items": items, "total": len(items)}

        @router.get("/intakes/{intake_id}")
        def get_intake(intake_id: str, x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role"), authorization: str | None = Header(default=None, alias="Authorization")) -> dict[str, Any]:
            _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
            try:
                return intake_service.detail(intake_id)
            except HardwareCaseIntakeError as error:
                raise _intake_error(error) from error

        @router.post("/intakes/{intake_id}/process")
        def process_intake(intake_id: str, x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role"), authorization: str | None = Header(default=None, alias="Authorization")) -> dict[str, Any]:
            _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
            try:
                return intake_service.process(intake_id)
            except HardwareCaseIntakeError as error:
                raise _intake_error(error) from error

    @router.post("", status_code=201)
    def create_case(
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
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
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        _principal(authorization, x_hardware_case_role, required_role="MAINTAINER")
        try:
            return service.publish_case(case_id)
        except HardwareCaseContractError as error:
            raise _http_error(error) from error

    if source_store is not None:
        def _evidence_for_case(
            case_id: str,
            evidence_id: str,
            *,
            role: str,
            historical: bool,
        ) -> dict[str, Any]:
            try:
                payload = service.get_evidence(
                    case_id,
                    role=role,
                    historical=historical,
                )
            except HardwareCaseContractError as error:
                raise _http_error(error) from error
            for item in payload.get("evidence") or []:
                if item.get("evidence_id") == evidence_id:
                    return item
            raise HTTPException(status_code=404, detail="EVIDENCE_NOT_FOUND")

        def _source_http(error: HardwareCaseSourceError) -> HTTPException:
            if error.code in {"SOURCE_NOT_REGISTERED", "SOURCE_UNAVAILABLE", "SOURCE_LOCATOR_NOT_FOUND"}:
                return HTTPException(status_code=404, detail=error.code)
            if error.code in {"HASH_MISMATCH", "SOURCE_REF_CONFLICT"}:
                return HTTPException(status_code=409, detail=error.code)
            return HTTPException(status_code=400, detail=error.code)

        @router.get("/{case_id}/evidence/{evidence_id}/source-meta")
        def evidence_source_meta(
            case_id: str,
            evidence_id: str,
            historical: bool = False,
            x_hardware_case_role: str | None = Header(
                default=None, alias="X-Hardware-Case-Role"
            ),
        ) -> dict[str, Any]:
            role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
            evidence = _evidence_for_case(
                case_id,
                evidence_id,
                role=role,
                historical=historical,
            )
            try:
                meta = source_store.get_metadata(str(evidence.get("source_ref") or ""))
            except HardwareCaseSourceError as error:
                raise _source_http(error) from error
            return {
                "case_id": case_id,
                "evidence_id": evidence_id,
                "evidence_status": evidence.get("evidence_status"),
                "source": meta,
            }

        @router.get("/{case_id}/evidence/{evidence_id}/source-preview")
        def evidence_source_preview(
            case_id: str,
            evidence_id: str,
            historical: bool = False,
            context_blocks: int = Query(default=1, ge=0, le=3),
            x_hardware_case_role: str | None = Header(
                default=None, alias="X-Hardware-Case-Role"
            ),
        ) -> dict[str, Any]:
            role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
            evidence = _evidence_for_case(
                case_id,
                evidence_id,
                role=role,
                historical=historical,
            )
            try:
                preview = source_store.preview(
                    str(evidence.get("source_ref") or ""),
                    dict(evidence.get("locator") or {}),
                    context_blocks=context_blocks,
                )
            except HardwareCaseSourceError as error:
                raise _source_http(error) from error
            return {
                "case_id": case_id,
                "evidence_id": evidence_id,
                "evidence_type": evidence.get("evidence_type"),
                "locator": evidence.get("locator") or {},
                **preview,
            }

        @router.get("/{case_id}/evidence/{evidence_id}/source-file")
        def evidence_source_file(
            case_id: str,
            evidence_id: str,
            historical: bool = False,
            x_hardware_case_role: str | None = Header(
                default=None, alias="X-Hardware-Case-Role"
            ),
        ) -> FileResponse:
            role = _principal(authorization, x_hardware_case_role, required_role="CONSUMER")
            evidence = _evidence_for_case(
                case_id,
                evidence_id,
                role=role,
                historical=historical,
            )
            source_ref = str(evidence.get("source_ref") or "")
            try:
                meta = source_store.get_metadata(source_ref)
                path = source_store.resolve_path(source_ref)
            except HardwareCaseSourceError as error:
                raise _source_http(error) from error
            return FileResponse(
                path=path,
                media_type=str(meta.get("mime_type") or "application/octet-stream"),
                filename=str(meta.get("display_name") or "source"),
            )

    return router
