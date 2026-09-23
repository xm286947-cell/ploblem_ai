from pathlib import Path

ROOT=Path(__file__).parents[1]
TPL=(ROOT/"quality_knowledge/web/templates/issue_detail.html").read_text(encoding="utf-8")
CSS=(ROOT/"quality_knowledge/web/static/app.css").read_text(encoding="utf-8")

def test_reference_area_uses_single_tab_window():
    assert 'data-reference-tabs' in TPL
    assert 'data-reference-tab="original"' in TPL
    assert 'data-reference-tab="normalized"' in TPL
    assert 'data-reference-tab="ai"' in TPL
    assert 'data-reference-panel="original"' in TPL
    assert 'data-reference-panel="normalized" hidden' in TPL
    assert 'data-reference-panel="ai" hidden' in TPL
    assert 'human-reference-grid' not in TPL

def test_original_is_default_and_no_compare_ui():
    assert 'data-reference-tab="original">原始问题</button>' in TPL
    assert 'aria-selected="true" data-reference-tab="original"' in TPL
    assert '并排比较' not in TPL
    assert '差异比较' not in TPL

def test_tab_switch_does_not_replace_human_form():
    # Tabs only toggle reference panels; human form remains a sibling and is not rebuilt.
    assert "panel.hidden=!active" in TPL
    assert 'id="human-analysis-form"' in TPL
    assert 'data-reference-panel' in TPL
    assert '.reference-panel[hidden]' in CSS
