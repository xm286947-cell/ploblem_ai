"""P07 backend API for HC-TREE-IMPORT-001 on the existing /api/v2 app.

This router exposes the product import workflow without creating a second Web
server, port, auth system, or Runtime. Uploaded Excel files remain in the
company-local staging directory and server paths are never returned.
"""
from __future__ import annotations

from hashlib import sha256
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from repositories.hardware_maintenance_audit_repository import HardwareMaintenanceAuditRepository
from repositories.hardware_tree_import_repository import HardwareTreeImportRepository
from services.hardware_tree_excel import HardwareTreeImportAnalyzer
from services.hardware_tree_import_contract import HardwareTreeImportContractError
from services.hardware_tree_import_files import HardwareTreeImportFileStore


def _configured_host_role(host_role: str | None) -> str:
    role = str(host_role or "CONSUMER").strip().upper()
    if role not in {"CONSUMER", "MAINTAINER"}:
        raise HTTPException(status_code=500, detail="HARDWARE_CASE_HOST_ROLE_INVALID")
    return role


def _require_maintainer(value: str | None, *, host_role: str | None = None) -> None:
    if _configured_host_role(host_role) != "MAINTAINER":
        raise HTTPException(status_code=403, detail="HARDWARE_CASE_MAINTAINER_REQUIRED")
    if value is not None and str(value).strip():
        claim = str(value).strip().upper()
        if claim not in {"CONSUMER", "MAINTAINER"}:
            raise HTTPException(status_code=403, detail="HARDWARE_CASE_ROLE_INVALID")


def _trusted_actor(host_role: str | None) -> str:
    actor = os.getenv("HARDWARE_CASE_HOST_ACTOR", "").strip()
    if actor:
        return actor
    return (
        "server:hardware-maintainer"
        if _configured_host_role(host_role) == "MAINTAINER"
        else "server:hardware-consumer"
    )


def _http_error(error: Exception) -> HTTPException:
    code = getattr(error, "code", str(error))
    if code in {
        "IMPORT_JOB_NOT_FOUND",
        "CHANGE_NOT_FOUND",
        "VALIDATION_ISSUE_NOT_FOUND",
        "STAGED_EXCEL_NOT_FOUND",
    }:
        return HTTPException(status_code=404, detail=code)
    if code == "EXCEL_FILE_TOO_LARGE":
        return HTTPException(status_code=413, detail=code)
    if code in {
        "IMPORT_NOT_UPLOADED",
        "IMPORT_NOT_IN_REVIEW",
        "IMPORT_NOT_READY_TO_APPLY",
        "UNRESOLVED_VALIDATION_ISSUES",
        "UNRESOLVED_CONFLICT",
        "CHANGE_REVIEW_INCOMPLETE",
        "SOURCE_FILENAME_MISMATCH",
        "SOURCE_HASH_MISMATCH",
        "IMPORT_STATUS_TRANSITION_INVALID",
    }:
        return HTTPException(status_code=409, detail=code)
    return HTTPException(status_code=400, detail=code)


def create_hardware_tree_import_router(
    repository: HardwareTreeImportRepository,
    file_store: HardwareTreeImportFileStore,
    *,
    prefix: str = "/api/v2/hardware-cases/tree-imports",
    host_role: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["hardware-tree-import"])
    analyzer = HardwareTreeImportAnalyzer(repository)
    audit = HardwareMaintenanceAuditRepository(repository.db_path)
    trusted_actor = _trusted_actor(host_role)

    def require_maintainer(value: str | None) -> None:
        _require_maintainer(value, host_role=host_role)

    @router.post("", status_code=201)
    async def upload_excel(
        tree_type: str = Form(...),
        file: UploadFile = File(...),
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
        x_hardware_case_operator: str | None = Header(
            default=None, alias="X-Hardware-Case-Operator"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        # Operator identity is trusted server context.  The legacy client
        # header remains accepted for compatibility but never becomes authority.
        operator = trusted_actor
        filename = str(file.filename or "").strip()
        payload = await file.read()
        job_id = "HTI-" + uuid4().hex.upper()
        staged = None
        try:
            staged = file_store.save(job_id, filename, payload)
            workbook = analyzer.inspect(staged)
            job = repository.create_job(
                job_id=job_id,
                tree_type=tree_type,
                source_filename=filename,
                source_sha256=sha256(payload).hexdigest(),
                operator=operator,
            )
            return {"job": job, "workbook": workbook}
        except HardwareTreeImportContractError as error:
            if staged is not None:
                file_store.delete(job_id, filename)
            raise _http_error(error) from error

    @router.get("")
    def list_imports(
        tree_type: str | None = None,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            items = repository.list_jobs(tree_type)
            return {"items": items, "total": len(items)}
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.get("/versions/{tree_type}")
    def list_versions(
        tree_type: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            items = repository.list_versions(tree_type)
            return {"tree_type": tree_type.upper(), "items": items, "total": len(items)}
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.get("/active-version/{tree_type}")
    def active_version(
        tree_type: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            version = repository.get_active_version(tree_type)
            return {"tree_type": tree_type.upper(), "active_version": version}
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.post("/{job_id}/workbook-preview")
    def workbook_preview(
        job_id: str,
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            job = repository.get_job(job_id)
            staged = file_store.get(job_id, job["source_filename"])
            return analyzer.preview_rows(
                staged,
                sheet_name=str(payload.get("sheet_name") or ""),
                header_row=int(payload.get("header_row") or 0),
                max_rows=int(payload.get("max_rows") or 20),
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="HEADER_ROW_INVALID") from error
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.get("/{job_id}")
    def get_import(
        job_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            return {
                "job": repository.get_job(job_id),
                "changes": repository.list_changes(job_id),
                "issues": repository.list_issues(job_id),
            }
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.post("/{job_id}/analyze")
    def analyze_import(
        job_id: str,
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            job = repository.get_job(job_id)
            staged = file_store.get(job_id, job["source_filename"])
            return analyzer.analyze(job_id, staged, payload)
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.post("/{job_id}/changes/{change_id}/decision")
    def decide_change(
        job_id: str,
        change_id: str,
        payload: dict[str, Any],
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            change = repository.get_change(change_id)
            if change["job_id"] != job_id:
                raise HardwareTreeImportContractError("CHANGE_NOT_FOUND")
            return repository.set_change_decision(
                change_id,
                str(payload.get("decision") or ""),
                resolved_after=payload.get("resolved_after"),
            )
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.post("/{job_id}/issues/{issue_id}/resolve")
    def resolve_issue(
        job_id: str,
        issue_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            issue = repository.get_issue(issue_id)
            if issue["job_id"] != job_id:
                raise HardwareTreeImportContractError("VALIDATION_ISSUE_NOT_FOUND")
            return repository.resolve_issue(issue_id)
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.post("/{job_id}/ready")
    def ready_to_apply(
        job_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            return repository.mark_ready_to_apply(job_id)
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.post("/{job_id}/apply")
    def apply_import(
        job_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        require_maintainer(x_hardware_case_role)
        try:
            before = repository.get_job(job_id)
            changes = repository.list_changes(job_id)
            result = repository.apply_job(job_id)
            audit.record(
                actor=trusted_actor,
                action="TREE_APPLY",
                target_type="HARDWARE_TREE_IMPORT",
                target_id=job_id,
                details={
                    "tree_type": before.get("tree_type"),
                    "source_filename": before.get("source_filename"),
                    "applied_version_id": result["job"].get("applied_version_id"),
                    "status": result["job"].get("status"),
                },
            )
            for change in changes:
                if (
                    change.get("change_type") == "DEPRECATE"
                    and change.get("decision") != "EXCLUDED"
                ):
                    audit.record(
                        actor=trusted_actor,
                        action="TREE_DEPRECATE",
                        target_type="HARDWARE_TREE_NODE",
                        target_id=str(change.get("node_id") or change.get("change_id")),
                        details={
                            "job_id": job_id,
                            "tree_type": before.get("tree_type"),
                            "change_id": change.get("change_id"),
                            "applied_version_id": result["job"].get("applied_version_id"),
                        },
                    )
            return result
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    return router
