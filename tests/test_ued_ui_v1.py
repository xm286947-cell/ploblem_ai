from pathlib import Path
ROOT=Path(__file__).parents[1]
T=ROOT/'quality_knowledge/web/templates'

def test_four_domain_shell():
    s=(T/'base.html').read_text(encoding='utf-8')
    for x in ['问题工作台','质量洞察','数据接入','系统配置']: assert x in s
    assert 'side-nav' in s

def test_issue_workspace_task_metrics():
    s=(T/'issues.html').read_text(encoding='utf-8')
    for x in ['待 AI 分析','分析失败','高再发风险','待人工分析']: assert x in s

def test_issue_workspace_reading_order():
    s=(T/'issue_detail.html').read_text(encoding='utf-8')
    ids=[s.index(f'id=\"{x}\"') for x in ['overview','causes','prevention','gaps','human-analysis','trace']]
    assert ids==sorted(ids)

def test_mapping_and_human_config_business_language():
    assert 'Excel 字段' in (T/'mapping_preview.html').read_text(encoding='utf-8')
    assert '填写预览' in (T/'human_analysis_settings.html').read_text(encoding='utf-8')
