from __future__ import annotations

from pathlib import Path
import json
import threading
import time
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import core
from . import case_adapter
from . import knowledge as knowledge_store
from . import knowledge_contracts as kc
from . import ai
from . import templates
from . import document_pipeline
from . import product_api
from . import knowledge_product
from .knowledge_release import KnowledgeReleaseConsumer, KnowledgeReleaseError

app = FastAPI(title="存储器件寿命知识库 MVP", version="0.8.0-rc3-runtime-rc2.1")

IMPORT_JOBS = {}
IMPORT_JOBS_LOCK = threading.Lock()

def _update_import_job(job_id: str, **changes):
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if job is not None:
            job.update(changes)
            job["updated_at"] = time.time()

def _get_import_job(job_id: str):
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        return dict(job) if job else None



class Review(BaseModel):
    status: str
    value: str | None = None
    unit: str | None = None
    condition: str | None = None
    scope: str | None = None
    verified_by: str


class ModelReview(BaseModel):
    status: str
    value: str | None = None
    scope: str | None = None
    verified_by: str


class Comparison(BaseModel):
    device_ids: list[str]


class Link(BaseModel):
    device_id: str
    target_system: str
    target_id: str
    relation: str


class SourceReview(BaseModel):
    status: str
    verified_by: str


class AnalysisRequest(BaseModel):
    question: str
    old_id: str | None = None
    new_id: str | None = None




class ProductCompareRequest(BaseModel):
    device_ids: list[str]


class KnowledgeReleaseBuildRequest(BaseModel):
    release_version: str



@app.get("/api/product/dashboard", tags=["Storage Product MVP"])
def product_dashboard():
    return product_api.dashboard()


@app.get("/api/product/devices/{device_id}", tags=["Storage Product MVP"])
def product_device_detail(device_id: str):
    try:
        return product_api.device_slots(device_id)
    except KeyError:
        raise HTTPException(404, "器件不存在")


@app.get("/api/product/devices/{device_id}/facts", tags=["Storage Product MVP"])
def product_device_facts(device_id: str):
    try:
        return product_api.confirmed_device_facts(device_id)
    except KeyError:
        raise HTTPException(404, "器件不存在")


@app.get("/api/product/devices/{device_id}/review-workbench", tags=["Storage Product MVP"])
def product_review_workbench(device_id: str):
    try:
        return product_api.review_workbench(device_id)
    except KeyError:
        raise HTTPException(404, "器件不存在")


@app.post("/api/product/compare", tags=["Storage Product MVP"])
def product_compare(body: ProductCompareRequest):
    try:
        return product_api.compare_devices(body.device_ids)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/product/diagnostics", tags=["Storage Product MVP"])
def product_diagnostics(device_type: str = "", device_id: str = ""):
    try:
        return product_api.diagnostics(device_type=device_type, device_id=device_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/product/change-impact", tags=["Storage Product MVP"])
def product_change_impact(old_id: str, new_id: str):
    try:
        return product_api.change_impact(old_id, new_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/product/maintenance", tags=["Storage Product MVP"])
def product_maintenance():
    return product_api.maintenance()


@app.get("/api/product/knowledge/status", tags=["Storage Product MVP"])
def product_knowledge_status():
    return KnowledgeReleaseConsumer.current().status()


@app.get("/api/product/knowledge/query", tags=["Storage Product MVP"])
def product_knowledge_query(q: str, device_type: str = "", top_k: int = 8):
    try:
        return KnowledgeReleaseConsumer.current().query(q, device_type=device_type, top_k=top_k)
    except KnowledgeReleaseError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/product/knowledge/evidence/{evidence_id}", tags=["Storage Product MVP"])
def product_knowledge_evidence(evidence_id: str):
    try:
        return KnowledgeReleaseConsumer.current().evidence(evidence_id)
    except KnowledgeReleaseError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/product/knowledge-production/sources", tags=["Storage Product MVP"])
async def knowledge_production_source(
    file: UploadFile = File(...),
    source_id: str = Form(...),
    publisher: str = Form(...),
    title: str = Form(...),
    version: str = Form(""),
    revision: str = Form(""),
    official_url: str = Form(""),
):
    data = await file.read()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "知识资料文件上限 30 MB")
    try:
        return await run_in_threadpool(
            knowledge_product.ingest_source,
            data,
            filename=file.filename or "source.pdf",
            source_id=source_id,
            publisher=publisher,
            title=title,
            version=version,
            revision=revision,
            official_url=official_url,
        )
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/product/knowledge-production/sources/{source_id}/{source_version}/extract", tags=["Storage Product MVP"])
async def knowledge_production_extract(source_id: str, source_version: str, requested_topics: str = Form("")):
    topics = [item.strip() for item in requested_topics.split(",") if item.strip()]
    try:
        return await run_in_threadpool(
            knowledge_product.extract_source,
            source_id,
            source_version,
            requested_topics=topics,
        )
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/product/knowledge-production/releases", tags=["Storage Product MVP"])
async def knowledge_production_release(body: KnowledgeReleaseBuildRequest):
    try:
        result = await run_in_threadpool(
            knowledge_product.build_and_activate_release,
            body.release_version,
        )
        return {**result, "consumer_status": KnowledgeReleaseConsumer.current().status()}
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "storage-life", "version": app.version}


@app.get("/", response_class=HTMLResponse)
def home():
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


@app.get("/api/devices")
def devices():
    return core.list_devices()


@app.get("/api/spec-fields")
def spec_fields(device_type: str):
    try:
        return ai.expected_fields(device_type)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/spec-template")
def spec_template(device_type: str, vendor: str = ""):
    try:
        return templates.template_summary(device_type, vendor)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/documents/identify")
async def identify_document(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "文件上限 20 MB")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(422, "当前仅接收 PDF")
    try:
        # P6 targeted fast path: inspect the first three pages locally, then use TOC headings
        # to add only high-value overview pages before the first AI call. This avoids the common
        # 3-page AI call -> 6-page AI call pattern while preserving P4's six-page fallback.
        identify_started = time.perf_counter()
        try:
            seed_pages = await run_in_threadpool(core.extract_pdf, data, 3)
        except TypeError as exc:
            if "positional argument" not in str(exc):
                raise
            seed_pages = await run_in_threadpool(core.extract_pdf, data)

        def _toc_target_pages(seed):
            import re
            text = "\n".join(str(x[1] or "") for x in seed)
            targets = []
            # Overview pages are useful for vendor/family/type identity. Ordering/part-number
            # pages deliberately stay out of basic identity and are handled by the model flow.
            patterns = (
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*FEATURES?\s*\.{2,}\s*(\d{1,3})\s*$",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*GENERAL\s+DESCRIPTIONS?\s*\.{2,}\s*(\d{1,3})\s*$",
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*PRODUCT\s+(?:OVERVIEW|DESCRIPTION)\s*\.{2,}\s*(\d{1,3})\s*$",
            )
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.I | re.M):
                    page_no = int(match.group(1))
                    if 1 <= page_no <= 12 and page_no not in targets:
                        targets.append(page_no)
            return targets[:2]

        targeted_pages = _toc_target_pages(seed_pages)
        pages = seed_pages
        identify_mode = "fast_3_pages"
        if targeted_pages:
            wanted = sorted({1, 2, 3, *targeted_pages})
            try:
                pages = await run_in_threadpool(core.extract_pdf_pages, data, wanted)
                identify_mode = "targeted_toc_pages"
            except (TypeError, ValueError):
                pages = seed_pages
                targeted_pages = []

        document_md, md_pages, md_stats = await run_in_threadpool(document_pipeline.build_markdown, data, pages)
        result = await run_in_threadpool(ai.identify_device, md_pages)

        def _identity_sufficient(value):
            for field in ("vendor", "model", "device_type"):
                item = value.get(field) or {}
                if not str(item.get("value") or "").strip():
                    return False
                try:
                    if float(item.get("confidence") or 0) < 0.70:
                        return False
                except (TypeError, ValueError):
                    return False
            return True

        if not _identity_sufficient(result):
            # P4 compatibility fallback: if targeted evidence is still insufficient, use the
            # bounded first six pages rather than scanning the document.
            try:
                pages = await run_in_threadpool(core.extract_pdf, data, 6)
            except TypeError as exc:
                if "positional argument" not in str(exc):
                    raise
                pages = await run_in_threadpool(core.extract_pdf, data)
            document_md, md_pages, md_stats = await run_in_threadpool(document_pipeline.build_markdown, data, pages)
            result = await run_in_threadpool(ai.identify_device, md_pages)
            identify_mode = "expanded_6_pages_fallback"

        result["identify_mode"] = identify_mode
        result["identify_targeted_pages"] = targeted_pages
        result["identify_input_pages"] = [p[0] for p in md_pages]
        result["identify_input_chars"] = sum(len(p[1]) for p in md_pages)
        result["identify_elapsed_ms"] = round((time.perf_counter() - identify_started) * 1000)
        vendor = result.get("vendor", {}).get("value", "")
        device_type = result.get("device_type", {}).get("value", "")
        family = result.get("model", {}).get("value", "")

        # P6.1: vendor is allowed to stay empty when the PDF does not explicitly print it,
        # but a configured manufacturer-specific model prefix may be surfaced as a clearly
        # marked candidate for human confirmation. It is never treated as formal evidence.
        if not vendor and family:
            marker = str(family).strip().casefold()
            for _key, _item in (templates.load_templates().get("vendors") or {}).items():
                for _alias in (_item.get("aliases") or []):
                    alias = str(_alias or "").strip()
                    if len(alias) >= 2 and marker.startswith(alias.casefold()):
                        canonical = str(_item.get("canonical_name") or "").strip()
                        if canonical:
                            result["vendor"] = {
                                "value": canonical, "page": int((result.get("model") or {}).get("page") or 0),
                                "quote": str((result.get("model") or {}).get("quote") or family),
                                "confidence": 0.70, "basis": "configured_model_prefix_candidate",
                            }
                            vendor = canonical
                        break
                if vendor:
                    break
        fallback_identity = document_pipeline.heuristic_identity(md_pages, vendor, family)
        try:
            agent_identity = await run_in_threadpool(ai.identify_document_identity, md_pages, vendor, family) if ai.configured() else None
        except ai.AIResponseError:
            agent_identity = None
        result["document_identity"] = core._merge_document_identity(agent_identity, fallback_identity, vendor, family)
        result["markdown"] = md_stats
        if device_type:
            result["template"] = templates.template_summary(device_type, vendor)
            result["planned_pages"] = [x["page"] for x in templates.build_read_plan(md_pages, device_type, vendor)]
            if ai.configured():
                # P6.1: part-number discovery has its own page plan. Read the TOC from the
                # basic-identity pages and jump directly to Ordering Information / Valid Part
                # Numbers instead of reusing the basic-identity or parameter-extraction pages.
                import re
                toc_text = "\n".join(str(x[1] or "") for x in seed_pages)
                model_pages = []
                for pattern in (
                    r"(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*ORDERING\s+INFORMATION\s*\.{2,}\s*(\d{1,3})\s*$",
                    r"(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*VALID\s+PART\s+NUMBERS?\s*\.{2,}\s*(\d{1,3})\s*$",
                    r"(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*PART\s+NUMBER(?:ING)?(?:\s+INFORMATION)?\s*\.{2,}\s*(\d{1,3})\s*$",
                ):
                    for match in re.finditer(pattern, toc_text, re.I | re.M):
                        page_no = int(match.group(1))
                        for n in (page_no, page_no + 1):
                            if n not in model_pages:
                                model_pages.append(n)
                model_md_pages = md_pages
                if model_pages:
                    try:
                        raw_model_pages = await run_in_threadpool(core.extract_pdf_pages, data, model_pages[:4])
                        _, model_md_pages, _ = await run_in_threadpool(document_pipeline.build_markdown, data, raw_model_pages)
                    except (TypeError, ValueError):
                        model_md_pages = md_pages
                model_result = await run_in_threadpool(ai.identify_models, model_md_pages, device_type, vendor, family)
                result["models"] = model_result.get("models", [])
                result["model_analyzed_pages"] = model_result.get("analyzed_pages", [])
                result["model_targeted_pages"] = model_pages[:4]
            else:
                result["models"] = []
                result["model_analyzed_pages"] = []
        else:
            result["models"] = []
            result["model_analyzed_pages"] = []
        return result
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ai.AIResponseError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/documents/jobs", status_code=202)
async def create_import_job(file: UploadFile = File(...), vendor: str = Form(...), model: str = Form(...),
                            device_type: str = Form(...), original_url: str = Form(""), publisher: str = Form(""),
                            models_json: str = Form("[]"), document_number: str = Form(""), revision: str = Form(""),
                            revision_date: str = Form(""), document_variant: str = Form("")):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "文件上限 20 MB")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(422, "当前仅接收 PDF")
    try:
        model_candidates = json.loads(models_json or "[]")
        if not isinstance(model_candidates, list):
            raise ValueError("models_json 必须是数组")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(422, "型号候选数据格式错误") from e

    job_id = uuid4().hex
    now = time.time()
    with IMPORT_JOBS_LOCK:
        IMPORT_JOBS[job_id] = {
            "job_id": job_id, "status": "queued", "stage": "queued", "progress": 3,
            "message": "文件已接收，等待开始导入…", "created_at": now, "updated_at": now,
            "result": None, "error": None,
        }

    def progress(stage, percent, message):
        _update_import_job(job_id, status="running", stage=stage, progress=percent, message=message)

    def worker():
        try:
            _update_import_job(job_id, status="running", stage="starting", progress=5, message="开始导入规格书…")
            identity_override = {"document_number": document_number, "revision": revision,
                                 "revision_date": revision_date, "document_variant": document_variant}
            if any(str(v or "").strip() for v in identity_override.values()):
                result = core.import_document(file.filename or "", data, vendor, model, device_type,
                                              original_url, publisher, model_candidates, progress=progress,
                                              document_identity_override=identity_override)
            else:
                result = core.import_document(file.filename or "", data, vendor, model, device_type,
                                              original_url, publisher, model_candidates, progress=progress)
            _update_import_job(job_id, status="completed", stage="completed", progress=100,
                               message="导入完成，可进入型号与参数核对。", result=result)
        except Exception as e:
            _update_import_job(job_id, status="failed", stage="failed",
                               message=f"导入失败：{e}", error=str(e))

    threading.Thread(target=worker, name=f"storage-life-import-{job_id[:8]}", daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/documents/jobs/{job_id}")
def import_job_status(job_id: str):
    job = _get_import_job(job_id)
    if not job:
        raise HTTPException(404, "导入任务不存在或服务已重启")
    return job


@app.post("/api/documents", status_code=201)
async def upload(file: UploadFile = File(...), vendor: str = Form(...), model: str = Form(...),
                 device_type: str = Form(...), original_url: str = Form(""), publisher: str = Form(""),
                 models_json: str = Form("[]"), document_number: str = Form(""), revision: str = Form(""),
                 revision_date: str = Form(""), document_variant: str = Form("")):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "文件上限 20 MB")
    try:
        try:
            model_candidates = json.loads(models_json or "[]")
            if not isinstance(model_candidates, list):
                raise ValueError("models_json 必须是数组")
        except json.JSONDecodeError as e:
            raise ValueError("型号候选数据格式错误") from e
        identity_override = {"document_number": document_number, "revision": revision,
                             "revision_date": revision_date, "document_variant": document_variant}
        if any(str(v or "").strip() for v in identity_override.values()):
            return await run_in_threadpool(core.import_document, file.filename or "", data,
                                           vendor, model, device_type, original_url, publisher, model_candidates, None,
                                           identity_override)
        return await run_in_threadpool(core.import_document, file.filename or "", data,
                                       vendor, model, device_type, original_url, publisher, model_candidates)
    except core.DuplicateDocumentError as e:
        raise HTTPException(409, str(e)) from e
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ai.AIResponseError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/v1/ai/status", tags=["AI Analysis v1"])
def ai_status():
    info = ai.status()
    return {**info, "extraction_mode": "agent" if info["configured"] else "agent_unconfigured", "template_version": 1}


@app.get("/api/v1/runtime/status", tags=["Runtime Integration"])
def runtime_status():
    from . import runtime_bridge
    return runtime_bridge.status()


@app.get("/api/v1/runtime/executions", tags=["Runtime Integration"])
def runtime_executions():
    from . import runtime_bridge
    return {"items": runtime_bridge.last_executions()}


@app.get("/api/v1/runtime/contract/emmc", tags=["Runtime Integration"])
def runtime_emmc_contract():
    """Expose the Storage-owned eMMC Runtime contract for product testing.

    This is a Storage business contract view, not a Runtime platform contract.
    """
    from .runtime_domain_strategy import EMMC_ATOMIC_GROUPS, EMMC_FIELD_ORDER
    return {
        "contract": "StorageParameterExtractResultV1",
        "strategy_ref": "storage_emmc_field_groups@1",
        "field_count": len(EMMC_FIELD_ORDER),
        "fields": list(EMMC_FIELD_ORDER),
        "atomic_groups": [
            {"name": name, "fields": list(fields)}
            for name, fields in EMMC_ATOMIC_GROUPS
        ],
        "runtime_boundary": {
            "storage_owns": ["schema", "prompt", "domain_strategy", "business_gate", "web_flow"],
            "runtime_owns": ["task", "retry", "budget", "resume", "checkpoint", "provider_calls", "execution_state"],
        },
    }


@app.get("/api/devices/{device_id}/runtime-facts", tags=["Runtime Integration"])
def runtime_device_facts(device_id: str):
    run = core.get_extraction_run(device_id)
    if not run:
        raise HTTPException(404, "未找到该器件的抽取运行记录")
    facts = list(run.get("facts") or [])
    expected = list(run.get("expected_fields") or [])
    with core.connect() as con:
        src = con.execute("SELECT id,filename,sha256,page_count FROM sources WHERE id=?", (run.get("source_id"),)).fetchone()
    source = dict(src) if src else None
    counts = {"found": 0, "missing": 0, "ambiguous": 0, "conflict": 0}
    for fact in facts:
        status = str(fact.get("status") or "").lower()
        if status in counts:
            counts[status] += 1
    return {
        "device_id": device_id,
        "source": source,
        "schema_valid": bool(run.get("schema_valid")),
        "field_count": len(facts),
        "expected_field_count": len(expected),
        "expected_fields": expected,
        "counts": counts,
        "facts": facts,
        "unresolved_evidence": run.get("unresolved_evidence") or [],
        "review_required": bool(run.get("review_required")),
        "review_queue": run.get("review_queue") or [],
        "searched_pages": run.get("searched_pages") or [],
        "searched_sections": run.get("searched_sections") or [],
        "coverage": run.get("coverage") or {},
        "coverage_layers": run.get("coverage_layers") or {},
    }


@app.get("/api/devices/{device_id}/document-analysis", tags=["Document Analysis"])
def document_analysis(device_id: str):
    run = core.get_extraction_run(device_id)
    if not run:
        raise HTTPException(404, "未找到该器件的文档分析记录")
    return run.get("document_analysis") or {
        "document_identity": {}, "valid_part_numbers": [], "facts": [],
        "searched_pages": [], "searched_sections": [], "coverage": {},
    }


@app.post("/api/v1/ai/analyze", tags=["AI Analysis v1"])
def analyze(body: AnalysisRequest):
    try:
        return ai.analyze(body.question, body.old_id, body.new_id)
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ai.AIResponseError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/devices/{device_id}/models")
def document_models(device_id: str):
    return core.list_models(device_id)


@app.patch("/api/document-models/{model_id}")
def review_document_model(model_id: str, body: ModelReview):
    try:
        return core.verify_model(model_id, body.status, body.value, body.verified_by, body.scope)
    except KeyError:
        raise HTTPException(404, "型号候选不存在")
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/devices/{device_id}/extraction-status")
def get_extraction_status(device_id: str):
    run = core.get_extraction_run(device_id)
    if not run:
        return {"status": "legacy_or_not_run", "review_required": False, "review_queue": []}
    return run


@app.get("/api/devices/{device_id}/final-review")
def get_final_review(device_id: str):
    review = core.get_final_review(device_id)
    if review:
        return review
    gate = core.get_extraction_run(device_id)
    models = core.list_models(device_id)
    pending_models = [m for m in models if m.get("verify_status") == "pending"]
    if gate is not None and pending_models:
        return {"overall_status": "blocked", "summary": f"尚有 {len(pending_models)} 个料号候选待人工确认，确认后再判断是否需要 Final Review",
                "missing_fields": ["part_number_confirmation"], "findings": [], "corrections": [], "applied_correction_count": 0}
    if gate is not None and not gate.get("review_required"):
        return {"overall_status": "not_required", "summary": "确定性 Review Gate 已通过，当前无需调用 Final Review 模型",
                "missing_fields": [], "findings": [], "corrections": [], "applied_correction_count": 0}
    return {"overall_status": "not_run", "summary": "已命中 Review Gate，尚未执行 Final Review Agent",
            "missing_fields": [], "findings": [], "corrections": [], "applied_correction_count": 0}


@app.post("/api/devices/{device_id}/final-review")
def run_final_review(device_id: str):
    with core.connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if not device:
        raise HTTPException(404, "器件不存在")
    try:
        gate = core.get_extraction_run(device_id)
        all_models = core.list_models(device_id)
        pending_models = [m for m in all_models if m.get("verify_status") == "pending"]
        if gate is not None and pending_models:
            raise HTTPException(409, f"尚有 {len(pending_models)} 个料号候选待人工确认；料号确认属于 Final Review 前置完整性 Gate")
        if gate is not None and not gate.get("review_required"):
            return {"overall_status": "not_required", "summary": "确定性 Review Gate 已通过，当前无需调用 Final Review 模型",
                    "missing_fields": [], "findings": [], "corrections": [], "applied_correction_count": 0}
        models = [m for m in all_models if m.get("verify_status") != "rejected"]
        candidates = [c for c in core.list_candidates(device_id) if c.get("verify_status") != "rejected"]
        result = ai.final_review(device["device_type"], device["vendor"], device["model"], models, candidates)
        return core.save_final_review(device_id, result)
    except ai.AIUnavailable as e:
        core.save_final_review(device_id, {"overall_status": "failed", "summary": f"Final Review Agent 不可用：{e}",
                                           "missing_fields": [], "findings": [], "corrections": []})
        raise HTTPException(503, str(e)) from e
    except ai.AIResponseError as e:
        core.save_final_review(device_id, {"overall_status": "failed", "summary": f"Final Review Agent 执行失败：{e}",
                                           "missing_fields": [], "findings": [], "corrections": []})
        raise HTTPException(502, str(e)) from e


@app.get("/api/devices/{device_id}/document-identity")
def device_document_identity(device_id: str):
    item = core.get_document_identity(device_id)
    if not item:
        raise HTTPException(404, "文档身份信息不存在")
    return item


@app.get("/api/devices/{device_id}/parsed-document")
def device_parsed_document(device_id: str, include_markdown: bool = False):
    item = core.get_parsed_document(device_id, include_markdown=include_markdown)
    if not item:
        raise HTTPException(404, "Markdown 解析结果不存在")
    return item


@app.get("/api/devices/{device_id}/specification-status")
def device_specification_status(device_id: str):
    try:
        return core.specification_workflow_status(device_id)
    except KeyError:
        raise HTTPException(404, "规格书记录不存在")


@app.get("/api/devices/{device_id}/reviewed-specifications")
def device_reviewed_specifications(device_id: str):
    try:
        return core.list_reviewed_specifications(device_id)
    except KeyError:
        raise HTTPException(404, "规格书记录不存在")


@app.get("/api/devices/{device_id}/family-view")
def device_family_view(device_id: str):
    try:
        return core.family_view(device_id)
    except KeyError:
        raise HTTPException(404, "规格书记录不存在")


@app.get("/api/devices/{device_id}/reviewed-specs")
def device_reviewed_specs(device_id: str):
    try:
        return core.reviewed_specs(device_id)
    except KeyError:
        raise HTTPException(404, "规格书记录不存在")


@app.get("/api/devices/{device_id}/conclusion")
def device_conclusion(device_id: str):
    try:
        return core.get_device_conclusion(device_id)
    except KeyError:
        raise HTTPException(404, "规格书记录不存在")


@app.post("/api/devices/{device_id}/conclusion/rebuild")
def rebuild_device_conclusion(device_id: str):
    try:
        return core.rebuild_device_conclusion(device_id)
    except KeyError:
        raise HTTPException(404, "规格书记录不存在")


@app.get("/api/devices/{device_id}/candidates")
def candidates(device_id: str):
    return core.list_candidates(device_id)


@app.get("/api/candidates/{candidate_id}/review-history")
def candidate_review_history(candidate_id: str):
    try:
        return core.list_candidate_review_history(candidate_id)
    except KeyError:
        raise HTTPException(404, "候选不存在")


@app.patch("/api/candidates/{candidate_id}")
def review(candidate_id: str, body: Review):
    try:
        return core.verify(candidate_id, body.status, body.value, body.unit, body.verified_by, body.condition, body.scope)
    except KeyError:
        raise HTTPException(404, "候选参数不存在")
    except core.ConfirmationConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.delete("/api/candidates/{candidate_id}")
def delete_candidate(candidate_id: str):
    try:
        return core.delete_candidate(candidate_id)
    except KeyError:
        raise HTTPException(404, "候选参数不存在")


@app.delete("/api/devices/{device_id}/candidates")
def clear_device_candidates(device_id: str):
    try:
        return core.clear_candidates(device_id)
    except KeyError:
        raise HTTPException(404, "器件不存在")


@app.delete("/api/devices/{device_id}")
def delete_device_record(device_id: str):
    try:
        return core.delete_device(device_id)
    except KeyError:
        raise HTTPException(404, "规格书记录不存在")


@app.get("/api/sources/{source_id}/pdf")
def source_pdf(source_id: str, page: int = 1):
    with core.connect() as con:
        source = con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        raise HTTPException(404, "来源不存在")
    if page < 1 or page > source["page_count"]:
        raise HTTPException(422, "页码超出范围")
    return FileResponse(source["local_path"], media_type="application/pdf", filename=source["filename"], content_disposition_type="inline")


@app.post("/api/compare")
def compare(body: Comparison):
    try:
        return core.compare(body.device_ids)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/knowledge/search")
def knowledge(q: str):
    return core.query_knowledge(q)


@app.get("/api/impact-drafts")
def impact(old_id: str, new_id: str):
    try:
        return core.impact_draft(old_id, new_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/links", status_code=201)
def create_link(body: Link):
    if body.target_system not in {"quality_issue", "quality_scenario", "review"} or not body.target_id.strip():
        raise HTTPException(422, "不支持的外部对象")
    with core.connect() as con:
        if not con.execute("SELECT 1 FROM devices WHERE id=?", (body.device_id,)).fetchone():
            raise HTTPException(404, "器件不存在")
        con.execute("""INSERT OR IGNORE INTO links VALUES (?,?,?,?,?,?)""",
                    (core.uuid4().hex, body.device_id, body.target_system, body.target_id,
                     body.relation, core.now()))
    return body.model_dump()


@app.get("/api/links")
def links(device_id: str | None = None, target_system: str | None = None, target_id: str | None = None):
    clauses, args = [], []
    for key, value in (("device_id", device_id), ("target_system", target_system), ("target_id", target_id)):
        if value:
            clauses.append(f"{key}=?")
            args.append(value)
    with core.connect() as con:
        return core.rows(con, "SELECT * FROM links" + (" WHERE " + " AND ".join(clauses) if clauses else ""), args)


@app.get("/api/external/cases")
def search_cases(keyword: str):
    try:
        return case_adapter.search_cases(keyword)
    except Exception as e:
        raise HTTPException(502, f"案例接口读取失败：{type(e).__name__}") from e


@app.get("/api/external/cases/{case_id}")
def get_case(case_id: str):
    try:
        result = case_adapter.get_case(case_id)
    except Exception as e:
        raise HTTPException(502, f"案例接口读取失败：{type(e).__name__}") from e
    if result is None:
        raise HTTPException(404, "案例不存在")
    return result


@app.post("/api/v1/knowledge/sources", status_code=201, tags=["Knowledge API v1"], response_model=kc.SourceCreated)
async def add_knowledge_source(file: UploadFile = File(...), title: str = Form(...),
                               publisher: str = Form(...), official_url: str = Form(""),
                               version: str = Form("")):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "文件上限 20 MB")
    try:
        return knowledge_store.ingest(file.filename or "", data, title, publisher, official_url, version)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/v1/knowledge/sources/from-url", status_code=201, tags=["Knowledge API v1"], response_model=kc.SourceCreated)
def add_knowledge_source_from_url(body: kc.UrlSourceRequest):
    try:
        return knowledge_store.fetch_official_source(body.official_url, body.title, body.publisher, body.version)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except knowledge_store.RemoteSourceError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/v1/knowledge/sources", tags=["Knowledge API v1"], response_model=list[kc.SourceSummary])
def knowledge_sources():
    return knowledge_store.list_sources()


@app.get("/api/v1/knowledge/sources/{source_id}", tags=["Knowledge API v1"], response_model=kc.SourceDetail)
def knowledge_source(source_id: str):
    result = knowledge_store.get_source(source_id)
    if not result:
        raise HTTPException(404, "来源不存在")
    return result


@app.patch("/api/v1/knowledge/sources/{source_id}/review", tags=["Knowledge API v1"], response_model=kc.SourceReviewed)
def review_knowledge_source(source_id: str, body: SourceReview):
    try:
        return knowledge_store.verify(source_id, body.status, body.verified_by)
    except KeyError:
        raise HTTPException(404, "来源不存在")
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/v1/knowledge/sources/{source_id}/file", tags=["Knowledge API v1"])
def knowledge_source_file(source_id: str):
    with knowledge_store.connect() as con:
        source = con.execute("SELECT local_path,filename,source_type FROM knowledge_sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        raise HTTPException(404, "来源不存在")
    media = "application/pdf" if source["source_type"] == "pdf" else "text/plain; charset=utf-8"
    return FileResponse(source["local_path"], media_type=media, filename=source["filename"],
                        content_disposition_type="inline")


@app.get("/api/v1/knowledge/search", tags=["Knowledge API v1"], response_model=kc.SearchResponse)
def search_knowledge(q: str, limit: int = 10):
    return knowledge_store.search(q, limit)


@app.get("/api/v1/knowledge/passages/{passage_id}", tags=["Knowledge API v1"], response_model=kc.PassageDetail)
def knowledge_passage(passage_id: int):
    result = knowledge_store.get_passage(passage_id)
    if not result:
        raise HTTPException(404, "原文片段不存在")
    return result


# Unified Knowledge Production UI is exposed on the same Storage product port.
# Only the shared Knowledge Production routes are attached; Storage does not
# implement or copy its review/publish state machine.
_KNOWLEDGE_PROCESSING_APP = knowledge_product.processing_app()
for _route in _KNOWLEDGE_PROCESSING_APP.router.routes:
    if str(getattr(_route, "path", "")).startswith("/knowledge-production"):
        app.router.routes.append(_route)
