from pathlib import Path
ROOT=Path(__file__).parents[1]
TPL=(ROOT/"quality_knowledge/web/templates/statistics.html").read_text(encoding="utf-8")
APP=(ROOT/"quality_knowledge/web/app.py").read_text(encoding="utf-8")
PRES=(ROOT/"quality_knowledge/web/statistics_presenter.py").read_text(encoding="utf-8")
DEP=(ROOT/"docs/UED_BACKEND_DEPENDENCY.md").read_text(encoding="utf-8")

def test_common_gap_is_ranking_not_wide_database_table():
    assert '治理优先级' in TPL and 'common-ranking-item' in TPL
    assert 'related_issues' not in TPL
    assert 'business_type_count' not in TPL
    assert 'business_types' not in TPL

def test_common_gap_kpi_and_filters():
    for text in ['共性能力缺口','关联问题','跨产品缺口','TOP 能力维度','全部能力维度','仅跨产品']:
        assert text in TPL
    assert "gap_dimension" in APP and "common_scope" in APP

def test_common_gap_scope_rule_and_bar():
    assert "bt_count >= 2" in PRES
    assert "'产品内共性'" in PRES and "'跨产品共性'" in PRES
    assert 'common-bar' in TPL and '个问题' in TPL

def test_no_fake_exact_drilldown():
    assert '下钻待后端支持' in TPL
    assert 'DEP-02' in DEP
