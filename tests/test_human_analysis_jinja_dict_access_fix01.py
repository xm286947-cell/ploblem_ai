from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT=Path(__file__).parents[1]
TPL=(ROOT/'quality_knowledge/web/templates/issue_detail.html').read_text(encoding='utf-8')

def test_human_analysis_values_uses_explicit_dict_access():
    assert 'human_analysis.values' not in TPL
    assert "human_analysis['values']" in TPL
    assert "human_analysis['updated_at']" in TPL

def test_no_known_dict_method_collision_patterns():
    for bad in ['human_analysis.values','human_analysis.items','human_analysis.keys']:
        assert bad not in TPL
