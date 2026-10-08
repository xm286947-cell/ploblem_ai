"""W3-03 Workbench UI additive contract tests. No browser/Provider required."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "quality_knowledge/web/templates/hardware_case_knowledge_production.html"
JS = ROOT / "quality_knowledge/web/static/hardware_case_knowledge_production.js"


def test_w3_controls_are_additive_and_default_to_legacy_sequential():
    page = HTML.read_text(encoding="utf-8")
    assert 'data-r1-workbench' in page
    assert '<option value="SEQUENTIAL">串行（兼容原模式）</option>' in page
    assert '<option value="PARALLEL">多案例并发</option>' in page
    assert '<option value="2" selected>2（推荐）</option>' in page
    for selector in (
        'data-w3-execution-mode', 'data-w3-concurrency',
        'data-w3-cancel-batch', 'data-w3-resume-batch',
        'data-w3-batch-progress',
    ):
        assert selector in page
    assert 'data-human-review' in page
    assert 'data-promotion-publish' in page
    assert 'data-batch-items' in page


def test_w3_ui_wires_real_existing_api_and_does_not_autoreplay():
    script = JS.read_text(encoding="utf-8")
    assert '?execution_mode=' in script
    assert '&concurrency=' in script
    assert "w3BatchControl('cancel')" in script
    assert "w3BatchControl('resume-cancelled')" in script
    assert "method: 'POST'" in script
    assert "w3Mode.value === 'PARALLEL' ? 'PARALLEL' : 'SEQUENTIAL'" in script
    assert "w3Concurrency.disabled = w3Mode.value !== 'PARALLEL'" in script
    assert "点击“开始 / 继续处理”才会重新执行" in script
    assert "取消不会强停运行中的 Provider 请求" in HTML.read_text(encoding="utf-8")
    assert "summary.CANCELLED || 0" in script
    assert "CANCELLED: '已取消'" in script
