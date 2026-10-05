"""Unified Web API for Major Source production; no test-only data paths."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from quality_knowledge.major_cases.import_governance import (
    TEMPLATE_VERSION,
    add_template_metadata,
)
from services.major_case_production import MajorCaseProductionService, MajorProductionError


def _error(error: MajorProductionError) -> HTTPException:
    status = 404 if error.code in {"MAJOR_CASE_NOT_FOUND", "MAJOR_ENTRY_NOT_FOUND"} else 409 if error.code in {
        "NO_PUBLISHABLE_CONFIRMED_FACT",
        "NO_PUBLISHABLE_HUMAN_REVISION",
        "PUBLISH_REVIEW_REQUIRED",
        "PUBLISH_REVISION_NOT_HUMAN",
        "PUBLISH_SEMANTIC_CONTENT_INVALID",
        "PUBLISH_SEMANTIC_SLOT_DUPLICATE",
        "PUBLISH_EVIDENCE_INVALID",
        "PUBLISH_EVIDENCE_SOURCE_NOT_FOUND",
        "PUBLISH_EVIDENCE_FRAGMENT_NOT_FOUND",
        "PUBLISH_EVIDENCE_SOURCE_REQUIRED",
        "PUBLISH_EVIDENCE_SOURCE_MISMATCH",
        "PUBLISH_EVIDENCE_EVENT_MISMATCH",
        "MAJOR_CONFIRMATION_REQUIRES_PENDING_AI_CANDIDATE",
        "MAJOR_SEMANTIC_REVIEW_DECISION_REQUIRED",
        "MAJOR_SEMANTIC_CORRECTION_REASON_REQUIRED",
        "MAJOR_CORRECTION_MUST_CHANGE_CONTENT",
        "MAJOR_SEMANTIC_SOURCE_LINK_MISSING",
        "MAJOR_SEMANTIC_EVIDENCE_REFERENCE_MISSING",
        "MAJOR_ANALYSIS_EVENT_REQUIRED",
        "MAJOR_ANALYSIS_EVENT_SELECTION_REQUIRED",
        "MAJOR_ANALYSIS_EVENT_INVALID",
        "MAJOR_REVIEW_EVENT_SELECTION_REQUIRED",
        "MAJOR_REVIEW_EVENT_MISMATCH",
    } else 503 if error.code == "MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED" else 400
    return HTTPException(status, error.code)


class MajorMutationBusy(RuntimeError):
    pass


class MajorMutationGuard:
    """Serialize writes sharing the Major SQLite and attachment stores."""

    def __init__(self) -> None:
        self._lock = Lock()

    def run(self, operation):
        if not self._lock.acquire(blocking=False):
            raise MajorMutationBusy("MAJOR_MUTATION_BUSY")
        try:
            return operation()
        finally:
            self._lock.release()


def create_major_production_router(
    service: MajorCaseProductionService,
    *,
    restore_service: Any | None = None,
    mutation_guard: MajorMutationGuard | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/major-production", tags=["major-production"])
    mutation_guard = mutation_guard or MajorMutationGuard()

    def run_mutation(operation):
        try:
            return mutation_guard.run(operation)
        except MajorMutationBusy as error:
            raise HTTPException(409, "MAJOR_MUTATION_BUSY") from error

    @router.get("/excel/template")
    def excel_template() -> StreamingResponse:
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        if not restore_service.parser_mapping_is_current():
            raise HTTPException(409, "MAJOR_EXCEL_MAPPING_RESTART_REQUIRED")
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
        mapping_version = restore_service.current_mapping_version()
        add_template_metadata(workbook, current_mapping_version=mapping_version)
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="MAJOR_CASE_IMPORT_TEMPLATE_V1.0.xlsx"',
                "X-Major-Template-Version": TEMPLATE_VERSION,
                "X-Major-Mapping-Version": mapping_version,
            },
        )

    @router.post("/excel/preview")
    async def excel_preview(
        file: UploadFile = File(...),
        materials: list[UploadFile] = File(default=[]),
        group_code: str = Form("MAJOR"),
        domain: str = Form("QUALITY"),
        actor: str = Form("web-user"),
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
            preview = run_mutation(
                lambda: restore_service.stage_upload(
                    file.filename or "major_cases.xlsx",
                    excel_content,
                    material_payload,
                    group_code=group_code.strip() or "MAJOR",
                    domain=domain.strip(),
                    actor=actor.strip() or "web-user",
                )
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return preview

    @router.post("/excel/confirm")
    def excel_confirm(
        batch_id: str = Form(...),
        actor: str = Form("web-user"),
    ) -> dict[str, Any]:
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        try:
            result = run_mutation(lambda: restore_service.commit(batch_id, actor=actor.strip() or "web-user"))
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
            source_bytes = await file.read()
            return run_mutation(
                lambda: service.intake_source(
                    title=title,
                    group_code=group_code,
                    domain=domain,
                    standard_itr=standard_itr,
                    source_name=file.filename or "major-source.pdf",
                    source_bytes=source_bytes,
                )
            )
        except MajorProductionError as error:
            raise _error(error) from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get("/cases/{case_id}")
    def case_detail(case_id: str) -> dict[str, Any]:
        try:
            detail = service.detail(case_id)
            if restore_service is not None:
                detail["case_identities"] = restore_service.identities(case_id)
                detail["source_fact_revisions"] = restore_service.source_fact_history(case_id)
            return detail
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/cases/{case_id}/analysis")
    def analyze(case_id: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        try:
            event_id = str((payload or {}).get("event_id") or "").strip() or None
            return run_mutation(lambda: service.analyze(case_id, event_id=event_id))
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/entries/{entry_id}/confirm")
    def confirm(entry_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return run_mutation(
                lambda: service.confirm_entry(
                    entry_id,
                    reviewer=str(payload.get("reviewer") or ""),
                    event_id=str(payload.get("event_id") or "").strip() or None,
                    content=str(payload.get("content") or ""),
                    reason=str(payload.get("reason") or ""),
                    action=str(payload.get("action") or ""),
                )
            )
        except MajorProductionError as error:
            raise _error(error) from error

    @router.post("/events/{event_id}/publish")
    def publish(event_id: str) -> dict[str, Any]:
        try:
            return run_mutation(lambda: service.publish(event_id))
        except MajorProductionError as error:
            raise _error(error) from error

    return router
