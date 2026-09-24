"""P07 backend API for HC-TREE-IMPORT-001 on the existing /api/v2 app.

This router exposes the product import workflow without creating a second Web
server, port, auth system, or Runtime. Uploaded Excel files remain in the
company-local staging directory and server paths are never returned.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from repositories.hardware_tree_import_repository import HardwareTreeImportRepository
from services.hardware_tree_excel import HardwareTreeImportAnalyzer
from services.hardware_tree_import_contract import HardwareTreeImportContractError
from services.hardware_tree_import_files import HardwareTreeImportFileStore


def _require_maintainer(value: str | None) -> None:
    role = str(value or "CONSUMER").strip().upper()
    if role != "MAINTAINER":
        raise HTTPException(status_code=403, detail="HARDWARE_CASE_MAINTAINER_REQUIRED")


def _operator(value: str | None) -> str:
    operator = str(value or "").strip()
    if not operator:
        raise HTTPException(status_code=400, detail="HARDWARE_TREE_OPERATOR_REQUIRED")
    return operator


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
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["hardware-tree-import"])
    analyzer = HardwareTreeImportAnalyzer(repository)

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
        _require_maintainer(x_hardware_case_role)
        operator = _operator(x_hardware_case_operator)
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
        _require_maintainer(x_hardware_case_role)
        try:
            items = repository.list_jobs(tree_type)
            return {"items": items, "total": len(items)}
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.get("/active-version/{tree_type}")
    def active_version(
        tree_type: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        try:
            version = repository.get_active_version(tree_type)
            return {"tree_type": tree_type.upper(), "active_version": version}
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    @router.get("/{job_id}")
    def get_import(
        job_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
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
        _require_maintainer(x_hardware_case_role)
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
        _require_maintainer(x_hardware_case_role)
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
        _require_maintainer(x_hardware_case_role)
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
        _require_maintainer(x_hardware_case_role)
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
        _require_maintainer(x_hardware_case_role)
        try:
            return repository.apply_job(job_id)
        except HardwareTreeImportContractError as error:
            raise _http_error(error) from error

    return router
