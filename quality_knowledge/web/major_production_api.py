"""Unified Web API for Major Source production; no test-only data paths."""
from __future__ import annotations

from io import BytesIO
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from starlette.concurrency import run_in_threadpool

from quality_knowledge.major_cases.import_governance import (
    TEMPLATE_VERSION,
    add_template_metadata,
)
from services.major_case_production import MajorCaseProductionService, MajorProductionError


logger = logging.getLogger('uvicorn.error')


def _error(error: MajorProductionError) -> HTTPException:
    status = 404 if error.code in {"MAJOR_CASE_NOT_FOUND", "MAJOR_ENTRY_NOT_FOUND", "MAJOR_EVENT_NOT_FOUND"} else 409 if error.code in {"NO_PUBLISHABLE_CONFIRMED_FACT", "MAJOR_CONFIRMATION_REQUIRES_PENDING_AI_CANDIDATE", "CASE_IDENTITY_CONFLICT"} else 503 if error.code == "MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED" else 400
    task_id = getattr(error, 'task_id', None)
    headers = {'X-Major-Runtime-Task-ID': task_id} if task_id else None
    return HTTPException(status, error.code, headers=headers)


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
        current_mapping_version = restore_service.current_mapping_version()
        add_template_metadata(
            workbook,
            current_mapping_version=current_mapping_version,
        )
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="MAJOR_CASE_IMPORT_TEMPLATE_V1.0.xlsx"',
                "X-Major-Template-Version": TEMPLATE_VERSION,
                "X-Major-Mapping-Version": current_mapping_version,
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
        if suffix not in {".xls", ".xlsx", ".xlsm"}:
            raise HTTPException(400, "MAJOR_EXCEL_TYPE_UNSUPPORTED")
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        excel_content = await file.read()
        if not excel_content:
            raise HTTPException(400, "MAJOR_EXCEL_EMPTY")
        material_payload: list[tuple[str, bytes]] = []
        for material in materials:
            # A native browser FormData may send a blank UploadFile when the
            # optional multiple-file control has no selected files. An empty
            # filename + empty body is *no material*, not an unsupported type.
            content = await material.read()
            name = (material.filename or "").strip()
            if not name and not content:
                continue
            material_suffix = Path(name).suffix.lower()
            if material_suffix not in {".pdf", ".docx"}:
                raise HTTPException(400, "MAJOR_REVIEW_MATERIAL_TYPE_UNSUPPORTED")
            material_payload.append((name, content))
        logger.info(
            "MAJOR_EXCEL_PREVIEW_UPLOAD_COMPLETE request_id=%s excel_bytes=%d material_count=%d",
            request_id, len(excel_content), len(material_payload),
        )
        try:
            logger.info("MAJOR_EXCEL_PREVIEW_STAGE_START request_id=%s", request_id)
            # Parsing/staging perform disk and SQLite IO; keep the ASGI loop responsive.
            preview = await run_in_threadpool(
                restore_service.stage_upload,
                file.filename or "major_cases.xlsx",
                excel_content,
                material_payload,
                group_code=group_code.strip() or "MAJOR",
                domain=domain.strip(),
                actor=actor.strip() or "web-user",
            )
        except ValueError as error:
            logger.warning(
                "MAJOR_EXCEL_PREVIEW_REJECTED request_id=%s elapsed_ms=%d error_code=%s",
                request_id, int((time.monotonic() - started) * 1000), str(error),
            )
            raise HTTPException(400, str(error)) from error
        except Exception:
            logger.exception(
                "MAJOR_EXCEL_PREVIEW_FAILED request_id=%s elapsed_ms=%d",
                request_id, int((time.monotonic() - started) * 1000),
            )
            raise
        logger.info(
            "MAJOR_EXCEL_PREVIEW_COMPLETE request_id=%s batch_id=%s rows=%d elapsed_ms=%d",
            request_id, preview.get("batch_id"), len(preview.get("rows") or []),
            int((time.monotonic() - started) * 1000),
        )
        preview["mapping"] = {
            "contract": preview["mapping_contract"],
            "version": preview["mapping_version"],
            "detected_sheet": preview.get("parse_summary", {}).get("sheet_name"),
            "header_row": preview.get("parse_summary", {}).get("header_row"),
            "fields": dict(restore_service.excel_parser.field_mapping),
            "missing_columns": preview.get("parse_summary", {}).get("missing_columns", []),
        }
        return preview

    @router.post("/excel/confirm")
    def excel_confirm(
        batch_id: str = Form(...),
        actor: str = Form("web-user"),
    ) -> dict[str, Any]:
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        started = time.monotonic()
        logger.info("MAJOR_EXCEL_COMMIT_START batch_id=%s", batch_id)
        try:
            result = restore_service.commit(
                batch_id,
                actor=actor.strip() or "web-user",
            )
        except KeyError as error:
            raise HTTPException(404, "MAJOR_EXCEL_BATCH_NOT_FOUND") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        logger.info(
            "MAJOR_EXCEL_COMMIT_COMPLETE batch_id=%s imported=%d elapsed_ms=%d",
            batch_id, int(result.get("imported") or 0),
            int((time.monotonic() - started) * 1000),
        )
        return {"batch_id": batch_id, "result": result}

    @router.get("/excel/batches/{batch_id}")
    def excel_batch(batch_id: str) -> dict[str, Any]:
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        batch = restore_service.batch(batch_id)
        if not batch:
            raise HTTPException(404, "MAJOR_EXCEL_BATCH_NOT_FOUND")
        # Read-only recovery metadata: never mutate the frozen preview snapshot
        # or its governance SHA. Only the original PREVIEW can be committed.
        batch["current_mapping_version"] = restore_service.current_mapping_version()
        return batch

    @router.get("/excel/batches/{batch_id}/preflight")
    def excel_batch_preflight(batch_id: str) -> dict[str, Any]:
        """Read-only explanation of the same row checks used by commit().

        No automatic exclusion of rows, commit, Provider call or mutation.
        """
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        batch = restore_service.batch(batch_id)
        if not batch:
            raise HTTPException(404, "MAJOR_EXCEL_BATCH_NOT_FOUND")
        errors: list[dict[str, Any]] = []
        status = str(batch.get("status") or "")
        if status != "PREVIEW":
            errors.append({"row": None, "error": "MAJOR_EXCEL_BATCH_NOT_CONFIRMABLE"})
        else:
            governance = batch.get("governance")
            if not governance:
                errors.append({"row": None, "error": "MAJOR_EXCEL_GOVERNANCE_MISSING_REPREVIEW_REQUIRED"})
            else:
                if restore_service.current_mapping_version() != governance.get("mapping_version"):
                    errors.append({"row": None, "error": "MAJOR_EXCEL_MAPPING_CHANGED_AFTER_PREVIEW"})
                # Use exactly the already existing preview snapshot hash rule;
                # never edit preview_json or the import governance record.
                from quality_knowledge.major_cases.restore import _hash
                if _hash(batch["preview"]) != governance.get("preview_sha256"):
                    errors.append({"row": None, "error": "MAJOR_EXCEL_PREVIEW_CHANGED_AFTER_PREVIEW"})
            if not errors:
                errors.extend(restore_service._preflight_commit(batch))
        return {
            "batch_id": batch_id,
            "status": status,
            "row_count": len((batch.get("preview") or {}).get("rows") or []),
            "confirmable": status == "PREVIEW" and not errors,
            "errors": errors,
        }

    @router.get("/recent")
    def recent_work() -> dict[str, Any]:
        """Read-only recovery index for an interrupted browser session.

        Return bounded identifiers/status only, not full source text, upload
        bytes, prompts, credentials, or preview content.
        """
        with service.repository.connect() as connection:
            batches = [dict(row) for row in connection.execute(
                """SELECT batch_id,source_file,status,created_at,committed_at
                   FROM kb_major_import_batch ORDER BY created_at DESC,batch_id DESC LIMIT 20"""
            ).fetchall()]
            cases = [dict(row) for row in connection.execute(
                """SELECT case_id,title,status,created_at,updated_at
                   FROM kb_case WHERE archived_at IS NULL
                   ORDER BY created_at DESC,case_id DESC LIMIT 20"""
            ).fetchall()]
        return {"batches": batches, "cases": cases}

    @router.post("/sources", status_code=201)
    async def intake_source(
        title: str = Form(...),
        group_code: str = Form(...),
        standard_itr: str = Form(...),
        domain: str = Form(""),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        source_content = await file.read()
        try:
            logger.info("MAJOR_SINGLE_SOURCE_INTAKE_START source_type=%s source_bytes=%d", Path(file.filename or "").suffix.lower(), len(source_content))
            result = await run_in_threadpool(
                service.intake_source,
                title=title,
                group_code=group_code,
                domain=domain,
                standard_itr=standard_itr,
                source_name=file.filename or "major-source.pdf",
                source_bytes=source_content,
            )
            logger.info("MAJOR_SINGLE_SOURCE_INTAKE_COMPLETE case_id=%s event_id=%s", result["case"]["case_id"], result["event"]["event_id"])
            return result
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

    @router.get("/cases/{case_id}/provenance")
    def case_provenance(case_id: str) -> dict[str, Any]:
        """Read-only proof of persisted Excel Source Fact and separate documents.

        Uses existing Major persistence and links; historical-case/v1 is unchanged.
        """
        if restore_service is None:
            raise HTTPException(503, "MAJOR_EXCEL_IMPORT_NOT_CONFIGURED")
        try:
            detail = service.detail(case_id)
        except MajorProductionError as error:
            raise _error(error) from error

        links = detail.get("source_links") or []
        structured = []
        for fact in restore_service.source_fact_history(case_id):
            if fact.get("source_type") != "EXCEL":
                continue
            link_ids = [
                link["source_link_id"]
                for link in links
                if link.get("source_type") == "MAJOR_EXCEL_SOURCE_FACT"
                and link.get("record_id") == fact.get("source_fact_revision_id")
            ]
            structured.append({
                "source_type": "EXCEL",
                "source_fact_revision_id": fact["source_fact_revision_id"],
                "revision_no": fact["revision_no"],
                "source_ref": fact["source_ref"],
                "source_hash": fact["source_hash"],
                "original_description": str((fact.get("normalized") or {}).get("original_description") or ""),
                "source_link_ids": link_ids,
                "linked": bool(link_ids),
            })

        documents = [
            {
                "version_id": doc["version_id"],
                "filename": doc["original_filename"],
                "parse_status": doc.get("parse_status") or "",
            }
            for doc in detail.get("documents") or []
        ]
        return {
            "case_id": case_id,
            "structured_source_facts": structured,
            "document_evidence": documents,
        }

    @router.post("/cases/{case_id}/analysis")
    def analyze(case_id: str, event_id: str = "") -> dict[str, Any]:
        try:
            return service.analyze(case_id, event_id=event_id)
        except MajorProductionError as error:
            raise _error(error) from error

    @router.get("/cases/{case_id}/analysis/diagnostics")
    def analysis_diagnostics(case_id: str) -> dict[str, Any]:
        """Read-only, same case scope as case detail; never returns source/credentials."""
        try:
            return service.analysis_diagnostics(case_id)
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
