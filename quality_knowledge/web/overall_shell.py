"""Platform-owned Overall Shell routes.

This module is intentionally transport/UI only. It never imports or opens a
Domain repository, table, model, or database. Domain workspaces remain owned
by their respective products and are reached through stable navigation entry
points.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from compatibility.common_evidence import (
    CommonEvidenceContractError,
    build_overall_evidence_href,
    validate_common_evidence,
)
from quality_knowledge.web.overall_navigation import (
    append_overall_return_state,
    normalize_overall_return_state,
)


_HERE = Path(__file__).resolve().parent
COMMON_EVIDENCE_CONTRACT_VERSION = "common-evidence/v1.0"

WORKSPACES: tuple[dict[str, str], ...] = (
    {
        "workspace_id": "major",
        "title": "重大问题案例库 × Repeat Risk",
        "summary": "历史案例、Repeat Risk、Evidence 与人工判断。",
        "entry_path": "/p0/cases",
        "shell_entry_path": "/p0/workspaces/major",
    },
    {
        "workspace_id": "quality-scenario",
        "title": "质量场景库",
        "summary": "质量场景、画像与场景证据导航。",
        "entry_path": "/p0/quality-scenario-insights",
        "shell_entry_path": "/p0/workspaces/quality-scenario",
    },
    {
        "workspace_id": "hardware",
        "title": "硬件案例库",
        "summary": "硬件案例、双树映射、Knowledge / Evidence。",
        "entry_path": "/p0/hardware-cases",
        "shell_entry_path": "/p0/workspaces/hardware",
    },
    {
        "workspace_id": "storage",
        "title": "存储器件寿命智能产品",
        "summary": "Device Fact、寿命评估、诊断、Knowledge / Evidence。",
        "entry_path": "/storage-workspace/",
        "shell_entry_path": "/p0/workspaces/storage",
    },
)

TaskProvider = Callable[[], Mapping[str, Any] | Sequence[Mapping[str, Any]]]

PRODUCT_AREAS: tuple[dict[str, Any], ...] = (
    {
        "area_id": "current-problem",
        "title": "当前问题",
        "summary": "以研发与测试对齐后的当前有效问题视图为入口，连接现场处理、彻底解决、软件考核与重大案例复用。",
        "capabilities": (
            {"title": "问题工作台", "summary": "研发与测试对齐后的当前有效问题消费视图；复用既有问题主数据。", "path": "/p0/issues"},
            {"title": "ITR / 现场恢复工作台", "summary": "复用既有 /issues 问题列表、详情与处理流程；不与新版问题消费视图混为一页。", "path": "/issues", "requires_legacy": True},
            {"title": "ITR 彻底解决工作台", "summary": "查看已导入彻底解决单及已确认的 ITR 关联，并进入原问题详情继续处理。", "path": "/itr/resolution-workbench", "requires_legacy": True},
            {"title": "软件问题考核", "summary": "独立管理软件问题考核范围、状态与责任；精确页面路由待 Targeted Verification。", "binding_pending": True},
            {"title": "重大问题案例库", "summary": "与问题处理并列关联、沉淀和复用；正式归属“案例与知识”。", "path": "/p0/cases"},
            {"title": "批量 AI 分析", "summary": "复用现有 P0 批量分析页面。", "path": "/p0/batch-analysis"},
            {
                "title": "原问题 AI 分析工作台",
                "summary": "旧问题分析页，含单条与批量分析；保留 /analysis 深链，尚未与软件问题考核或独立漏测分析建立映射。",
                "path": "/analysis",
                "requires_legacy": True,
            },
        ),
    },
    {
        "area_id": "cases-knowledge",
        "title": "案例与知识",
        "summary": "复用正式案例资产和统一知识生产能力。",
        "capabilities": (
            {"title": "重大问题案例库", "summary": "Historical Case、Repeat Risk 与 Evidence。", "path": "/p0/cases"},
            {"title": "硬件案例库", "summary": "硬件案例、电路树与证据。", "path": "/p0/hardware-cases"},
            {"title": "P07 硬件双树导入", "summary": "进入 Hardware 产品内基础数据管理，导入 / Apply 电路特性树与物料器件树。", "path": "/p0/hardware-cases/base-data"},
            {"title": "已发布知识", "summary": "统一知识生产发布的正式对象。", "path": "/storage-workspace/knowledge-production/published"},
            {"title": "统一知识生产", "summary": "Source、Candidate、审核与发布。", "path": "/storage-workspace/knowledge-production/sources"},
        ),
    },
    {
        "area_id": "scenarios-insights",
        "title": "质量场景与洞察",
        "summary": "保留原正向场景与画像能力；新质量场景消费已确认问题来源，Parity 前新旧能力并行可用。",
        "capabilities": (
            {"title": "原有质量场景工作台", "summary": "新版 Projection Parity 完成前继续保留；精确页面路由待 Targeted Verification。", "binding_pending": True},
            {"title": "原产品质量画像", "summary": "保留既有产品画像直至新版 Projection Parity；旧页面路由待 Targeted Verification。", "binding_pending": True},
            {"title": "原客户质量画像", "summary": "保留既有客户画像直至新版 Projection Parity；旧页面路由待 Targeted Verification。", "binding_pending": True},
            {"title": "原行业质量画像", "summary": "保留既有行业画像直至新版 Projection Parity；旧页面路由待 Targeted Verification。", "binding_pending": True},
            {"title": "新版质量场景库", "summary": "浏览已发布质量场景、详情、来源与 Evidence。", "path": "/p0/quality-scenario-insights"},
            {"title": "新版产品质量画像", "summary": "从正式质量场景切换到产品视角。", "path": "/p0/quality-scenario-insights?view=PRODUCT"},
            {"title": "新版客户质量画像", "summary": "从正式质量场景切换到客户视角。", "path": "/p0/quality-scenario-insights?view=CUSTOMER"},
            {"title": "新版行业质量画像", "summary": "从正式质量场景切换到行业视角。", "path": "/p0/quality-scenario-insights?view=INDUSTRY"},
            {"title": "质量洞察", "summary": "复用现有质量洞察页面。", "path": "/p0/insights"},
            {"title": "产品综合报告", "summary": "由现有 P1 产品报告承接。", "path": "/p1/product-reports"},
        ),
    },
    {
        "area_id": "professional-topics",
        "title": "专业专题",
        "summary": "面向专业工程分析的独立产品工作区。",
        "capabilities": (
            {"title": "存储器件寿命智能产品", "summary": "器件、规格、寿命评估、诊断与变更影响。", "path": "/storage-workspace/"},
            {"title": "正向质量风险评估", "summary": "复用现有 P1 风险评估能力。", "path": "/p1/risk-assessment"},
        ),
    },
    {
        "area_id": "management",
        "title": "管理与配置",
        "summary": "数据接入、字段映射与产品配置。",
        "capabilities": (
            {"title": "数据接入", "summary": "复用现有 P0 数据接入流程。", "path": "/p0/data-intake"},
            {"title": "Legacy 问题导入", "summary": "保留原问题数据导入步骤与兼容入口。", "path": "/import", "requires_legacy": True},
            {"title": "ITR 材料导入", "summary": "导入 ITR 来源材料；属于数据接入，不是现场 ITR 工作台。", "path": "/materials/itr", "requires_legacy": True},
            {"title": "彻底解决单材料导入", "summary": "导入彻底解决单来源材料。", "path": "/materials/cs", "requires_legacy": True},
            {"title": "软件运营数据导入", "summary": "导入软件问题运营/考核数据。", "path": "/materials/software-operations", "requires_legacy": True},
            {"title": "字段映射与产品", "summary": "复用现有 P0 设置页面。", "path": "/p0/settings"},
            {"title": "数据关联配置", "summary": "维护 Legacy 材料与问题之间的关联规则。", "path": "/settings/associations", "requires_legacy": True},
            {"title": "Legacy 问题业务统计", "summary": "问题规模、分析状态、再发风险与质量能力缺口；保留 /statistics 历史路由。", "path": "/statistics", "requires_legacy": True},
        ),
    },
)

KNOWLEDGE_COMPATIBILITY_ROUTES: dict[str, str] = {
    "cases": "/p0/cases",
    "repeat-case": "/p0/issues",
    "storage-lifetime": "/storage-workspace/",
    "hardware": "/p0/hardware-cases",
}


def _safe_local_path(value: Any, *, default: str | None = None) -> str | None:
    """Return one same-origin path or fail closed.

    Cross-product return is navigation metadata, never an open redirect.  A
    missing optional path remains missing rather than being guessed.
    """

    if value is None or value == "":
        return default
    path = str(value).strip()
    parsed = urlsplit(path)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in path
        or "\n" in path
        or "\r" in path
        or len(path) > 4096
    ):
        raise HTTPException(status_code=400, detail="OVERALL_NAVIGATION_PATH_INVALID")
    return path


def _workspace_index() -> dict[str, dict[str, str]]:
    return {item["workspace_id"]: dict(item) for item in WORKSPACES}


def _product_area_index() -> dict[str, dict[str, Any]]:
    return {item["area_id"]: dict(item) for item in PRODUCT_AREAS}


def _present_product_area(area: Mapping[str, Any], *, legacy_ready: bool) -> dict[str, Any]:
    result = dict(area)
    result["capabilities"] = [
        {
            **dict(item),
            "available": False if item.get("binding_pending") else (legacy_ready if item.get("requires_legacy") else True),
        }
        for item in area["capabilities"]
    ]
    return result


def _normalize_task(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("task must be an object")
    task_id = str(raw.get("task_id") or "").strip()
    title = str(raw.get("title") or "").strip()
    workspace_id = str(raw.get("workspace_id") or "").strip()
    status = str(raw.get("status") or "UNKNOWN").strip().upper()
    if not task_id or not title or not workspace_id:
        raise ValueError("task_id/title/workspace_id are required")
    if workspace_id not in _workspace_index():
        raise ValueError("unknown workspace_id")
    return_to = _safe_local_path(raw.get("return_to"), default="/p0/overall")
    evidence = raw.get("common_evidence")
    evidence_link = (
        build_overall_evidence_href(evidence, return_to=return_to, presentation="drawer")
        if isinstance(evidence, Mapping)
        else _safe_local_path(raw.get("evidence_link"))
    )
    return {
        "task_id": task_id,
        "title": title,
        "workspace_id": workspace_id,
        "status": status,
        "deep_link": _safe_local_path(raw.get("deep_link")),
        "evidence_link": evidence_link,
        "return_to": return_to,
    }


def _task_overview(provider: TaskProvider | None) -> dict[str, Any]:
    if provider is None:
        return {
            "state": "NO_PROVIDER",
            "items": [],
            "total": 0,
            "message": "统一任务源未注入；Overall Shell 不直接读取任何 Domain Repository。",
        }
    try:
        payload = provider()
        if isinstance(payload, Mapping):
            raw_items = payload.get("items") or []
        else:
            raw_items = payload
        items = [_normalize_task(item) for item in raw_items]
    except Exception as exc:
        return {
            "state": "UNAVAILABLE",
            "items": [],
            "total": 0,
            "message": f"统一任务源不可用：{type(exc).__name__}",
        }
    return {
        "state": "READY" if items else "EMPTY",
        "items": items,
        "total": len(items),
        "message": "" if items else "当前没有待处理任务。",
    }


def create_overall_shell_router(
    *,
    task_provider: TaskProvider | None = None,
    template_dir: str | Path | None = None,
) -> APIRouter:
    """Return the platform-owned Overall Shell router.

    The router owns navigation and presentation only.  Domain data can enter
    Task Overview exclusively through an injected public provider.
    """

    templates = Jinja2Templates(directory=str(template_dir or (_HERE / "templates")))
    router = APIRouter()

    @router.get("/api/v2/overall/workspaces")
    def overall_workspaces() -> dict[str, Any]:
        return {"items": [dict(item) for item in WORKSPACES], "total": len(WORKSPACES)}

    @router.get("/api/v2/overall/task-overview")
    def overall_task_overview() -> dict[str, Any]:
        return _task_overview(task_provider)

    @router.get("/p0/overall", response_class=HTMLResponse, include_in_schema=False)
    def overall_home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "overall_shell.html",
            {
                "page_title": "总体工作台",
                "workspaces": WORKSPACES,
                "task_overview": _task_overview(task_provider),
                "common_evidence_contract": COMMON_EVIDENCE_CONTRACT_VERSION,
                "product_areas": PRODUCT_AREAS,
            },
        )

    @router.get(
        "/p0/overall/areas/{area_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def product_area(request: Request, area_id: str) -> HTMLResponse:
        area = _product_area_index().get(area_id)
        if area is None:
            raise HTTPException(status_code=404, detail="PRODUCT_AREA_NOT_FOUND")
        legacy_status = getattr(request.app.state, "legacy_quality_issue_status", {})
        legacy_ready = legacy_status.get("ready") is True
        return templates.TemplateResponse(
            request,
            "overall_product_area.html",
            {
                "page_title": area["title"],
                "area": _present_product_area(area, legacy_ready=legacy_ready),
                "product_areas": PRODUCT_AREAS,
                "legacy_ready": legacy_ready,
            },
        )

    @router.get(
        "/p0/workspaces/{workspace_id}",
        response_class=RedirectResponse,
        include_in_schema=False,
    )
    def workspace_entry(workspace_id: str) -> RedirectResponse:
        workspace = _workspace_index().get(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="WORKSPACE_NOT_FOUND")
        return RedirectResponse(workspace["entry_path"])

    @router.get("/p0/overall/evidence", response_class=HTMLResponse, include_in_schema=False)
    def overall_evidence(
        request: Request,
        producer_domain: str = Query(""),
        evidence_id: str = Query(""),
        producer_object_id: str = Query(""),
        source_ref: str = Query(""),
        object_href: str = Query(""),
        return_to: str = Query("/p0/overall"),
        return_state: str = Query(""),
        common_evidence: str = Query(""),
        presentation: str = Query("page"),
    ) -> HTMLResponse:
        if presentation not in {"page", "drawer"}:
            raise HTTPException(status_code=400, detail="OVERALL_EVIDENCE_PRESENTATION_INVALID")
        evidence: dict[str, Any] | None = None
        if common_evidence:
            if len(common_evidence.encode("utf-8")) > 12000:
                raise HTTPException(status_code=400, detail="COMMON_EVIDENCE_PAYLOAD_INVALID")
            try:
                parsed_evidence = json.loads(common_evidence)
                if not isinstance(parsed_evidence, dict):
                    raise ValueError("object required")
                validate_common_evidence(parsed_evidence)
            except (TypeError, ValueError, CommonEvidenceContractError) as exc:
                raise HTTPException(status_code=400, detail="COMMON_EVIDENCE_PAYLOAD_INVALID") from exc
            evidence = parsed_evidence
        state = normalize_overall_return_state(return_state or None)
        safe_return = _safe_local_path(return_to, default="/p0/overall") or "/p0/overall"
        return_url = append_overall_return_state(safe_return, state)
        template_name = "overall_evidence_drawer.html" if presentation == "drawer" else "overall_evidence.html"
        return templates.TemplateResponse(
            request,
            template_name,
            {
                "page_title": "Evidence / Source Viewer",
                "contract_version": COMMON_EVIDENCE_CONTRACT_VERSION,
                "evidence": evidence,
                "producer_domain": (evidence or {}).get("producer_domain", producer_domain).strip(),
                "evidence_id": (evidence or {}).get("evidence_id", evidence_id).strip(),
                "evidence_type": (evidence or {}).get("evidence_type", ""),
                "producer_object_id": (evidence or {}).get("producer_object_id", producer_object_id) or "",
                "source_ref": (evidence or {}).get("source_ref", source_ref) or "",
                "source": (evidence or {}).get("source", {}),
                "locator": (evidence or {}).get("locator", {}),
                "excerpt": (evidence or {}).get("excerpt", ""),
                "source_text": (evidence or {}).get("source_text", ""),
                "verification_status": (evidence or {}).get("verification_status", ""),
                "evidence_status": (evidence or {}).get("evidence_status", ""),
                "source_reference": (evidence or {}).get("source_reference", ""),
                "object_href": _safe_local_path(object_href),
                "return_to": safe_return,
                "return_url": return_url,
                "return_state_json": json.dumps(state, ensure_ascii=False) if state else "",
            },
        )

    @router.get("/p0/overall/return", include_in_schema=False)
    def overall_return(to: str = Query("/p0/overall")) -> RedirectResponse:
        return RedirectResponse(_safe_local_path(to, default="/p0/overall"))

    @router.get("/p0/knowledge", include_in_schema=False)
    def legacy_knowledge_root() -> RedirectResponse:
        return RedirectResponse("/p0/overall/areas/cases-knowledge")

    @router.get("/p0/knowledge/{capability_id}", include_in_schema=False)
    def legacy_knowledge_entry(capability_id: str) -> RedirectResponse:
        target = KNOWLEDGE_COMPATIBILITY_ROUTES.get(capability_id)
        if target is None:
            raise HTTPException(status_code=404, detail="KNOWLEDGE_CAPABILITY_NOT_FOUND")
        return RedirectResponse(target)

    return router
