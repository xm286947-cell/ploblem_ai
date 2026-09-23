from pathlib import Path
ROOT=Path(__file__).parents[1]
IMPORT=(ROOT/'quality_knowledge/web/templates/import.html').read_text(encoding='utf-8')
PREVIEW=(ROOT/'quality_knowledge/web/templates/import_preview.html').read_text(encoding='utf-8')
MAPPING=(ROOT/'quality_knowledge/web/templates/mapping_settings.html').read_text(encoding='utf-8')
BASE=(ROOT/'quality_knowledge/web/templates/base.html').read_text(encoding='utf-8')
CSS=(ROOT/'quality_knowledge/web/static/app.css').read_text(encoding='utf-8')

def test_data_intake_is_preview_first_workspace():
    assert '开始预检' in IMPORT
    assert '上传不会直接写入' in IMPORT or '不会直接写入正式 Knowledge' in IMPORT
    assert '确认正式导入' in PREVIEW
    assert '/import/preview' in IMPORT
    assert '/import/confirm' in PREVIEW

def test_preview_has_file_identity_coverage_filters_and_business_table():
    for text in ['文件识别结果','字段覆盖情况','字段映射 Preview','总体覆盖率','未匹配','冲突','必填缺失']:
        assert text in PREVIEW
    assert 'data-filter="UNMATCHED"' in PREVIEW
    assert 'id="di-field-search"' in PREVIEW
    assert 'Excel 原字段' in PREVIEW and 'Knowledge 目标' in PREVIEW

def test_preview_prioritizes_business_language_not_raw_debug():
    assert 'Raw Only / 未映射' in PREVIEW
    assert 'raw_json' not in PREVIEW
    assert 'normalized_json' not in PREVIEW
    assert 'Mapping V{{result.mapping_config_version}}' in PREVIEW

def test_mapping_settings_exposes_versioned_workflow():
    for text in ['当前 Mapping','正式版本','Draft 修改','Validate','Activate','版本历史','基于此版本创建 Draft']:
        assert text in MAPPING
    assert 'ACTIVE 版本不可直接编辑' in MAPPING
    assert '来源与技术追溯' in MAPPING

def test_navigation_separates_intake_and_mapping_management():
    assert '>数据接入</a>' in BASE
    assert '>字段映射配置</a>' in BASE
    assert '?v=p3-mapping-difference-entry2' in BASE

def test_chrome_firefox_compatible_css_uses_standard_layouts():
    assert '.di-flow{' in CSS
    assert '.map-workflow{' in CSS
    assert 'position:sticky' in CSS
    assert '-webkit-appearance' not in CSS[-15000:]  # no Chromium-only component dependency in P2 block

def test_mapping_sticky_header_stays_inside_scroll_container_and_fields_are_labeled():
    assert '.map-ued-table th,.di-preview-table th{top:0!important' in CSS
    assert 'aria-label="Excel 源字段"' in MAPPING
    assert 'aria-label="业务别名 Alias"' in MAPPING
