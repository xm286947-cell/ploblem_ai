from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT=Path(__file__).parents[1]
TEMPLATES=ROOT/"quality_knowledge/web/templates"

def test_common_gap_template_does_not_resolve_dict_items_method():
    env=Environment(loader=FileSystemLoader(str(TEMPLATES)))
    env.filters.setdefault("urlencode", lambda x: x)
    tpl=env.get_template("statistics.html")
    # We only assert the source no longer uses the ambiguous dot access.
    src=(TEMPLATES/"statistics.html").read_text(encoding="utf-8")
    assert "cg.items" not in src
    assert "cg['items']" in src
    assert "cg['summary']" in src
