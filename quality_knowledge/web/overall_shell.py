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
    overall_navigation_asset_version,
)
from quality_knowledge.web.overall_assembly import build_domain_assembly


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
        "summary": "围绕同一 Canonical Problem Identity 进入四个业务工作台；公共问题视图作为共享底座，不作为第五个业务工作台。",
        "common_problem_path": "/p0/issues",
        "capabilities": (
            {"title": "ITR工作台", "summary": "Source-owned ITR / 现场恢复事实只读消费视图。", "path": "/p0/itr-recovery"},
            {"title": "彻底解决工作台", "summary": "Source-aligned 原因、措施、验证与业务状态视图。", "path": "/p0/itr-resolution"},
            {"title": "软件考核工作台", "summary": "复用 Existing Capability 与历史考核事实；不创建第二套状态机。", "path": "/p0/software-assessment"},
            {"title": "漏测分析", "summary": "复用 Existing Problem + Escape Analysis。", "path": "/p0/missed-test-analysis"},
            {"title": "批量 AI 分析", "summary": "保留正式 P0 批量分析入口。", "path": "/p0/batch-analysis", "supporting": True},
            {"title": "原问题 AI 分析", "summary": "保留 /analysis 历史深链；属于支撑能力，不替代四个业务工作台。", "path": "/analysis", "requires_legacy": True, "supporting": True},
        ),
    },
    {
        "area_id": "cases-knowledge",
        "title": "案例与知识",
        "summary": "统一导航重大问题、硬件案例和知识生产能力，Domain 数据所有权保持不变。",
        "capabilities": (
            {"title": "重大问题案例库", "summary": "Historical Case、Repeat Risk 与 Evidence。", "path": "/p0/cases"},
            {"title": "硬件案例库", "summary": "硬件案例、电路树与来源追溯。", "path": "/p0/hardware-cases"},
            {"title": "硬件基础数据", "summary": "双树导入与基础数据管理。", "path": "/p0/hardware-cases/base-data"},
            {"title": "已发布知识", "summary": "统一知识生产正式发布对象。", "path": "/storage-workspace/knowledge-production/published"},
            {"title": "统一知识生产", "summary": "Source、Candidate、审核与发布。", "path": "/storage-workspace/knowledge-production/sources"},
        ),
    },
    {
        "area_id": "scenarios-insights",
        "title": "质量场景与洞察",
        "summary": "新版场景/画像与旧能力并行；旧能力在 Projection Parity 前不得静默退役。",
        "capabilities": (
            {"title": "原有质量场景工作台", "summary": "恢复历史 /quality-scenarios 深链；R2 兼容层只读。", "path": "/quality-scenarios", "requires_legacy_scenario": True},
            {"title": "原产品质量画像", "summary": "恢复历史场景资产产品聚合视图；只读。", "path": "/quality-scenario-assets", "requires_legacy_scenario": True},
            {"title": "原客户质量画像", "summary": "恢复历史客户/行业画像页中的客户聚合视图；只读。", "path": "/quality-scenario-assets/portrait", "requires_legacy_scenario": True},
            {"title": "原行业质量画像", "summary": "恢复历史客户/行业画像页中的行业聚合视图；只读。", "path": "/quality-scenario-assets/portrait", "requires_legacy_scenario": True},
            {"title": "新版质量场景库", "summary": "正式质量场景、详情、来源与 Evidence。", "path": "/p0/quality-scenario-insights"},
            {"title": "新版产品质量画像", "summary": "按产品视角消费正式质量场景。", "path": "/p0/quality-scenario-insights?view=PRODUCT"},
            {"title": "新版客户质量画像", "summary": "按客户视角消费正式质量场景。", "path": "/p0/quality-scenario-insights?view=CUSTOMER"},
            {"title": "新版行业质量画像", "summary": "按行业视角消费正式质量场景。", "path": "/p0/quality-scenario-insights?view=INDUSTRY"},
            {"title": "质量洞察", "summary": "保留现有质量洞察入口。", "path": "/p0/insights"},
            {"title": "产品综合报告", "summary": "复用现有 P1 产品报告。", "path": "/p1/product-reports"},
        ),
    },
    {
        "area_id": "professional-topics",
        "title": "专业专题",
        "summary": "独立专业产品通过稳定入口接入 Overall，不复制 Domain 实现。",
        "capabilities": (
            {"title": "存储器件寿命智能产品", "summary": "器件、规格、寿命、诊断与变更影响。", "path": "/storage-workspace/"},
            {"title": "正向质量风险评估", "summary": "复用现有 P1 风险评估能力。", "path": "/p1/risk-assessment"},
        ),
    },
    {
        "area_id": "management",
        "title": "管理与配置",
        "summary": "数据接入、字段映射、产品配置与历史兼容入口。",
        "capabilities": (
            {"title": "数据接入", "summary": "统一 P0 数据接入。", "path": "/p0/data-intake"},
            {"title": "字段映射与产品", "summary": "统一 P0 设置。", "path": "/p0/settings"},
            {"title": "Legacy 问题导入", "summary": "保留原问题导入。", "path": "/import", "requires_legacy": True},
            {"title": "ITR 材料导入", "summary": "来源材料导入，不是业务工作台。", "path": "/materials/itr", "requires_legacy": True},
            {"title": "彻底解决单材料导入", "summary": "ITR-CS 来源材料导入。", "path": "/materials/cs", "requires_legacy": True},
            {"title": "数据关联配置", "summary": "维护来源材料与权威问题的关联规则。", "path": "/settings/associations", "requires_legacy": True},
        ),
    },
)

SCENARIO_PROJECTION_PARITY: tuple[dict[str, Any], ...] = (
    {
        "capability_id": "legacy-scenario",
        "legacy_title": "原有质量场景工作台",
        "legacy_path": "/quality-scenarios",
        "replacement_title": "新版质量场景库",
        "replacement_path": "/p0/quality-scenario-insights",
        "parity_status": "PENDING_EVIDENCE",
        "retirement_allowed": False,
    },
    {
        "capability_id": "product-portrait",
        "legacy_title": "原产品质量画像",
        "legacy_path": "/quality-scenario-assets",
        "replacement_title": "新版产品质量画像",
        "replacement_path": "/p0/quality-scenario-insights?view=PRODUCT",
        "parity_status": "PENDING_EVIDENCE",
        "retirement_allowed": False,
    },
    {
        "capability_id": "customer-portrait",
        "legacy_title": "原客户质量画像",
        "legacy_path": "/quality-scenario-assets/portrait",
        "replacement_title": "新版客户质量画像",
        "replacement_path": "/p0/quality-scenario-insights?view=CUSTOMER",
        "parity_status": "PENDING_EVIDENCE",
        "retirement_allowed": False,
    },
    {
        "capability_id": "industry-portrait",
        "legacy_title": "原行业质量画像",
        "legacy_path": "/quality-scenario-assets/portrait",
        "replacement_title": "新版行业质量画像",
        "replacement_path": "/p0/quality-scenario-insights?view=INDUSTRY",
        "parity_status": "PENDING_EVIDENCE",
        "retirement_allowed": False,
    },
)


def _scenario_projection_parity(*, legacy_scenario_ready: bool) -> dict[str, Any]:
    items = []
    for raw in SCENARIO_PROJECTION_PARITY:
        item = dict(raw)
        item["legacy_available"] = legacy_scenario_ready
        item["coexistence_required"] = True
        item["retirement_reason"] = (
            "Projection Parity evidence not yet approved"
            if legacy_scenario_ready
            else "Legacy scenario source is not bound in this runtime; retirement cannot be inferred"
        )
        items.append(item)
    return {
        "contract": "overall-scenario-projection-parity/v1",
        "items": items,
        "total": len(items),
        "all_parity_pass": False,
        "retirement_allowed": False,
        "policy": "KEEP_LEGACY_UNTIL_PROJECTION_PARITY",
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


def _present_product_area(
    area: Mapping[str, Any],
    *,
    legacy_ready: bool,
    legacy_scenario_ready: bool,
) -> dict[str, Any]:
    result = dict(area)
    capabilities = []
    for raw in area["capabilities"]:
        item = dict(raw)
        if item.get("binding_pending"):
            available = False
        elif item.get("requires_legacy_scenario"):
            available = legacy_scenario_ready
        elif item.get("requires_legacy"):
            available = legacy_ready
        else:
            available = True
        capabilities.append({**item, "available": available})
    result["capabilities"] = capabilities
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
        build_overall_evidence_href(
            evidence,
            return_to=return_to or "/p0/overall",
            presentation="drawer",
        )
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
    templates.env.globals["overall_navigation_asset_version"] = overall_navigation_asset_version()
    router = APIRouter()

    @router.get("/api/v2/overall/workspaces")
    def overall_workspaces() -> dict[str, Any]:
        return {"items": [dict(item) for item in WORKSPACES], "total": len(WORKSPACES)}

    @router.get("/api/v2/overall/task-overview")
    def overall_task_overview() -> dict[str, Any]:
        return _task_overview(task_provider)

    @router.get("/api/v2/overall/assembly")
    def overall_assembly(request: Request) -> dict[str, Any]:
        return build_domain_assembly(request.app.state)

    @router.get("/api/v2/overall/scenario-parity")
    def overall_scenario_parity(request: Request) -> dict[str, Any]:
        legacy_scenario_status = getattr(request.app.state, "legacy_scenario_status", {}) or {}
        return _scenario_projection_parity(
            legacy_scenario_ready=legacy_scenario_status.get("ready") is True,
        )

    @router.get("/p0/overall", response_class=HTMLResponse, include_in_schema=False)
    def overall_home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "overall_shell.html",
            {
                "page_title": "总体工作台",
                "workspaces": WORKSPACES,
                "task_overview": _task_overview(task_provider),
                "domain_assembly": build_domain_assembly(request.app.state),
                "common_evidence_contract": COMMON_EVIDENCE_CONTRACT_VERSION,
                "product_areas": PRODUCT_AREAS,
            },
        )

    @router.get("/api/v2/overall/product-areas")
    def overall_product_areas(request: Request) -> dict[str, Any]:
        legacy_status = getattr(request.app.state, "legacy_quality_issue_status", {}) or {}
        legacy_scenario_status = getattr(request.app.state, "legacy_scenario_status", {}) or {}
        legacy_ready = legacy_status.get("ready") is True
        legacy_scenario_ready = legacy_scenario_status.get("ready") is True
        items = [
            _present_product_area(
                area,
                legacy_ready=legacy_ready,
                legacy_scenario_ready=legacy_scenario_ready,
            )
            for area in PRODUCT_AREAS
        ]
        return {"items": items, "total": len(items)}

    @router.get(
        "/p0/overall/areas/{area_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def product_area(request: Request, area_id: str) -> HTMLResponse:
        area = _product_area_index().get(area_id)
        if area is None:
            raise HTTPException(status_code=404, detail="PRODUCT_AREA_NOT_FOUND")
        legacy_status = getattr(request.app.state, "legacy_quality_issue_status", {}) or {}
        legacy_scenario_status = getattr(request.app.state, "legacy_scenario_status", {}) or {}
        legacy_ready = legacy_status.get("ready") is True
        legacy_scenario_ready = legacy_scenario_status.get("ready") is True
        return templates.TemplateResponse(
            request,
            "overall_product_area.html",
            {
                "page_title": area["title"],
                "area": _present_product_area(
                    area,
                    legacy_ready=legacy_ready,
                    legacy_scenario_ready=legacy_scenario_ready,
                ),
                "product_areas": PRODUCT_AREAS,
                "scenario_parity": (
                    _scenario_projection_parity(
                        legacy_scenario_ready=legacy_scenario_ready,
                    )
                    if area_id == "scenarios-insights"
                    else None
                ),
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
        template_name = (
            "overall_evidence_drawer.html"
            if presentation == "drawer"
            else "overall_evidence.html"
        )
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
            },
        )

    @router.get("/p0/overall/return", include_in_schema=False)
    def overall_return(to: str = Query("/p0/overall")) -> RedirectResponse:
        return RedirectResponse(_safe_local_path(to, default="/p0/overall"))

    return router
