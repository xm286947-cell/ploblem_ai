from pathlib import Path

HTML = Path(__file__).parents[1] / "storage_life" / "index.html"

def test_recognized_fields_are_sorted_before_missing_fields():
    text = HTML.read_text(encoding="utf-8")
    assert "const recognitionRank=" in text
    assert "if(!items.length)return 3" in text
    assert "verify_status==='confirmed'" in text
    assert "verify_status==='pending'" in text
    assert "fields=[...fields].sort((x,y)=>recognitionRank(x)-recognitionRank(y))" in text

def test_missing_row_is_still_rendered_not_hidden():
    text = HTML.read_text(encoding="utf-8")
    assert "未识别" in text
    assert "Agent 未找到有证据的候选值" in text
