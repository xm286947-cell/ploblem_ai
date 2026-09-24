"""Native P0 HTTP API; never constructs or mutates the validation database."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from quality_knowledge.p0.intake_service import P0IntakeError, P0IntakeService
from quality_knowledge.p0.repository import P0RepositoryError
from quality_knowledge.p1 import ForwardRiskError, ForwardRiskService
from quality_knowledge.product_report import ProductQualityReportService, ProductReportError
from quality_knowledge.quality_scenario_candidate_v1_service import CandidateV1Service
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.quality_scenario_v1_workflow_service import QualityScenarioV1WorkflowService
from quality_knowledge.quality_scenario_traceability_service import QualityScenarioTraceabilityService
from quality_knowledge.services.v2_analysis_service import V2AnalysisError, V2AnalysisService
from quality_knowledge.services.v2_batch_analysis_service import V2BatchAnalysisError, V2BatchAnalysisService
from quality_knowledge.services.v2_batch_job_service import V2BatchAnalysisJobManager
from quality_knowledge.standard_fields.repository import StandardFieldCatalogError, StandardFieldRepository
from quality_knowledge.standard_fields.service import StandardFieldService


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, ValidationError):
        return HTTPException(400, "SCENARIO_VALIDATION_ERROR")
    if isinstance(error, sqlite3.Error):
        return HTTPException(500, "SCENARIO_PERSISTENCE_ERROR")
    code = str(error)
    if code in {
        "ISSUE_NOT_FOUND", "V2_ANALYSIS_NOT_AVAILABLE", "ANALYSIS_SET_NOT_FOUND", "ANALYSIS_JOB_NOT_FOUND",
        "RISK_CASE_SOURCE_ISSUE_NOT_FOUND", "RISK_CASE_MERGE_TARGET_NOT_FOUND", "ASSESSMENT_NOT_FOUND",
        "ASSESSMENT_VERSION_NOT_FOUND", "RISK_RESULT_NOT_FOUND",
        "REPORT_NOT_FOUND", "QUALITY_SCENARIO_V1_NOT_FOUND",
    }:
        return HTTPException(404, code)
    if code in {"HUMAN_CONFIRMATION_STALE", "HUMAN_CONFIRMATION_VERSION_CONFLICT", "INSIGHT_SCOPE_CHANGED",
                "ASSESSMENT_MATERIAL_UNCHANGED", "SCENARIO_VERSION_CONFLICT",
                "SCENARIO_CONFIRM_STATE_CONFLICT", "SCENARIO_REJECT_STATE_CONFLICT"}:
        return HTTPException(409, code)
    if code in {"ANALYSIS_RUNNER_NOT_CONFIGURED", "AI_CONFIGURATION_NOT_CONFIGURED"}:
        return HTTPException(503, code)
    if code in {"P0_PREVIEW_NOT_FOUND", "P0_INTAKE_FILE_NOT_FOUND", "P0_INTAKE_SHEET_NOT_FOUND"}:
        return HTTPException(404, code)
    if code in {
        "P0_MAPPING_CHANGED_AFTER_PREVIEW", "P0_INTAKE_CONFIRM_BLOCKED", "P0_IMPORT_IN_PROGRESS",
        "P0_DRAFT_PREVIEW_REQUIRES_ACTIVATION",
    }:
        return HTTPException(409, code)
    return HTTPException(400, code)


def create_v2_router(
    repository: Any,
    *,
    stage_runner: Any | None = None,
    initialization_status: dict[str, Any] | None = None,
    analysis_runtime_status: dict[str, Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2")
    fields = StandardFieldRepository(repository.db_path)
    analysis_jobs = V2BatchAnalysisJobManager(repository, stage_runner)
    reports = ProductQualityReportService(repository)
    scenario_candidates = CandidateV1Service(SQLiteQualityScenarioV1Repository(repository.db_path))
    scenario_workflow = QualityScenarioV1WorkflowService(scenario_candidates.repository)
    scenario_traceability = QualityScenarioTraceabilityService(scenario_candidates.repository)

    @router.get("/product-reports/precheck")
    def report_precheck(product_code: str, start_month: str, end_month: str) -> dict[str, Any]:
        return reports.precheck(product_code, start_month, end_month)

    @router.get("/product-reports")
    def product_reports() -> dict[str, Any]:
        items = reports.list(); return {"items": items, "total": len(items)}

    @router.post("/product-reports", status_code=201)
    def create_product_report(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return reports.create(str(payload.get("product_code") or ""), str(payload.get("start_month") or ""), str(payload.get("end_month") or ""), str(payload.get("created_by") or ""))
        except ProductReportError as error:
            raise _http_error(error) from error

    @router.get("/product-reports/{report_id}")
    def product_report(report_id: str) -> dict[str, Any]:
        try: return reports.get(report_id)
        except ProductReportError as error: raise _http_error(error) from error

    @router.post("/product-reports/{report_id}/publish")
    def publish_product_report(report_id: str) -> dict[str, Any]:
        try: return reports.publish(report_id)
        except ProductReportError as error: raise _http_error(error) from error

    @router.delete("/product-reports/{report_id}")
    def delete_product_report(report_id: str) -> dict[str, Any]:
        try: return reports.delete(report_id)
        except ProductReportError as error: raise HTTPException(409 if str(error) == "PUBLISHED_REPORT_CANNOT_BE_DELETED" else 404, str(error)) from error

    def _issue_summary(row: Any) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"] or "{}")
        fact = snapshot.get("ISSUE_FACT") or snapshot.get("issue_fact") or {}
        context = snapshot.get("PRODUCT_CONTEXT") or snapshot.get("product_context") or {}
        return {
            "knowledge_id": row["knowledge_id"],
            "business_issue_id": row["business_issue_id"],
            "business_type": row["business_type"],
            "product_code": row["product_code"],
            "product_name": row["product_name"],
            "title": fact.get("title") or fact.get("description") or row["business_issue_id"],
            "product": fact.get("product") or context.get("product"),
            "platform": fact.get("platform") or context.get("platform"),
            "month": fact.get("month"),
            "severity": fact.get("severity"),
            "issue_domain": fact.get("issue_domain"),
            "analysis_set_id": row["analysis_set_id"],
            "analysis_status": row["analysis_status"] or "NOT_ANALYZED",
            "updated_at": row["updated_at"],
        }

    def _issue_scope(
        business_type: str = "", product: str = "", month: str = "",
        analysis_status: str = "", q: str = "",
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if business_type:
            clauses.append("mapping.business_type = ?")
            values.append(business_type.upper())
        if product:
            clauses.append("(product.product_code = ? OR product.product_id = ?)")
            values.extend([product.upper(), product])
        if analysis_status:
            if analysis_status.upper() == "NOT_ANALYZED":
                clauses.append("analysis.analysis_set_id IS NULL")
            else:
                clauses.append("analysis.status = ?")
                values.append(analysis_status.upper())
        if month:
            clauses.append("json_extract(snapshot.snapshot_json, '$.ISSUE_FACT.month') = ?")
            values.append(month)
        if q:
            clauses.append("(issue.business_issue_id LIKE ? OR snapshot.snapshot_json LIKE ?)")
            values.extend([f"%{q}%", f"%{q}%"])
        return (" AND " + " AND ".join(clauses)) if clauses else "", values

    @router.get("/initialization/status")
    def initialization() -> dict[str, Any]:
        return initialization_status or {"initialization_state": "READY", "outcome": "READY"}

    @router.get("/analysis-runtime/status")
    def analysis_runtime() -> dict[str, Any]:
        return analysis_runtime_status or {
            "ready": stage_runner is not None,
            "source": "INJECTED_RUNNER" if stage_runner is not None else "NOT_CONFIGURED",
            "errors": [] if stage_runner is not None else ["ANALYSIS_RUNNER_NOT_CONFIGURED"],
        }

    @router.get("/products")
    def products(enabled: bool = True) -> dict[str, Any]:
        query = "SELECT * FROM product_config" + (" WHERE enabled = 1" if enabled else "") + " ORDER BY product_code"
        with repository.connect() as connection:
            items = []
            for row in connection.execute(query):
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
                items.append(item)
        return {"items": items, "total": len(items)}

    @router.post("/products", status_code=201)
    def create_product(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return fields.create_product(
                product_code=str(payload.get("product_code") or ""),
                product_name=str(payload.get("product_name") or ""),
                product_type=str(payload.get("product_type") or ""),
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            )
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.post("/products/{product_code}/mapping-drafts", status_code=201)
    def create_mapping_draft(product_code: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return fields.persist_product_starter_draft(
                product_code, str(payload.get("created_by") or "")
            )
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.get("/mappings")
    def mappings(business_type: str = "") -> dict[str, Any]:
        items = fields.list_mappings(business_type)
        return {"items": items, "total": len(items)}

    @router.get("/mappings/{config_id}")
    def mapping(config_id: str) -> dict[str, Any]:
        try:
            return fields.get_mapping(config_id)
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.put("/mappings/{config_id}")
    def update_mapping(config_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return fields.update_mapping_draft(
                config_id,
                bindings=payload.get("bindings") or [],
                updated_by=str(payload.get("updated_by") or ""),
            )
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.post("/mappings/{config_id}/validate")
    def validate_mapping(config_id: str) -> dict[str, Any]:
        try:
            return fields.validate_mapping_draft(config_id)
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.post("/mappings/{config_id}/activate")
    def activate_mapping(config_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return fields.activate_mapping(config_id, str(payload.get("actor") or ""))
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.get("/taxonomies")
    def taxonomies(taxonomy_type: str = "") -> dict[str, Any]:
        query = """SELECT term.* FROM analysis_taxonomy_term term
                   JOIN analysis_taxonomy_version version USING(taxonomy_version_id)
                   WHERE version.status='ACTIVE' AND term.enabled=1"""
        values: list[Any] = []
        if taxonomy_type:
            query += " AND term.taxonomy_type=?"
            values.append(taxonomy_type.upper())
        query += " ORDER BY term.taxonomy_type,term.sort_order,term.code"
        with repository.connect() as connection:
            items = [dict(row) for row in connection.execute(query, values)]
        return {"items": items, "total": len(items)}

    @router.get("/standard-fields")
    def standard_fields(q: str = "", limit: int = 100) -> dict[str, Any]:
        try:
            items = [asdict(item) for item in fields.search_active(q, min(max(limit, 1), 1000))]
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error
        return {"items": items, "total": len(items)}

    @router.post("/intake/preview")
    def preview_intake(
        file: UploadFile = File(...),
        business_type: str = Form("PLC"),
        sheet: str = Form(""),
    ) -> dict[str, Any]:
        suffix = Path(file.filename or "upload.xlsx").suffix.lower()
        if suffix not in {".xlsx", ".xlsm"}:
            raise HTTPException(400, "P0_INTAKE_FILE_TYPE_UNSUPPORTED")
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / (Path(file.filename or "upload.xlsx").name)
                source.write_bytes(file.file.read())
                return P0IntakeService(repository).preview(source, business_type.upper(), sheet or None)
        except P0IntakeError as error:
            raise _http_error(error) from error

    @router.post("/intake/confirm")
    def confirm_intake(payload: dict[str, Any]) -> dict[str, Any]:
        token = str(payload.get("preview_token") or "")
        if not token:
            raise HTTPException(400, "P0_PREVIEW_TOKEN_REQUIRED")
        try:
            return P0IntakeService(repository).confirm(token)
        except P0IntakeError as error:
            raise _http_error(error) from error

    @router.post("/intake/mapping-draft", status_code=201)
    def create_intake_mapping_draft(payload: dict[str, Any]) -> dict[str, Any]:
        token = str(payload.get("preview_token") or "")
        if not token:
            raise HTTPException(400, "P0_PREVIEW_TOKEN_REQUIRED")
        decisions = payload.get("decisions")
        if not isinstance(decisions, list) or not decisions:
            raise HTTPException(400, "P0_MAPPING_DECISIONS_REQUIRED")
        try:
            return P0IntakeService(repository).create_mapping_draft(
                token, decisions, str(payload.get("actor") or "web")
            )
        except P0IntakeError as error:
            raise _http_error(error) from error

    @router.get("/issues")
    def issues(
        business_type: str = "", product: str = "", month: str = "",
        analysis_status: str = "", q: str = "", page: int = 1, page_size: int = 100,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 200:
            raise HTTPException(400, "ISSUE_PAGINATION_INVALID")
        scope, values = _issue_scope(business_type, product, month, analysis_status, q)
        base = """FROM quality_issue issue
                  JOIN quality_issue_version version ON version.issue_version_id=issue.current_version_id
                  JOIN issue_normalized_snapshot snapshot ON snapshot.issue_version_id=version.issue_version_id
                  JOIN mapping_config mapping ON mapping.config_id=version.mapping_config_id
                  LEFT JOIN product_config product ON product.product_id=issue.product_id
                  LEFT JOIN analysis_set analysis ON analysis.analysis_set_id=(
                    SELECT candidate.analysis_set_id FROM analysis_set candidate
                    WHERE candidate.issue_version_id=version.issue_version_id
                    ORDER BY candidate.rowid DESC LIMIT 1) WHERE 1=1""" + scope
        with repository.connect() as connection:
            total = connection.execute("SELECT COUNT(*) " + base, values).fetchone()[0]
            rows = connection.execute(
                """SELECT issue.knowledge_id,issue.business_issue_id,issue.updated_at,
                          product.product_code,product.product_name,mapping.business_type,snapshot.snapshot_json,
                          analysis.analysis_set_id,analysis.status analysis_status """ + base
                + " ORDER BY issue.updated_at DESC,issue.knowledge_id LIMIT ? OFFSET ?",
                [*values, page_size, (page - 1) * page_size],
            ).fetchall()
        return {"total": total, "page": page, "page_size": page_size, "items": [_issue_summary(row) for row in rows]}

    @router.get("/issues/facets")
    def issue_facets() -> dict[str, Any]:
        with repository.connect() as connection:
            months = [row[0] for row in connection.execute(
                """SELECT DISTINCT json_extract(snapshot_json,'$.ISSUE_FACT.month') month
                     FROM issue_normalized_snapshot
                    WHERE month IS NOT NULL AND TRIM(month) <> '' ORDER BY month DESC"""
            )]
            statuses = [row[0] for row in connection.execute(
                "SELECT DISTINCT status FROM analysis_set ORDER BY status"
            )]
        return {"months": months, "analysis_statuses": statuses}

    @router.post("/issues/batch-analysis")
    def batch_analyze(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return V2BatchAnalysisService(repository, stage_runner).run(
                payload.get("knowledge_ids") or [], concurrency=payload.get("concurrency", 2),
                request={"force": bool(payload.get("force"))},
            )
        except V2BatchAnalysisError as error:
            raise _http_error(error) from error

    @router.post("/analysis-jobs", status_code=202)
    def start_analysis_job(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return analysis_jobs.start(
                payload.get("knowledge_ids") or [],
                concurrency=payload.get("concurrency", 2),
                request={"force": bool(payload.get("force"))},
            )
        except V2BatchAnalysisError as error:
            raise _http_error(error) from error

    @router.get("/analysis-jobs/{job_id}")
    def analysis_job(job_id: str) -> dict[str, Any]:
        try:
            return analysis_jobs.get(job_id)
        except V2BatchAnalysisError as error:
            raise _http_error(error) from error

    @router.get("/issues/{knowledge_id}")
    def issue_detail(knowledge_id: str) -> dict[str, Any]:
        issue = repository.get_issue(knowledge_id)
        if issue is None:
            raise HTTPException(404, "ISSUE_NOT_FOUND")
        with repository.connect() as connection:
            context = connection.execute(
                """SELECT mapping.business_type,product.product_code,product.product_name
                     FROM quality_issue current_issue
                     JOIN quality_issue_version version ON version.issue_version_id=current_issue.current_version_id
                     JOIN mapping_config mapping ON mapping.config_id=version.mapping_config_id
                     LEFT JOIN product_config product ON product.product_id=current_issue.product_id
                    WHERE current_issue.knowledge_id=?""",
                (knowledge_id,),
            ).fetchone()
        if context:
            issue.update(dict(context))
        analysis = repository.get_latest_analysis_set(knowledge_id)
        effective = repository.get_effective_analysis(analysis["analysis_set_id"]) if analysis else None
        revisions = repository.get_human_revisions(analysis["analysis_set_id"]) if analysis else []
        return {
            "issue": issue,
            "analysis": analysis,
            "effective_analysis": effective,
            "human_revisions": revisions,
        }

    @router.get("/issues/{knowledge_id}/navigation")
    def issue_navigation(
        knowledge_id: str, business_type: str = "", product: str = "", month: str = "",
        analysis_status: str = "", q: str = "",
    ) -> dict[str, Any]:
        scope, values = _issue_scope(business_type, product, month, analysis_status, q)
        base = """FROM quality_issue issue
                  JOIN quality_issue_version version ON version.issue_version_id=issue.current_version_id
                  JOIN issue_normalized_snapshot snapshot ON snapshot.issue_version_id=version.issue_version_id
                  JOIN mapping_config mapping ON mapping.config_id=version.mapping_config_id
                  LEFT JOIN product_config product ON product.product_id=issue.product_id
                  LEFT JOIN analysis_set analysis ON analysis.analysis_set_id=(
                    SELECT candidate.analysis_set_id FROM analysis_set candidate
                    WHERE candidate.issue_version_id=version.issue_version_id
                    ORDER BY candidate.rowid DESC LIMIT 1) WHERE 1=1""" + scope
        query = """WITH scoped AS (
                     SELECT issue.knowledge_id,issue.business_issue_id,issue.updated_at """ + base + """
                   ), ordered AS (
                     SELECT *,
                            LAG(knowledge_id) OVER (ORDER BY updated_at DESC,knowledge_id) previous_id,
                            LEAD(knowledge_id) OVER (ORDER BY updated_at DESC,knowledge_id) next_id,
                            ROW_NUMBER() OVER (ORDER BY updated_at DESC,knowledge_id) position,
                            COUNT(*) OVER () total
                       FROM scoped
                   ) SELECT * FROM ordered WHERE knowledge_id=?"""
        with repository.connect() as connection:
            row = connection.execute(query, [*values, knowledge_id]).fetchone()
        if row is None:
            raise HTTPException(404, "ISSUE_NOT_IN_SCOPE")
        return dict(row)

    @router.post("/standard-fields/change-requests", status_code=201)
    def request_standard_field(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with fields.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                result = fields.create_controlled_draft(
                    proposed_domain=str(payload.get("proposed_domain") or ""),
                    proposed_field=str(payload.get("proposed_field") or ""),
                    label_zh=str(payload.get("label_zh") or ""),
                    definition_zh=str(payload.get("definition_zh") or ""),
                    data_type=str(payload.get("data_type") or "string"),
                    business_example=str(payload.get("business_example") or ""),
                    reuse_rationale=str(payload.get("reuse_rationale") or ""),
                    candidate_fields=payload.get("candidate_fields") or [],
                    source_context=payload.get("source_context") or {},
                    requested_by=str(payload.get("requested_by") or ""),
                    connection=connection,
                )
                connection.commit()
                return result
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.post("/standard-field-catalogs/{catalog_id}/validate")
    def validate_catalog(catalog_id: str) -> dict[str, Any]:
        with fields.connect() as connection:
            errors = StandardFieldService(fields, "unused").validate_catalog_for_activation(catalog_id, connection)
        return {"catalog_version_id": catalog_id, "valid": not errors, "errors": errors}

    @router.post("/standard-fields/change-requests/{change_request_id}/approve")
    def approve_standard_field(change_request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return fields.approve_change_request(
                change_request_id,
                str(payload.get("approved_by") or ""),
            )
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.post("/standard-field-catalogs/{catalog_id}/activate")
    def activate_catalog(catalog_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return fields.activate_catalog(catalog_id, str(payload.get("approved_by") or ""))
        except StandardFieldCatalogError as error:
            raise _http_error(error) from error

    @router.post("/issues/{knowledge_id}/analysis")
    def analyze(knowledge_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return V2AnalysisService(repository, stage_runner).run(knowledge_id, payload or {}).model_dump(mode="json")
        except (V2AnalysisError, P0RepositoryError) as error:
            raise _http_error(error) from error

    @router.get("/issues/{knowledge_id}/analysis")
    def analysis(knowledge_id: str) -> dict[str, Any]:
        try:
            return V2AnalysisService(repository, stage_runner).get(knowledge_id)
        except (V2AnalysisError, P0RepositoryError) as error:
            raise _http_error(error) from error

    @router.post("/issues/{knowledge_id}/human-confirmations", status_code=201)
    def confirm(knowledge_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = repository.get_latest_analysis_set(knowledge_id)
        if current is None:
            raise HTTPException(404, "V2_ANALYSIS_NOT_AVAILABLE")
        try:
            normalized_answers: list[dict[str, Any]] = []
            for answer in payload.get("answers") or []:
                target_path = str(answer.get("target_path") or "")
                status = str(answer.get("confirmation_status") or "CONFIRMED").upper()
                value = answer.get("confirmed_value")
                if target_path in {"occurrence.mrc.primary", "escape.mrc.primary"}:
                    if isinstance(value, dict):
                        value = value.get("mrc_code") or value.get("code")
                    if status in {"CONFIRMED", "CORRECTED"} and (not isinstance(value, str) or not value.strip()):
                        raise P0RepositoryError("HUMAN_CONFIRMATION_MRC_CODE_REQUIRED")
                    value = value.strip() if isinstance(value, str) else value
                elif target_path.startswith("capability_gaps."):
                    if status in {"CONFIRMED", "CORRECTED"} and not isinstance(value, (str, dict)):
                        raise P0RepositoryError("HUMAN_CONFIRMATION_GAP_VALUE_INVALID")
                normalized_answers.append({
                    **answer,
                    "target_path": target_path,
                    "confirmation_status": status,
                    "confirmed_value": value,
                    "changes_insight": bool(answer.get("changes_insight"))
                    and status in {"CONFIRMED", "CORRECTED"}
                    and (target_path in {"occurrence.mrc.primary", "escape.mrc.primary"}
                         or target_path.startswith("capability_gaps.")),
                })
            return repository.save_human_revision(
                base_analysis_set_id=str(payload.get("base_analysis_set_id") or current["analysis_set_id"]),
                base_input_hash=str(payload.get("base_input_hash") or ""),
                confirmed_by=str(payload.get("confirmed_by") or ""),
                answers=normalized_answers,
                expected_revision_no=payload.get("expected_revision_no"),
            )
        except P0RepositoryError as error:
            raise _http_error(error) from error

    @router.post("/risk-cases/publish", status_code=201)
    def publish_risk_case(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ForwardRiskService(repository).publish_issue(
                str(payload.get("knowledge_id") or ""),
                publication_level=str(payload.get("publication_level") or "INTERNAL_FULL"),
                published_by=str(payload.get("published_by") or ""),
                merge_into_risk_case_id=str(payload.get("merge_into_risk_case_id") or ""),
            )
        except ForwardRiskError as error:
            raise _http_error(error) from error

    @router.get("/risk-cases")
    def risk_cases(
        q: str = "", publication_level: str = "", product: str = "", domain: str = "",
        lifecycle: str = "", mrc: str = "", capability: str = "",
    ) -> dict[str, Any]:
        try:
            return ForwardRiskService(repository).list_cases(
                q=q, publication_level=publication_level, product=product, domain=domain,
                lifecycle=lifecycle, mrc=mrc, capability=capability,
            )
        except ForwardRiskError as error:
            raise _http_error(error) from error

    @router.post("/forward-assessments", status_code=201)
    def create_forward_assessment(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ForwardRiskService(repository).create_assessment(
                project_name=str(payload.get("project_name") or ""),
                product_code=str(payload.get("product_code") or ""),
                assessment_stage=str(payload.get("assessment_stage") or ""),
                material_type=str(payload.get("material_type") or "TEXT"),
                material_name=str(payload.get("material_name") or ""),
                material_text=str(payload.get("material_text") or ""),
                created_by=str(payload.get("created_by") or ""),
            )
        except ForwardRiskError as error:
            raise _http_error(error) from error

    @router.get("/forward-assessments/{assessment_id}")
    def forward_assessment(assessment_id: str) -> dict[str, Any]:
        try:
            return ForwardRiskService(repository).get_assessment(assessment_id)
        except ForwardRiskError as error:
            raise _http_error(error) from error

    @router.post("/forward-assessments/{assessment_id}/versions", status_code=201)
    def reassess_forward_assessment(assessment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ForwardRiskService(repository).reassess(
                assessment_id,
                assessment_stage=str(payload.get("assessment_stage") or ""),
                material_type=str(payload.get("material_type") or "TEXT"),
                material_name=str(payload.get("material_name") or ""),
                material_text=str(payload.get("material_text") or ""),
                created_by=str(payload.get("created_by") or ""),
            )
        except ForwardRiskError as error:
            raise _http_error(error) from error

    @router.post("/forward-risk-results/{risk_result_id}/review")
    def review_forward_risk(risk_result_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ForwardRiskService(repository).review_result(
                risk_result_id,
                status=str(payload.get("status") or ""),
                note=str(payload.get("note") or ""),
                reviewed_by=str(payload.get("reviewed_by") or ""),
            )
        except ForwardRiskError as error:
            raise _http_error(error) from error

    def insight_filters(business_type: str, month: str, issue_domain: str, lifecycle_phase: str) -> dict[str, str]:
        return {key: value for key, value in {
            "business_type": business_type, "month": month,
            "issue_domain": issue_domain, "lifecycle_phase": lifecycle_phase,
        }.items() if value}

    @router.get("/insights/business-contradictions")
    def contradictions(business_type: str = "", month: str = "", issue_domain: str = "", lifecycle_phase: str = "") -> dict[str, Any]:
        from quality_knowledge.p0.insight_service import InsightService
        return InsightService(repository).business_contradictions(
            insight_filters(business_type, month, issue_domain, lifecycle_phase)
        )

    @router.get("/insights/business-contradictions/{key}/issues")
    def drilldown(
        key: str, analysis_scope_hash: str, business_type: str = "", month: str = "",
        issue_domain: str = "", lifecycle_phase: str = "", page: int = 1, page_size: int = 100,
    ) -> dict[str, Any]:
        from quality_knowledge.p0.insight_service import InsightService, P0InsightError
        try:
            return InsightService(repository).drilldown(
                key, analysis_scope_hash,
                insight_filters(business_type, month, issue_domain, lifecycle_phase),
                page, page_size,
            )
        except P0InsightError as error:
            raise _http_error(error) from error


    @router.post("/quality-scenarios/candidates/from-reverse")
    def create_quality_scenario_candidate(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("reverse_quality_result")
        taxonomy = payload.get("taxonomy")
        if not isinstance(result, dict):
            raise HTTPException(400, "REVERSE_QUALITY_RESULT_REQUIRED")
        if not isinstance(taxonomy, dict):
            raise HTTPException(400, "SCENARIO_TAXONOMY_REQUIRED")
        try:
            produced = scenario_candidates.create_from_reverse(
                result,
                taxonomy,
                trigger_source=payload.get("trigger_source"),
                trigger_reason=str(payload.get("trigger_reason") or ""),
                created_by=str(payload.get("created_by") or ""),
            )
            return produced.to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios/candidates")
    def quality_scenario_candidates(
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        items = scenario_candidates.list_candidates(
            product_code=product_code,
            lifecycle_stage_code=lifecycle_stage_code,
            business_activity_code=business_activity_code,
            quality_concern_code=quality_concern_code,
            q=q,
        )
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": len(items),
        }

    @router.get("/quality-scenarios/candidates/{scenario_id}")
    def quality_scenario_candidate(scenario_id: str) -> dict[str, Any]:
        item = scenario_candidates.get_candidate(scenario_id)
        if item is None:
            raise HTTPException(404, "QUALITY_SCENARIO_V1_CANDIDATE_NOT_FOUND")
        return item.model_dump(mode="json")


    def _expected_version(payload: dict[str, Any]) -> int:
        try:
            value = int(payload.get("expected_scenario_version") or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            raise HTTPException(400, "EXPECTED_SCENARIO_VERSION_REQUIRED")
        return value


    @router.get("/quality-scenario-sources/scenarios")
    def quality_scenarios_for_source(source_ref: str = "") -> dict[str, Any]:
        try:
            return scenario_traceability.scenarios_for_source(source_ref)
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios")
    def quality_scenarios_v1(
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        status: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        try:
            items = scenario_workflow.list_scenarios(
                product_code=product_code,
                lifecycle_stage_code=lifecycle_stage_code,
                business_activity_code=business_activity_code,
                quality_concern_code=quality_concern_code,
                status=status or None,
                q=q,
            )
        except ValueError as error:
            raise _http_error(error) from error
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": len(items),
        }

    @router.get("/quality-scenarios/{scenario_id}")
    def quality_scenario_v1_detail(scenario_id: str) -> dict[str, Any]:
        try:
            return scenario_workflow.get(scenario_id).model_dump(mode="json")
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios/{scenario_id}/traceability")
    def quality_scenario_v1_traceability(scenario_id: str) -> dict[str, Any]:
        try:
            return scenario_traceability.trace(scenario_id)
        except ValueError as error:
            raise _http_error(error) from error

    @router.get("/quality-scenarios/{scenario_id}/history")
    def quality_scenario_v1_history(scenario_id: str) -> dict[str, Any]:
        try:
            return scenario_workflow.history(scenario_id)
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/review")
    def review_quality_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return scenario_workflow.review_candidate(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                patch=payload.get("patch") if isinstance(payload.get("patch"), dict) else {},
                review_status=str(payload.get("review_status") or "PENDING"),
                reviewer=str(payload.get("reviewer") or ""),
                reviewed_at=str(payload.get("reviewed_at") or ""),
                comment=str(payload.get("comment") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/confirm")
    def confirm_quality_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return scenario_workflow.confirm(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                quality_confirmed_by=str(payload.get("quality_confirmed_by") or ""),
                quality_confirmed_at=str(payload.get("quality_confirmed_at") or ""),
                technical_confirmed_by=str(payload.get("technical_confirmed_by") or ""),
                technical_confirmed_at=str(payload.get("technical_confirmed_at") or ""),
                confirmation_note=str(payload.get("confirmation_note") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/reject")
    def reject_quality_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return scenario_workflow.reject(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                reviewer=str(payload.get("reviewer") or ""),
                reviewed_at=str(payload.get("reviewed_at") or ""),
                comment=str(payload.get("comment") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    @router.post("/quality-scenarios/{scenario_id}/publish")
    def publish_quality_scenario(scenario_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return scenario_workflow.publish(
                scenario_id,
                expected_scenario_version=_expected_version(payload),
                published_by=str(payload.get("published_by") or ""),
                published_at=str(payload.get("published_at") or ""),
            ).to_dict()
        except ValueError as error:
            raise _http_error(error) from error

    return router
