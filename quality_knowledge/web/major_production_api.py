"""Unified Web API for Major Source production; no test-only data paths."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from services.major_case_production import MajorCaseProductionService, MajorProductionError


def _error(error: MajorProductionError) -> HTTPException:
    status = 404 if error.code in {"MAJOR_CASE_NOT_FOUND", "MAJOR_ENTRY_NOT_FOUND"} else 409 if error.code in {"NO_PUBLISHABLE_CONFIRMED_FACT", "MAJOR_CONFIRMATION_REQUIRES_PENDING_AI_CANDIDATE"} else 503 if error.code == "MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED" else 400
    return HTTPException(status, error.code)


def create_major_production_router(
    service: MajorCaseProductionService,
    *,
    restore_service: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/major-production", tags=["major-production"])

    @router.get("/excel/template")
    def excel_template() -> StreamingResponse:
        """Download the parser-compatible formal Major Case Excel template."""
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "重大问题"
        headers = list(dict.fromkeys([
            "IGR编号",
            *restore_service.excel_parser.field_mapping.values(),
            "关联ITR",
            "重大问题标题",
            "产品",
            "模块",
            "器件",
            "Failure Mode",
            "Failure Mechanism",
            "Trigger Condition",
        ]))
        sheet.append(headers)
        sheet.freeze_panes = "A2"
        example = {
            "IGR编号": "IGR-2026-001",
            "ITR单号": "ITR20260001",
            "问题描述": "示例：运行过程中出现异常",
            "TRC发生": "示例：技术发生原因",
            "MRC发生": "示例：管理发生原因",
            "产品": "PLC-X",
            "模块": "Motion",
            "Failure Mechanism": "示例失效机理",
            "Trigger Condition": "示例触发条件",
        }
        sheet.append([example.get(header, "") for header in headers])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="MAJOR_CASE_IMPORT_TEMPLATE_V1.0.xlsx"'
            },
        )

    @router.post("/excel/preview")
    async def excel_preview(
        file: UploadFile = File(...),
        materials: list[UploadFile] = File(default=[]),
        group_code: str = Form("MAJOR"),
        domain: str = Form("QUALITY"),
    ) -> dict[str, Any]:
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".xlsx", ".xlsm"}:
            raise HTTPException(400, "MAJOR_EXCEL_TYPE_UNSUPPORTED")
        excel_content = await file.read()
        if not excel_content:
            raise HTTPException(400, "MAJOR_EXCEL_EMPTY")
        material_payload: list[tuple[str, bytes]] = []
        for material in materials:
            material_suffix = Path(material.filename or "").suffix.lower()
            if material_suffix not in {".pdf", ".docx", ".doc"}:
                raise HTTPException(400, "MAJOR_REVIEW_MATERIAL_TYPE_UNSUPPORTED")
            material_payload.append((material.filename or "material.bin", await material.read()))
        try:
            preview = restore_service.stage_upload(
                file.filename or "major_cases.xlsx",
                excel_content,
                material_payload,
                group_code=group_code.strip() or "MAJOR",
                domain=domain.strip(),
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        preview["mapping"] = {
            "contract": "major-excel-field-mapping/v1",
            "detected_sheet": preview.get("parse_summary", {}).get("sheet_name"),
            "header_row": preview.get("parse_summary", {}).get("header_row"),
            "fields": dict(restore_service.excel_parser.field_mapping),
            "missing_columns": preview.get("parse_summary", {}).get("missing_columns", []),
        }
        return preview

    @router.post("/excel/confirm")
    def excel_confirm(batch_id: str = Form(...)) -> dict[str, Any]:
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        try:
            result = restore_service.commit(batch_id)
        except KeyError as error:
            raise HTTPException(404, "MAJOR_EXCEL_BATCH_NOT_FOUND") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"batch_id": batch_id, "result": result}

    @router.get("/excel/batches/{batch_id}")
    def excel_batch(batch_id: str) -> dict[str, Any]:
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        batch = restore_service.batch(batch_id)
        if not batch:
            raise HTTPException(404, "MAJOR_EXCEL_BATCH_NOT_FOUND")
        return batch

    @router.post("/sources", status_code=201)
    async def intake_source(
        title: str = Form(...),
        group_code: str = Form(...),
        standard_itr: str = Form(...),
        domain: str = Form(""),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        try:
            return service.intake_source(
                title=title,
                group_code=group_code,
                domain=domain,
                standard_itr=standard_itr,
                source_name=file.filename or "major-source.pdf",
                source_bytes=await file.read(),
            )
        except MajorProductionError as error:
            raise _error(error) from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get("/cases/{case_id}")
    def case_detail(case_id: str) -> dict[str, Any]:
        try:
            return service.detail(case_id)
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/cases/{case_id}/analysis")
    def analyze(case_id: str) -> dict[str, Any]:
        try:
            return service.analyze(case_id)
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/entries/{entry_id}/confirm")
    def confirm(entry_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return service.confirm_entry(
                entry_id,
                reviewer=str(payload.get("reviewer") or ""),
                content=str(payload.get("content") or ""),
                reason=str(payload.get("reason") or ""),
            )
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/events/{event_id}/publish")
    def publish(event_id: str) -> dict[str, Any]:
        try:
            return service.publish(event_id)
        except MajorProductionError as error:
            raise _error(error) from error

    return router
