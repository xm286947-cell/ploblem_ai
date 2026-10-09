from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "quality_knowledge/web/static/hardware_case.js"
CSS = ROOT / "quality_knowledge/web/static/hardware_case.css"
DETAIL = ROOT / "quality_knowledge/web/templates/hardware_case_detail.html"
SEARCH = ROOT / "quality_knowledge/web/templates/hardware_case_search.html"


def test_evidence_drawer_hidden_state_is_authoritatively_hidden() -> None:
    css = CSS.read_text(encoding="utf-8").replace("\n", "")
    assert (
        ".hc-drawer-backdrop[hidden],.hc-evidence-drawer[hidden]{display:none!important}"
        in css
    )


def test_search_card_exposes_recall_tier_and_expansion_cost() -> None:
    js = JS.read_text(encoding="utf-8")
    assert "why.query_expansion" in js
    assert "expansion.expansion_cost" in js
    assert "召回：" in js
    assert "cost=" in js


def test_w2_ui_assets_are_cache_busted_on_search_and_detail() -> None:
    # W2 detail asset identity stays frozen; R2 search must bump the JS
    # version to prevent browsers from reusing a non-Agent cached script.
    detail = DETAIL.read_text(encoding="utf-8")
    search = SEARCH.read_text(encoding="utf-8")
    for html in (detail, search):
        assert "hardware_case.css?v=hc-w2-ui-smoke-v1" in html
    assert "hardware_case.js?v=hc-w2-ui-smoke-v1" in detail
    assert "hardware_case.js?v=hardware-r2-s1-v1" in search
