from pathlib import Path

from storage_life import core


def test_family_view_exposes_document_analysis_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "coverage-ui.sqlite3")
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ("s", "x.pdf", "h", str(tmp_path / "x.pdf"), "", "", 2, "now"))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ("d", "Vendor", "Family", "NOR Flash", "s"))
        con.execute("""INSERT INTO extraction_runs
          (id,device_id,source_id,extraction_mode,schema_valid,model_calls,unresolved_evidence_json,review_required,review_queue_json,
           searched_pages_json,searched_sections_json,searched_fields_json,coverage_json,coverage_layers_json,document_analysis_json,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("r", "d", "s", "agent_single_pass_generic", 1, 1, "[]", 1, "[]", "[1,2]", "[]", "{}", "{}", "{}",
           '{"searched_pages":[1,2],"coverage":{"closed":false,"layers":{"critical":{"complete":false,"coverage_ratio":0.5,"counts":{"FOUND":1,"NOT_SPECIFIED":0,"NOT_APPLICABLE":0,"UNRESOLVED":1},"unresolved_fields":["retention"]}},"critical_unresolved":["retention"]},"supplement_rounds":1,"supplement_status":"completed"}', "now"))
    view = core.family_view("d")
    assert view["document_analysis"]["coverage"]["critical_unresolved"] == ["retention"]
    assert view["document_analysis"]["searched_pages"] == [1, 2]
    assert view["document_analysis"]["supplement_rounds"] == 1


def test_ui_renders_layered_coverage_and_supplement_status():
    html = Path("storage_life/index.html").read_text()
    assert "识别完整性（Coverage Gate）" in html
    assert "文档身份" in html and "关键规格" in html and "诊断能力" in html and "其他规格" in html
    assert "定向补查" in html
    assert "未闭环字段" in html
