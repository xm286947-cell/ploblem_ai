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

from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Callable

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_ai_retrieval import HardwareCaseAIRetrievalError
from services.hardware_case_contract import HardwareCaseContractError
from services.hardware_case_source_store import HardwareCaseSourceError, HardwareCaseSourceStore
from services.hardware_case_intake import HardwareCaseIntakeError, HardwareCaseIntakeService
from services.hardware_case_markdown_agent import (
    HardwareCaseMarkdownError,
    build_markdown_view,
    run_r1_agent_extraction,
)
from services.hardware_case_r1_preview_store import HardwareR1PreviewStore
from services.hardware_case_word import HardwareWordParseError, parse_docx


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
    source_store: HardwareCaseSourceStore | None = None,
    intake_service: HardwareCaseIntakeService | None = None,
    r1_structurer_factory: Callable[[], Any] | None = None,
    r1_preview_store: HardwareR1PreviewStore | None = None,
    r1_stage_cache_invalidator: Callable[[str], int] | None = None,
    ai_search_service: Any | None = None,
    retrieval_catalog_service: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["hardware-case"])

    @router.post("/r1/word-snapshot")
    async def r1_word_snapshot(
        file: UploadFile = File(...),
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        """Parse one DOCX into the frozen R1 DocumentSnapshot for field validation.

        This is intentionally parse-only: no Agent, tree mapping, search, publish,
        or revision workflow is invoked.  It reuses parse_docx().to_snapshot().
        """
        _require_maintainer(x_hardware_case_role)
        raw_name = str(file.filename or "").replace("\\", "/")
        filename = Path(raw_name).name
        if not filename or not filename.lower().endswith(".docx"):
            raise HTTPException(status_code=400, detail="DOCX_REQUIRED")
        payload = await file.read()
        if not payload:
            raise HTTPException(status_code=400, detail="DOCX_EMPTY")
        with TemporaryDirectory(prefix="hardware-r1-word-") as temporary:
            source = Path(temporary) / filename
            source.write_bytes(payload)
            try:
                parse_started = perf_counter()
                snapshot = parse_docx(source).to_snapshot()
                parse_ms = max(0, int((perf_counter() - parse_started) * 1000))
                markdown_started = perf_counter()
                snapshot["markdown_view"] = build_markdown_view(snapshot)
                markdown_ms = max(0, int((perf_counter() - markdown_started) * 1000))
                metadata = snapshot.setdefault("metadata", {})
                metadata["r1_latency_trace"] = {
                    "PARSE_MS": parse_ms,
                    "MARKDOWN_MS": markdown_ms,
                }

                identity = snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {}
                business_case_id = str(identity.get("business_case_id") or "").strip()
                if source_store is not None and business_case_id:
                    try:
                        snapshot["source_binding"] = source_store.register_active_bytes(
                            business_case_id,
                            filename,
                            payload,
                            mime_type=file.content_type,
                        )
                    except HardwareCaseSourceError as error:
                        if error.code == "SOURCE_ALREADY_EXISTS":
                            raise HTTPException(status_code=409, detail=error.code) from error
                        if error.code in {
                            "SOURCE_OPERATION_JOURNAL_UNAVAILABLE",
                            "SOURCE_UPLOAD_COMMIT_FAILED",
                            "SOURCE_UPLOAD_STAGING_FAILED",
                            "SOURCE_RECOVERY_REQUIRED",
                            "SOURCE_OPERATION_JOURNAL_INVALID",
                        }:
                            raise HTTPException(status_code=503, detail=error.code) from error
                        raise HTTPException(status_code=400, detail=error.code) from error
                else:
                    snapshot["source_binding"] = {
                        "binding_status": "UNBOUND_IDENTITY_REVIEW",
                        "business_case_id": business_case_id or None,
                    }
                return snapshot
            except (HardwareWordParseError, HardwareCaseMarkdownError) as error:
                raise HTTPException(status_code=400, detail=error.code) from error

    @router.get("/r1/sources/{business_case_id}")
    def r1_get_active_source(
        business_case_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if source_store is None:
            raise HTTPException(status_code=503, detail="SOURCE_STORE_UNAVAILABLE")
        try:
            return source_store.get_active_source(business_case_id)
        except HardwareCaseSourceError as error:
            status = 404 if error.code == "SOURCE_NOT_REGISTERED" else 409
            raise HTTPException(status_code=status, detail=error.code) from error

    @router.delete("/r1/sources/{business_case_id}")
    def r1_delete_active_source(
        business_case_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if source_store is None:
            raise HTTPException(status_code=503, detail="SOURCE_STORE_UNAVAILABLE")
        try:
            active = source_store.get_active_source(business_case_id)
            refs = source_store.formal_knowledge_references(business_case_id)
            if refs:
                raise HardwareCaseSourceError(
                    "SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE"
                )
            source_id = str(active["source_id"])
            result = source_store.delete_active_source(
                business_case_id,
                deleted_by="MAINTAINER",
            )
            preview_deleted = (
                r1_preview_store.delete_source(source_id)
                if r1_preview_store is not None
                else 0
            )
            cache_deleted = (
                int(r1_stage_cache_invalidator(source_id))
                if r1_stage_cache_invalidator is not None
                else 0
            )
            return {
                **result,
                "preview_records_invalidated": preview_deleted,
                "stage_cache_records_invalidated": cache_deleted,
                "runtime_audit_preserved": True,
            }
        except HardwareCaseSourceError as error:
            if error.code == "SOURCE_NOT_REGISTERED":
                status = 404
            elif error.code in {
                "SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE",
                "SOURCE_ALREADY_EXISTS",
                "SOURCE_DELETE_BLOCKED_BY_PROMOTION",
                "SOURCE_RECOVERY_LOCKED",
            }:
                status = 409
            elif error.code in {
                "SOURCE_OPERATION_JOURNAL_UNAVAILABLE",
                "SOURCE_OPERATION_JOURNAL_INVALID",
                "SOURCE_CANDIDATE_STORE_UNAVAILABLE",
                "SOURCE_CANDIDATE_INVALIDATION_FAILED",
                "SOURCE_RECOVERY_REQUIRED",
                "SOURCE_DELETE_RECOVERY_CONFLICT",
                "SOURCE_DELETE_RECOVERY_REQUIRED",
                "SOURCE_OPERATION_QUARANTINE_CONFLICT",
            }:
                status = 503
            else:
                status = 400
            raise HTTPException(status_code=status, detail=error.code) from error

    @router.get("/r1/source-delete-audit")
    def r1_source_delete_audit(
        business_case_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if source_store is None:
            raise HTTPException(status_code=503, detail="SOURCE_STORE_UNAVAILABLE")
        items = source_store.list_delete_audit(
            business_case_id=business_case_id,
            limit=limit,
        )
        return {"items": items, "total": len(items)}

    @router.post("/r1/agent-extract")
    def r1_agent_extract(
        payload: dict[str, Any],
        force_retry: bool = Query(default=False),
        force_full_run: bool = Query(default=False),
        retry_failed_stage: str | None = Query(default=None),
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        """Run/Resume, selectively retry one failed stage, or force a full R1 run."""
        _require_maintainer(x_hardware_case_role)
        retry_stage = str(retry_failed_stage or "").strip().upper() or None
        if retry_stage not in {None, "STAGE_A", "STAGE_B"}:
            raise HTTPException(status_code=400, detail="RETRY_FAILED_STAGE_INVALID")
        full_run = bool(force_full_run or force_retry)
        if full_run and retry_stage is not None:
            raise HTTPException(status_code=400, detail="EXECUTION_MODE_CONFLICT")
        factory = r1_structurer_factory
        if factory is None:
            from services.hardware_case_r1_runtime import build_hardware_case_r1_structurer
            factory = build_hardware_case_r1_structurer
        try:
            structurer = factory()
            result = run_r1_agent_extraction(
                payload,
                structurer,
                force_retry=full_run,
                retry_failed_stage=retry_stage,
            )
            if r1_preview_store is not None:
                result["preview"] = r1_preview_store.save(payload, result)
            return result
        except HardwareCaseMarkdownError as error:
            raise HTTPException(status_code=400, detail=error.code) from error
        except Exception as error:
            code = str(getattr(error, "code", None) or "RUNTIME_EXECUTION_FAILED")
            from services.hardware_case_r1_runtime import map_r1_runtime_error
            mapped = map_r1_runtime_error(code)
            status = 503 if mapped == "RUNTIME_CONFIG_MISSING" else 502
            raise HTTPException(
                status_code=status,
                detail={
                    "pipeline_status": "CASE_EXTRACTION_FAILED",
                    "failed_stage": "STAGE_A",
                    "error_code": mapped,
                    "raw_error_code": code,
                    "run_id": None,
                    "task_id": None,
                    "provider_call_count": 0,
                    "validation_retry_count": 0,
                },
            ) from error

    @router.get("/r1/previews")
    def r1_list_previews(
        source_id: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if r1_preview_store is None:
            raise HTTPException(status_code=503, detail="R1_PREVIEW_STORE_UNAVAILABLE")
        items = r1_preview_store.list(source_id=source_id, limit=limit)
        return {"items": items, "total": len(items)}

    @router.get("/r1/previews/latest")
    def r1_latest_preview(
        source_id: str | None = Query(default=None),
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if r1_preview_store is None:
            raise HTTPException(status_code=503, detail="R1_PREVIEW_STORE_UNAVAILABLE")
        item = r1_preview_store.latest(source_id=source_id)
        if item is None:
            raise HTTPException(status_code=404, detail="R1_PREVIEW_NOT_FOUND")
        return item

    @router.get("/r1/previews/by-run/{run_id}")
    def r1_preview_by_run(
        run_id: str,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if r1_preview_store is None:
            raise HTTPException(status_code=503, detail="R1_PREVIEW_STORE_UNAVAILABLE")
        item = r1_preview_store.by_run_id(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="R1_PREVIEW_NOT_FOUND")
        return item

    @router.get("/r1/previews/{preview_id}")
    def r1_preview_by_id(
        preview_id: int,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if r1_preview_store is None:
            raise HTTPException(status_code=503, detail="R1_PREVIEW_STORE_UNAVAILABLE")
        item = r1_preview_store.by_id(preview_id)
        if item is None:
            raise HTTPException(status_code=404, detail="R1_PREVIEW_NOT_FOUND")
        return item

    @router.delete("/r1/previews/{preview_id}")
    def r1_delete_preview(
        preview_id: int,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if r1_preview_store is None:
            raise HTTPException(status_code=503, detail="R1_PREVIEW_STORE_UNAVAILABLE")
        if not r1_preview_store.delete(preview_id):
            raise HTTPException(status_code=404, detail="R1_PREVIEW_NOT_FOUND")
        return {
            "status": "PASS",
            "preview_id": int(preview_id),
            "deleted": True,
            "scope": "LOCAL_GOLDEN_PREVIEW_ONLY",
        }

    @router.delete("/r1/previews")
    def r1_clear_previews(
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if r1_preview_store is None:
            raise HTTPException(status_code=503, detail="R1_PREVIEW_STORE_UNAVAILABLE")
        deleted_count = r1_preview_store.clear()
        return {
            "status": "PASS",
            "deleted_count": deleted_count,
            "scope": "LOCAL_GOLDEN_PREVIEW_ONLY",
        }

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
            search_service = ai_search_service or service
            return search_service.search_cases(
                q,
                role=role,
                statuses=status,
                historical=historical,
            )
        except (HardwareCaseContractError, HardwareCaseAIRetrievalError) as error:
            raise _http_error(error) from error

    @router.get("/retrieval/status")
    def retrieval_status() -> dict[str, Any]:
        return {
            "status": "READY" if ai_search_service is not None else "LEGACY_ONLY",
            "ai_search_enabled": ai_search_service is not None,
            "index_rebuild_available": retrieval_catalog_service is not None,
            "formal_knowledge_write": False,
        }

    @router.post("/retrieval/rebuild")
    def rebuild_retrieval_index(
        generation_id: str | None = None,
        x_hardware_case_role: str | None = Header(
            default=None, alias="X-Hardware-Case-Role"
        ),
    ) -> dict[str, Any]:
        _require_maintainer(x_hardware_case_role)
        if retrieval_catalog_service is None:
            raise HTTPException(
                status_code=503,
                detail="HARDWARE_RETRIEVAL_REBUILD_UNAVAILABLE",
            )
        try:
            return retrieval_catalog_service.rebuild_all(generation_id)
        except HardwareCaseAIRetrievalError as error:
            raise HTTPException(status_code=503, detail=error.code) from error

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

    if source_store is not None:
        @router.post("/sources/upload", status_code=201)
        async def upload_source(
            file: UploadFile = File(...),
            source_ref: str | None = Form(default=None),
            x_hardware_case_role: str | None = Header(
                default=None, alias="X-Hardware-Case-Role"
            ),
        ) -> dict[str, Any]:
            _require_maintainer(x_hardware_case_role)
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
                if code in {
                    "SOURCE_OPERATION_JOURNAL_UNAVAILABLE",
                    "SOURCE_UPLOAD_COMMIT_FAILED",
                    "SOURCE_UPLOAD_STAGING_FAILED",
                    "SOURCE_RECOVERY_REQUIRED",
                    "SOURCE_UPLOAD_RECONCILIATION_CONFLICT",
                    "SOURCE_OPERATION_JOURNAL_INVALID",
                    "SOURCE_OPERATION_QUARANTINE_CONFLICT",
                }:
                    raise HTTPException(status_code=503, detail=code) from error
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
            _require_maintainer(x_hardware_case_role)
            try:
                return intake_service.upload(str(file.filename or ""), await file.read())
            except (HardwareCaseIntakeError, HardwareCaseSourceError) as error:
                raise _intake_error(error) from error

        @router.get("/intakes")
        def list_intakes(x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role")) -> dict[str, Any]:
            _require_maintainer(x_hardware_case_role)
            items = intake_service.list()
            return {"items": items, "total": len(items)}

        @router.get("/intakes/{intake_id}")
        def get_intake(intake_id: str, x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role")) -> dict[str, Any]:
            _require_maintainer(x_hardware_case_role)
            try:
                return intake_service.detail(intake_id)
            except HardwareCaseIntakeError as error:
                raise _intake_error(error) from error

        @router.post("/intakes/{intake_id}/process")
        def process_intake(intake_id: str, x_hardware_case_role: str | None = Header(default=None, alias="X-Hardware-Case-Role")) -> dict[str, Any]:
            _require_maintainer(x_hardware_case_role)
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
            if error.code in {
                "SOURCE_OPERATION_JOURNAL_UNAVAILABLE",
                "SOURCE_OPERATION_JOURNAL_INVALID",
                "SOURCE_RECOVERY_REQUIRED",
                "SOURCE_CANDIDATE_STORE_UNAVAILABLE",
                "SOURCE_CANDIDATE_INVALIDATION_FAILED",
            }:
                return HTTPException(status_code=503, detail=error.code)
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
            role = _role(x_hardware_case_role)
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
            role = _role(x_hardware_case_role)
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
            role = _role(x_hardware_case_role)
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
