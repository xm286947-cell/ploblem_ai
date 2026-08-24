from pathlib import Path
import sqlite3
import pytest
from quality_knowledge.human_analysis import HumanAnalysisRepository, HumanAnalysisService

def test_disabled_field_can_be_deleted_with_values_and_options(tmp_path):
    db = tmp_path / "k.db"
    svc = HumanAnalysisService(HumanAnalysisRepository(db))
    field = svc.create_field(
        field_key="risk",
        field_name="风险",
        field_type="SINGLE_SELECT",
        options=[{"value":"HIGH","label":"高"},{"value":"LOW","label":"低"}],
    )
    svc.save_analysis("K1","V1",{field["field_id"]:"HIGH"})
    with pytest.raises(ValueError):
        svc.delete_field(field["field_id"])
    svc.update_field(field["field_id"], enabled=False)
    result = svc.delete_field(field["field_id"])
    assert result["deleted"] is True
    assert svc.list_field_definitions() == []
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from human_analysis_field_option").fetchone()[0] == 0
        assert c.execute("select count(*) from human_analysis_value").fetchone()[0] == 0
        assert c.execute("select count(*) from human_analysis_audit").fetchone()[0] == 0
        assert c.execute("select count(*) from human_analysis").fetchone()[0] == 0

def test_issue_detail_is_compact_and_human_analysis_is_business_end_with_quick_anchor():
    p = Path("quality_knowledge/web/templates/issue_detail.html")
    text = p.read_text(encoding="utf-8")
    assert "top-workbench" in text
    assert "human-analysis-compact" in text
    assert "gap-summary-grid" in text
    assert "trace-compact-list" in text
    # UED V1.0: Human Analysis is after capability gaps, but before technical trace; top action/anchor keeps it easy to reach.
    assert text.index('id="gaps"') < text.index('id="human-analysis"') < text.index('id="trace"')
    assert 'href="#human-analysis"' in text
    assert text.index('id="human-analysis"') < text.index('id="trace"')
