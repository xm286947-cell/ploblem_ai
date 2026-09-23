from pathlib import Path
ROOT=Path(__file__).parents[1]
TPL=(ROOT/"quality_knowledge/web/templates/statistics.html").read_text(encoding="utf-8")
CSS=(ROOT/"quality_knowledge/web/static/app.css").read_text(encoding="utf-8")
BASE=(ROOT/"quality_knowledge/web/templates/base.html").read_text(encoding="utf-8")

def test_r4_v2_hifi_core_sections_exist():
    for x in ["共性能力缺口","治理优先级","跨产品能力缺口","共性能力缺口","关联问题","跨产品缺口","TOP 能力维度"]:
        assert x in TPL
    assert "cg2-summary" in TPL
    assert "cg2-item" in TPL
    assert "cg2-bar" in TPL
    assert "cg2-table" in TPL

def test_ranking_matches_hifi_columns_and_text():
    assert "grid-template-columns:50px 220px minmax(260px,1fr) 130px" in CSS
    assert "涉及平台：" in TPL
    assert "个问题" in TPL
    assert "覆盖：" in TPL

def test_cross_product_section_only_uses_cross_items():
    assert "cg['cross_items']" in TPL
    assert "覆盖 ≥ 2 个产品" in TPL
    assert "当前没有覆盖 ≥ 2 个产品" in TPL

def test_static_css_cache_is_busted():
    assert "?v=ued-r4v2-cg-fix01" in BASE

def test_no_internal_database_fields_in_primary_template():
    for x in ["related_issues","business_type_count","business_types"]:
        assert x not in TPL
