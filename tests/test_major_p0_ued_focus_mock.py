"""Major production focused UED Mock contract; no Provider, network or browser required."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "quality_knowledge" / "web" / "templates"
STATIC = ROOT / "quality_knowledge" / "web" / "static"


def _render(template: str, path: str, *, overall: bool = False, mature: bool = False) -> str:
    request = SimpleNamespace(
        url=SimpleNamespace(path=path),
        query_params={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                mature_quality_host=mature,
                overall_shell_enabled=overall,
            )
        ),
    )
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True).get_template(
        template
    ).render(request=request, page_title="重大问题生产", api_prefix="/api/v2")


def test_major_navigation_has_three_primary_links_and_discoverable_legacy():
    page = _render("major_production.html", "/p0/major-production")
    focus_nav = page.split('<nav class="main-nav" aria-label="工作台导航">', 1)[1]
    initial_links = focus_nav.split("<details class=\"major-other-workbenches\"", 1)[0]
    assert initial_links.count('class="nav-item') == 3
    assert 'aria-current="page"' in initial_links
    assert "重大问题案例库" in initial_links
    assert "返回工作台" in initial_links
    assert "其他工作台（展开）" in focus_nav
    assert 'href="/p0/batch-analysis"' in page
    assert 'href="/p0/missed-test-analysis"' in page
    assert page.count('href="/p0/major-production"') == 1


def test_import_paths_are_progressive_without_changing_form_contract():
    page = _render("major_production.html", "/p0/major-production")
    assert 'id="major-excel"' in page
    assert 'data-major-excel' in page
    assert 'name="file" type="file" accept=".xls,.xlsx,.xlsm"' in page
    assert 'name="materials" type="file"' in page
    assert '<details class="major-alternative" data-major-single-source>' in page
    assert 'data-major-intake' in page
    assert 'name="standard_itr"' in page
    assert 'name="group_code" value="MAJOR"' in page
    assert 'data-major-analyze' in page
    assert 'data-major-publish disabled' in page
    assert "上传并预览（不立即导入）" in page
    assert "此操作不会自动运行 AI" in page
    assert page.index('id="major-excel"') < page.index('data-major-single-source')


def test_only_one_semantic_main_and_chinese_business_steps():
    page = _render("major_production.html", "/p0/major-production")
    assert page.count('<main class="') == 1
    assert "导入问题、AI 分析、审核发布" in page
    assert "AI Analysis → Human Review → Publish" not in page
    assert "标准 ITR 编号" in page
    assert "问题组代码" in page
    assert "重大问题业务步骤" in page


def test_global_shell_unchanged_for_other_pages():
    page = _render("p0_issues.html", "/p0/issues")
    assert "major-focus-shell" not in page
    assert '<div class="side-foot">' in page
    assert '<div class="top-actions">' in page
    assert '<details class="major-other-workbenches"' not in page
    assert 'href="/p0/batch-analysis"' in page


def test_major_overall_shell_preserves_back_navigation():
    page = _render("major_production.html", "/p0/major-production", overall=True)
    assert 'href="/p0/overall">返回工作台</a>' in page
    assert 'href="/p0/overall/areas/scenarios-insights"' in page


def test_localized_stages_and_recovery_without_api_changes():
    script = (STATIC / "major_production.js").read_text(encoding="utf-8")
    for token in [
        "EVENT_SELECTION_REQUIRED: '请选择具体事件'",
        "REVIEW_REQUIRED: '待人工审核'",
        "READY_TO_PUBLISH: '审核完成 · 可发布'",
        "INTAKED: '已导入 · 待分析'",
        "await restoreCase(state.caseId, state.eventId);",
        "root.querySelector('[data-major-single-source]').after(recoveryPanel);",
    ]:
        assert token in script
    assert "MAJOR_ANALYSIS_INCOMPLETE" in script
    assert "data-major-recovered-confirm" in script
    assert "renderPagedRows(" in script
    assert "'/excel/confirm'" in script
    assert "'/cases/'" in script
    # Review completion is checked against persisted Case/Event records.
    assert "publishButton.disabled = true;" in script
    assert "无法复核服务端四项完整状态" in script
    assert "发布结果未核实" in script
    assert "避免重复发布" in script
