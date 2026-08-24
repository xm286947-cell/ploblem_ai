from pathlib import Path

ROOT=Path(__file__).parents[1]
TPL=(ROOT/'quality_knowledge/web/templates/issue_detail.html').read_text(encoding='utf-8')
CSS=(ROOT/'quality_knowledge/web/static/app.css').read_text(encoding='utf-8')
APP=(ROOT/'quality_knowledge/web/app.py').read_text(encoding='utf-8')

def test_human_workspace_has_reference_area_and_no_default_diff():
    assert '原始问题' in TPL and '标准化数据' in TPL and 'AI 分析摘要' in TPL
    assert '查看完整 AI 分析' in TPL
    assert 'Original ↔ Normalized' not in TPL
    assert '差异比较' not in TPL

def test_dynamic_components_follow_frozen_rules():
    assert "f.field_type=='SINGLE_SELECT'" in TPL and '<select name="human_' in TPL
    assert "f.field_type=='MULTI_SELECT'" in TPL and 'class="multi-select"' in TPL
    assert "f.field_type=='BOOLEAN'" in TPL and '>是</option>' in TPL and '>否</option>' in TPL
    assert 'multi-tags' in TPL and '.multi-tags i' in CSS

def test_human_form_is_compact_and_save_state_visible():
    assert 'grid-template-columns:repeat(2' in CSS
    assert '已修改未保存' in TPL and '保存中' in TPL and '已保存' in TPL
    assert 'human-save-state' in TPL

def test_original_and_normalized_have_business_readable_rows():
    assert "'original_rows': _readable_rows" in APP
    assert "'normalized_rows': _readable_rows" in APP
    assert 'readable-kv' in TPL
    assert 'Raw JSON' not in TPL.split('<section class="compact-section human-section"')[1].split('<section id="trace"')[0]

def test_raw_data_remains_in_traceability_only():
    trace=TPL.split('<section id="trace"')[1]
    assert 'Original · 原始 Excel 数据' in trace and 'Normalized · 数据库标准化数据' in trace
    assert 'AI Debug · 仅调试使用' in trace
