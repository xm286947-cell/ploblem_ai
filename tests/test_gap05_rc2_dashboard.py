from pathlib import Path

def test_statistics_dashboard_template_is_dense_and_linked():
    root=Path(__file__).parents[1]
    text=(root/'quality_knowledge/web/templates/statistics.html').read_text(encoding='utf-8')
    for token in ['总体概览','问题分布与原因分析','能力缺口分析','跨产品共性能力缺口','导出报表','查看详情','metric-grid','insight-causal-grid','product-context-strip']:
        assert token in text
    for token in ['dimension-occurrence','dimension-escape','dimension-recurrence','capability-dimension-card']:
        assert token in text
    css=(root/'quality_knowledge/web/static/app.css').read_text(encoding='utf-8')
    for token in ['capability-technical','capability-management','capability-governance']:
        assert token in css
    assert 'Raw JSON' not in text

def test_statistics_dashboard_css_has_dense_layout():
    root=Path(__file__).parents[1]
    css=(root/'quality_knowledge/web/static/app.css').read_text(encoding='utf-8')
    assert 'grid-template-columns:repeat(7' in css
    assert '.report-grid.four' in css
    assert 'max-width:1760px' in css
