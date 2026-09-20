from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
import json

from quality_knowledge.major_cases.legacy_adapter import LegacyRepeatAdapter
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.service import MajorCaseService


def create_major_case_router(
    repository: MajorKnowledgeRepository,
    service: MajorCaseService,
    templates,
    project_root: str | Path,
    run_root: str | Path,
) -> APIRouter:
    router = APIRouter()
    repeat = LegacyRepeatAdapter(repository, project_root, run_root)

    @router.get("/knowledge/major-cases", response_class=HTMLResponse, include_in_schema=False)
    def cases_page(request: Request, group: str = "", status: str = "", tag: str = "", page: int = 1):
        return templates.TemplateResponse(request, "major_cases.html", {
            "result": service.list_cases(group_code=group, status=status, tag=tag, page=page, page_size=20),
            "group": group, "status": status, "tag": tag,
        })

    @router.post("/knowledge/major-cases", include_in_schema=False)
    def create_case(title: str = Form(...), group_code: str = Form(...), domain: str = Form("")):
        try:
            case = service.create_case(title, group_code, domain)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"/knowledge/major-cases/{case['case_id']}", 303)

    @router.get("/knowledge/major-cases/{case_id}", response_class=HTMLResponse, include_in_schema=False)
    def case_detail(request: Request, case_id: str, message: str = ""):
        case = service.detail(case_id)
        if not case:
            raise HTTPException(404, "重大案例不存在")
        return templates.TemplateResponse(request, "major_case_detail.html", {"case": case, "message": message})

    @router.post("/knowledge/major-cases/{case_id}/documents", include_in_schema=False)
    async def upload_document(
        case_id: str,
        file: UploadFile = File(...),
        current_itrs: str = Form(""),
        document_id: str = Form(""),
        role: str = Form("PRIMARY"),
    ):
        content = await file.read()
        if len(content) > 100 * 1024 * 1024:
            raise HTTPException(413, "文件超过100MB限制")
        try:
            result = service.ingest_upload(
                case_id, file.filename or "upload.bin", content,
                current_itrs=[value.strip() for value in current_itrs.replace("；", ",").split(",") if value.strip()],
                document_id=document_id or None, role=role,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"/knowledge/major-cases/{case_id}?message={result['action']}", 303)

    @router.post("/knowledge/major-cases/{case_id}/extract", include_in_schema=False)
    def extract(
        case_id: str,
        version_id: str = Form(...),
        skill_version_id: str = Form(""),
        retry_failed: str = Form(""),
        execution_mode: str = Form("mock"),
    ):
        result = service.extract(
            case_id,
            version_id,
            skill_version_id=skill_version_id or None,
            retry_failed=retry_failed == "1",
            execution_mode=execution_mode,
        )
        return RedirectResponse(f"/knowledge/tasks/{result['run_id']}", 303)

    @router.post("/knowledge/entries/{entry_id}/review", include_in_schema=False)
    def review_entry(entry_id: str, case_id: str = Form(...), content: str = Form(...), action: str = Form(...), reviewer: str = Form("web"), reason: str = Form("")):
        try:
            service.review_entry(entry_id, content=content, action=action, reviewer=reviewer, reason=reason)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"/knowledge/major-cases/{case_id}#entries", 303)

    @router.post("/knowledge/events/{event_id}/repeat", include_in_schema=False)
    def run_repeat(event_id: str, use_mock: str = Form("1")):
        result = repeat.run(event_id, mock=use_mock == "1", skip_ai=use_mock != "1")
        return RedirectResponse(f"/knowledge/repeat-runs/{result['run_id']}", 303)

    @router.get("/knowledge/tasks", response_class=HTMLResponse, include_in_schema=False)
    def tasks_page(request: Request, state: str = ""):
        return templates.TemplateResponse(request, "major_tasks.html", {"runs": repository.list_runs(state), "state": state})

    @router.get("/knowledge/tasks/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    def task_detail(request: Request, run_id: str):
        run = repository.run(run_id)
        if not run:
            raise HTTPException(404, "任务不存在")
        return templates.TemplateResponse(request, "major_task_detail.html", {"run": run})

    @router.post("/knowledge/tasks/{run_id}/cancel", include_in_schema=False)
    def cancel_task(run_id: str):
        try:
            repository.cancel_run(run_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return RedirectResponse(f"/knowledge/tasks/{run_id}", 303)

    @router.get("/knowledge/repeat-runs/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    def repeat_detail(request: Request, run_id: str):
        run = repository.run(run_id)
        if not run:
            raise HTTPException(404, "重复分析任务不存在")
        return templates.TemplateResponse(request, "major_repeat_detail.html", {"run": run})

    @router.post("/knowledge/repeat-results/{result_id}/review", include_in_schema=False)
    def repeat_review(result_id: str, run_id: str = Form(...), action: str = Form(...), reviewer: str = Form("web"), reason: str = Form("")):
        try:
            repository.confirm_repeat(result_id, action, reviewer, reason)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"/knowledge/repeat-runs/{run_id}", 303)

    @router.get("/knowledge/skills", response_class=HTMLResponse, include_in_schema=False)
    def skills_page(request: Request):
        return templates.TemplateResponse(request, "major_skills.html", {"skills": repository.list_skills()})

    @router.post("/knowledge/skills", include_in_schema=False)
    def create_skill(config_json: str = Form(...)):
        try:
            config = json.loads(config_json)
            allowed = {"skill_code", "material_types", "required_sections", "auxiliary_sections", "output_schema", "prompt_rules", "tag_dictionary", "input_budget", "output_budget"}
            if set(config) - allowed:
                raise ValueError("SKILL_CONFIG_UNSUPPORTED_FIELDS")
            if not all(key in config for key in ("skill_code", "material_types", "required_sections", "output_schema", "prompt_rules", "input_budget", "output_budget")):
                raise ValueError("SKILL_CONFIG_REQUIRED_FIELDS_MISSING")
            repository.seed_skill(config)
        except (ValueError, json.JSONDecodeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/knowledge/skills", 303)

    @router.get("/api/knowledge/major-cases")
    def api_cases(group: str = "", status: str = "", tag: str = "", page: int = 1, page_size: int = 20):
        return service.list_cases(group_code=group, status=status, tag=tag, page=page, page_size=page_size)

    @router.get("/api/knowledge/major-cases/{case_id}")
    def api_case(case_id: str):
        case = service.detail(case_id)
        if not case:
            raise HTTPException(404, "major case not found")
        return case

    return router
