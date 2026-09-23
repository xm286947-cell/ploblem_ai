from pathlib import Path
from quality_knowledge.web.statistics_presenter import present_statistics


def test_statistics_presenter_translates_user_facing_enums():
    view = present_statistics({
        'product_distribution': [{'category':'PLC','count':3}],
        'top_technical_gaps': [{'category':'TEST_CAPABILITY','count':2}],
        'top_management_gaps': [{'category':'CHANGE_MANAGEMENT','count':1}],
        'top_governance_gaps': [{'category':'CROSS_PRODUCT_GOVERNANCE','count':1}],
        'top_occurrence_causes': [], 'top_escape_causes': [],
        'cross_product_common_gaps': [{'category':'COMMON_TEST_ASSET','business_count':2,'businesses':'PLC,HMI','count':4}],
        'common_capability_analysis': [],
    })
    sections={s['key']:s for s in view['sections']}
    assert sections['top_technical_gaps']['rows'][0]['category']=='测试能力'
    assert sections['top_management_gaps']['rows'][0]['category']=='变更管理'
    assert sections['top_governance_gaps']['rows'][0]['category']=='跨产品治理'
    assert sections['cross_product_common_gaps']['rows'][0]['category']=='公共测试资产'


def test_statistics_template_is_chinese_and_structured():
    root=Path(__file__).parents[1]
    html=(root/'quality_knowledge/web/templates/statistics.html').read_text(encoding='utf-8')
    assert '质量问题统计分析' in html
    assert '问题分布与原因' in html
    assert '能力缺口分析' in html
    assert '跨产品共性能力' in html
    assert '<h1>Statistics</h1>' not in html
